"""Small JSON settings file kept in the library root (auto-import flag, pairing token)."""
import json
import secrets

from .config import SETTINGS_FILE

DEFAULTS = {"auto_import": False, "token": ""}


def _path():
    return SETTINGS_FILE


def load() -> dict:
    data = dict(DEFAULTS)
    try:
        data.update(json.loads(_path().read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    if not data["token"]:
        data["token"] = secrets.token_urlsafe(16)
        save(data)
    return data


def save(data: dict) -> None:
    _path().parent.mkdir(parents=True, exist_ok=True)
    _path().write_text(json.dumps(data, indent=2), encoding="utf-8")


def update(**changes) -> dict:
    data = load()
    data.update(changes)
    save(data)
    return data
