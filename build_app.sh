#!/usr/bin/env bash
# Builds "iPhone Backup.app" (dist-app/) from backend/mac_app. Run on the Mac:  ./build_app.sh
# Uses its own venv (backend/.build-venv) so your working venv is untouched: the bundle needs a cryptography wheel
# with OpenSSL built in; the newest one links Homebrew's OpenSSL, which would not exist on another Mac.
set -euo pipefail
cd "$(dirname "$0")"
PY=${PYTHON:-python3.12}
V=backend/.build-venv
if [ ! -x "$V/bin/python" ]; then
  "$PY" -m venv "$V"
  grep -v -E '^(pywin32|pytest)' backend/requirements.txt | sed 's/^cryptography$/cryptography<45/' > "$V/requirements.txt"
  "$V/bin/pip" install -q --upgrade pip
  "$V/bin/pip" install -q -r "$V/requirements.txt" pywebview pyinstaller
  "$V/bin/pip" install -q --no-deps pymobiledevice3
fi
cd backend
rm -rf ../dist-app ../build-app
../"$V"/bin/pyinstaller --noconfirm --windowed --name "iPhone Backup" --osx-bundle-identifier com.iphonebackup.macapp \
  --distpath ../dist-app --workpath ../build-app --specpath ../build-app --paths . \
  --collect-all pymobiledevice3 --collect-all pillow_heif --add-data "$PWD/mac_agent/ui.html:mac_agent" \
  --collect-submodules app --collect-submodules mac_agent --collect-submodules uvicorn \
  --hidden-import webview.platforms.cocoa mac_app/launcher.py
echo
echo "Built: $(cd .. && pwd)/dist-app/iPhone Backup.app"
echo "Drag it to Applications. First launch on a Mac that did not build it: right-click > Open (unless signed + notarized)."
