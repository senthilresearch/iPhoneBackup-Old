"""Cached listing of what is on the phone, with filtering and selection."""
import asyncio
import json
from dataclasses import asdict
from datetime import date, datetime

from pydantic import BaseModel

from . import device
from .config import DATA_ROOT
from .device import DeviceFile
from .library import KINDS, connect

SIDECAR = ".aae"


class Filter(BaseModel):
    kinds: list[str] = ["photos", "videos"]
    date_from: date | None = None  # inclusive, phone's local calendar date
    date_to: date | None = None
    hide_imported: bool = False


class Selection(BaseModel):
    paths: list[str] | None = None  # explicit picks; otherwise the filter decides
    filter: Filter = Filter(hide_imported=True)
    exclude: list[str] = []


class Catalog:
    def __init__(self):
        self.files: list[DeviceFile] = []
        self.device_id: str | None = None
        self.scanned_at: str | None = None
        self.stale = False  # loaded from disk cache, not rescanned yet
        self.scanning = False
        self.error = ""
        self._lock = asyncio.Lock()

    def meta(self) -> dict:
        return {
            "scanning": self.scanning,
            "scanned_at": self.scanned_at,
            "count": sum(1 for f in self.files if f.ext != SIDECAR),
            "stale": self.stale,
            "error": self.error,
        }

    # -- persistence so a restart doesn't need a 30s rescan to show something
    def _cache_file(self, device_id: str):
        return DATA_ROOT / ".cache" / f"catalog_{device_id}.json"

    def _save(self):
        if not self.device_id:
            return
        f = self._cache_file(self.device_id)
        f.parent.mkdir(exist_ok=True)
        rows = [{**asdict(x), "mtime": x.mtime.isoformat()} for x in self.files]
        f.write_text(json.dumps({"scanned_at": self.scanned_at, "files": rows}), encoding="utf-8")

    def load_cached(self, device_id: str) -> bool:
        try:
            data = json.loads(self._cache_file(device_id).read_text(encoding="utf-8"))
            self.files = [DeviceFile(**{**r, "mtime": datetime.fromisoformat(r["mtime"])}) for r in data["files"]]
        except (OSError, ValueError, KeyError, TypeError):
            return False
        self.device_id, self.scanned_at, self.stale = device_id, data["scanned_at"], True
        return True

    def invalidate(self):
        self.files, self.device_id, self.scanned_at, self.stale = [], None, None, False

    async def refresh(self) -> None:
        async with self._lock:
            self.scanning, self.error = True, ""
            try:
                async with device.open_afc() as (device_id, afc):
                    files = await device.scan(afc)
                self.files, self.device_id, self.stale = files, device_id, False
                self.scanned_at = datetime.now().astimezone().isoformat(timespec="seconds")
                self._save()
            except Exception as e:  # noqa: BLE001
                self.error = f"{type(e).__name__}: {e}"
            finally:
                self.scanning = False

    async def ensure(self, device_id: str | None = None) -> None:
        """Make sure a fresh (non-cached) scan exists."""
        if not self.files or self.stale or (device_id and self.device_id != device_id):
            await self.refresh()
            if self.error:
                raise RuntimeError(self.error)

    # -- querying
    def imported_paths(self) -> dict[str, set[str]]:
        out = {}
        for kind in KINDS:
            with connect(kind) as conn:
                rows = conn.execute("SELECT device_path FROM items WHERE device_id=?", (self.device_id,))
                out[kind] = {r[0] for r in rows}
        return out

    def select(self, flt: Filter) -> list[DeviceFile]:
        known = self.imported_paths() if flt.hide_imported else {}
        out = []
        for f in self.files:
            if f.ext == SIDECAR or f.kind not in flt.kinds:
                continue
            day = f.mtime.astimezone().date()
            if flt.date_from and day < flt.date_from:
                continue
            if flt.date_to and day > flt.date_to:
                continue
            if flt.hide_imported and f.path in known.get(f.kind, ()):
                continue
            out.append(f)
        out.sort(key=lambda f: f.mtime, reverse=True)
        return out

    def resolve(self, sel: Selection) -> list[DeviceFile]:
        """Concrete files for a selection, including .AAE edit sidecars of chosen photos."""
        if sel.paths is not None:
            wanted = set(sel.paths)
            chosen = [f for f in self.files if f.path in wanted and f.ext != SIDECAR]
        else:
            chosen = self.select(sel.filter)
        skip = set(sel.exclude)
        chosen = [f for f in chosen if f.path not in skip]
        stems = {f.path.rsplit(".", 1)[0] for f in chosen if f.kind == "photos"}
        sidecars = [f for f in self.files if f.ext == SIDECAR and f.path.rsplit(".", 1)[0] in stems]
        return chosen + sidecars


catalog = Catalog()
