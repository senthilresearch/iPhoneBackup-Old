#!/usr/bin/env bash
# Starts iPhone Backup on macOS/Linux (the PowerShell start.ps1 is the Windows equivalent).
#   ./start.sh          local only: http://127.0.0.1:8765
#   ./start.sh --lan    also reachable from the iPhone on your Wi-Fi (needs the pairing token)
#   ./start.sh --build  rebuild the web UI first
set -euo pipefail
root="$(cd "$(dirname "$0")" && pwd)"
py="$root/backend/.venv/bin/python"
[ -x "$py" ] || { echo "Python environment missing. See docs/README.md (Setup)." >&2; exit 1; }

host=127.0.0.1; build=0
for a in "$@"; do
  case "$a" in
    --lan) host=0.0.0.0 ;;
    --build) build=1 ;;
    *) echo "Unknown option: $a" >&2; exit 2 ;;
  esac
done

if [ "$build" = 1 ] || [ ! -d "$root/frontend/dist" ]; then
  (cd "$root/frontend" && npm install --silent && npm run build)
fi

export SETTINGS_FILE="$root/config/settings.json"   # same file Docker uses, so one pairing token
cd "$root/backend"
exec "$py" -m uvicorn app.main:app --host "$host" --port 8765
