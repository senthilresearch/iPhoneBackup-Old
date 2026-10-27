"""Mac companion agent: reads an iPhone over USB and uploads it to the backend's /api/upload."""
import os
from pathlib import Path

# The agent shares app.thumbs with the backend; keep its thumbnail cache out of ~/iPhoneLibrary (the backend default)
# and in the per-user cache folder. Must run before app.config is imported.
os.environ.setdefault("IPHONE_DATA", str(Path.home() / "Library" / "Caches" / "iphone-mac-agent"))
