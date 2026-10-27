# iPhoneBackup local app (moved out of the Home Attic repository)

The original "copy photos from an iPhone to a PC" app, kept whole after the main project (`iPhoneBackup`, to become **Home Attic**) went to the
cloud relay + home agent + operator console design. Nothing here is needed to run that system.

What is in here
- `backend/app`: FastAPI backend that reads an iPhone over USB or Wi-Fi sync (`device.py`, `catalog.py`, `importer.py`, `watcher.py`), the chunked upload receiver (`uploads.py`), thumbnails and the library catalogs. Its filing code (`library.py`, `store_file`) is also copied into the main project's `backend/home_agent`, so the library layout and catalog format must stay compatible.
- `frontend`: the React/Vite web UI that browses and imports the library (`npm run dev`, or the nginx container on :8081).
- `backend/mac_agent`, `backend/mac_app`: the Mac CLI/menu-bar app that uploads a USB iPhone to the backend; `frontend/public/upload.html` is the Safari upload page.
- `start.ps1`, `start.sh`, `build_app.sh`, `deployment/` (compose file, Dockerfiles, Caddy and nginx configs, mutual TLS script, the auto-redeploy scheduled task).
- `docs/runbooks/REMOTE_ACCESS.md`, `docs/specs/BROWSE_CONTRACT.md`, `docs/archive/BROWSE_LIBRARY.md`.

How it was split: the main repository was tagged `pre-split-local-app` right before these files were removed from it; the full history of this code is
in that repository (`git log pre-split-local-app -- backend/app`). This repository starts with that code as its first commit.

Run it: see `CLAUDE.md` (the commands section is still accurate for this code); tests: `cd backend; python -m pytest` (needs the dependencies of
`backend/requirements.txt`, Python 3.13).

Known stale parts: `CLAUDE.md` and the guides still describe the relay, admin console and iPhone app that now live in the main repository.
