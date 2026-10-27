<#
  Starts iPhone Backup.

  .\start.ps1            local only (http://127.0.0.1:8765)
  .\start.ps1 -Lan       also reachable from your iPhone on the same Wi-Fi (needs the pairing token)
  .\start.ps1 -Build     rebuild the web UI first
  .\start.ps1 -Hidden    no console window (used by the auto-start task)
#>
param([switch]$Lan, [switch]$Build, [switch]$Hidden)

$root = $PSScriptRoot
$py = Join-Path $root "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "Python environment missing. See docs/README.md (Setup)." }

if ($Build -or -not (Test-Path (Join-Path $root "frontend\dist"))) {
    Push-Location (Join-Path $root "frontend")
    npm install --silent
    npm run build
    Pop-Location
}

$env:SETTINGS_FILE = Join-Path $root "config\settings.json"   # same file Docker uses, so one pairing token
$bindHost = if ($Lan) { "0.0.0.0" } else { "127.0.0.1" }
$uvicornArgs = @("-m", "uvicorn", "app.main:app", "--host", $bindHost, "--port", "8765")

if ($Hidden) {
    Start-Process -FilePath $py -ArgumentList $uvicornArgs -WorkingDirectory (Join-Path $root "backend") -WindowStyle Hidden
} else {
    Push-Location (Join-Path $root "backend")
    & $py @uvicornArgs
    Pop-Location
}
