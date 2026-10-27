"""Start-at-login through a per-user LaunchAgent (no admin rights needed)."""
import plistlib
import sys
from pathlib import Path

LABEL = "com.iphonebackup.macapp"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def program() -> list[str]:
    """The command that starts this app: the bundle's executable, or `python -m mac_app` when run from source."""
    exe = Path(sys.executable)
    if ".app/Contents/MacOS" in str(exe):
        return [str(exe)]
    return [str(exe), "-m", "mac_app"]


def enabled() -> bool:
    return PLIST.exists()


def set_enabled(on: bool) -> None:
    if on:
        PLIST.parent.mkdir(parents=True, exist_ok=True)
        PLIST.write_bytes(plistlib.dumps({"Label": LABEL, "ProgramArguments": program(), "RunAtLoad": True}))
    else:
        PLIST.unlink(missing_ok=True)
