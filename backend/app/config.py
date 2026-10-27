import os
import sys
from pathlib import Path

# Library folder: IPHONE_LIBRARY, else the original Windows location, else ~/iPhoneLibrary (macOS/Linux).
_DEFAULT_LIBRARY = Path(r"C:\Senthil Krishna\iPhoneLibrary") if sys.platform == "win32" else Path.home() / "iPhoneLibrary"
LIBRARY_ROOT = Path(os.environ.get("IPHONE_LIBRARY") or _DEFAULT_LIBRARY).resolve()  # resolved: macOS temp dirs sit behind /var -> /private/var
# Metadata (SQLite catalogs) and derived caches (thumbnails, phone catalog cache) live in IPHONE_DATA, default: the library
# folder. In Docker the media files sit in a Windows folder (IPHONE_LIBRARY) while this stays in a Docker volume.
DATA_ROOT = Path(os.environ["IPHONE_DATA"]).resolve() if os.environ.get("IPHONE_DATA") else LIBRARY_ROOT
# Settings file (auto-import flag, pairing token). Defaults to <library>/settings.json; set SETTINGS_FILE to keep it
# elsewhere, e.g. the gitignored config/settings.json in the workspace shared by Docker and the native launchers.
SETTINGS_FILE = Path(os.environ["SETTINGS_FILE"]) if os.environ.get("SETTINGS_FILE") else LIBRARY_ROOT / "settings.json"
DCIM = "/DCIM"
# Other top-level folders on the phone's media partition that hold plain files (best effort;
# missing folders are ignored).
FILE_ROOTS = ["/Downloads", "/Books", "/Recordings"]
CHUNK = 1024 * 1024
PORT = 8765
# Docker only: the PC never looks like loopback behind the port mapping, so let a valid token use the PC-only endpoints.
TOKEN_IS_ADMIN = os.environ.get("TOKEN_IS_ADMIN", "") == "1"
PUBLIC_URL = os.environ.get("PUBLIC_URL", "")  # address phones should use, e.g. http://192.168.1.20:8081 (Docker)

PHOTO_EXT = {".heic", ".jpg", ".jpeg", ".png", ".gif", ".dng", ".tif", ".tiff", ".webp",
             ".heif", ".heics", ".bmp", ".avif", ".jp2", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".orf", ".rw2", ".srw"}  # the later ones: other HEIF/image types and camera RAW

VIDEO_EXT = {".mov", ".mp4", ".m4v", ".3gp"}
SIDECAR_EXT = {".aae"}  # iOS edit data; stored beside photos
