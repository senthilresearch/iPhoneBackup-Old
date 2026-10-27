<#
  Registers a Windows Scheduled Task so iPhone Backup starts (hidden) whenever you sign in.
  After that, plugging in the iPhone is all that is needed.

  .\install-autostart.ps1          install (local only)
  .\install-autostart.ps1 -Lan     install, also reachable from the iPhone over Wi-Fi
  .\install-autostart.ps1 -Remove  uninstall
#>
param([switch]$Lan, [switch]$Remove)

$name = "iPhoneBackup"
if ($Remove) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed scheduled task '$name'."
    return
}

$script = Join-Path (Split-Path $PSScriptRoot -Parent) "start.ps1"
$extra = if ($Lan) { " -Lan" } else { "" }
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`" -Hidden$extra"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "Installed. iPhone Backup will start at sign-in. Open http://127.0.0.1:8765"
