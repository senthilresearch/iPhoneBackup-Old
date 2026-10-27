<#
  Registers a Windows Scheduled Task that runs deploy-watch.ps1 (hidden) at sign-in, so pushed commits are
  pulled and redeployed into Docker automatically. Docker Desktop must also start at sign-in.

  .\install-deploy-watch.ps1                 install (checks every 60 s)
  .\install-deploy-watch.ps1 -Interval 300   check every 5 minutes
  .\install-deploy-watch.ps1 -Remove         uninstall
#>
param([int]$Interval = 60, [switch]$Remove)

$name = "iPhoneBackupDeployWatch"
if ($Remove) {
    Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed scheduled task '$name'."
    return
}

$script = Join-Path $PSScriptRoot "deploy-watch.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`" -DeployNow -Interval $Interval"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $name
Write-Host "Installed and started. Watching origin every $Interval s. Log: $(Join-Path $PSScriptRoot 'deploy-watch.log')"
