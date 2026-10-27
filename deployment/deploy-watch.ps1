<#
  Watches the git remote and redeploys the Docker containers when the branch has new commits.

  .\deploy-watch.ps1                  poll every 60 s, runs until stopped
  .\deploy-watch.ps1 -Once            check once (exit code 0 = ok), then stop
  .\deploy-watch.ps1 -DeployNow       build and start the containers first, then keep watching
  .\deploy-watch.ps1 -NoDeploy        only sync the repo, skip docker (for testing)

  Safe by design: it only fast-forwards (never merges, resets or discards anything), skips the update while there are
  uncommitted changes to tracked files or local commits that are not pushed, and never touches the library volume.
  A failed build leaves the running containers as they are and is retried only when a newer commit arrives.
  Every poll is logged (including "nothing new"). Log: deploy-watch.log (next to this script, rotated at 1 MB).
#>
param(
    [int]$Interval = 60,
    [string]$Branch = "main",
    [switch]$Once,
    [switch]$DeployNow,
    [switch]$NoDeploy
)

$ErrorActionPreference = "Continue"
$root = Split-Path $PSScriptRoot -Parent   # repo root (git work tree, .env)
$logFile = Join-Path $PSScriptRoot "deploy-watch.log"
Set-Location $root

function Log($msg) {
    $line = "{0:yyyy-MM-dd HH:mm:ss}  {1}" -f (Get-Date), $msg
    Write-Host $line
    try {
        if ((Test-Path $logFile) -and (Get-Item $logFile).Length -gt 1MB) { Move-Item $logFile "$logFile.old" -Force -ErrorAction Stop }
    } catch { }
    # Open with shared access and retry, so a reader (tail -f, Get-Content -Wait, an editor) cannot break logging.
    for ($i = 0; $i -lt 5; $i++) {
        try {
            $fs = [System.IO.File]::Open($logFile, "Append", "Write", "ReadWrite")
            try {
                $sw = New-Object System.IO.StreamWriter($fs, (New-Object System.Text.UTF8Encoding($false)))
                $sw.WriteLine($line)
                $sw.Flush()
            } finally { $fs.Dispose() }
            return
        } catch { Start-Sleep -Milliseconds 150 }
    }
}

# Compose files live in deployment/; the PC's .env stays in the repo root, so pass it explicitly.
$compose = @("compose", "-f", "deployment/docker-compose.yml")
if (Test-Path (Join-Path $root ".env")) { $compose += @("--env-file", ".env") }

function Invoke-Git { & git.exe @args 2>&1 }

# `docker compose up` renames the old container to "<12 hex>_name" while it recreates it. If a run is killed in that
# window (watcher stopped, PC sleeps, Docker restarts), the renamed leftover blocks the next deploy with a name
# conflict. Remove leftovers of this compose project (only those with that prefix) before and after a failure.
function Clear-StaleContainers {
    $project = $null
    try { $project = (& docker @compose config --format json 2>$null | Out-String | ConvertFrom-Json).name } catch { }
    if (-not $project) { return }
    $rows = & docker ps -a --filter "label=com.docker.compose.project=$project" --format "{{.ID}} {{.Names}}" 2>$null
    foreach ($row in $rows) {
        $id, $cname = "$row".Trim() -split "\s+", 2
        if ($cname -match "^[0-9a-f]{12}_") {
            Log "Removing leftover container from an interrupted deploy: $cname"
            & docker rm -f $id 2>&1 | Out-Null
        }
    }
}

function Deploy {
    if ($NoDeploy) { Log "NoDeploy: skipping docker"; return $true }
    for ($attempt = 1; $attempt -le 2; $attempt++) {
        Clear-StaleContainers
        Log "Running: docker compose up -d --build (attempt $attempt of 2)"
        $out = & docker @compose up -d --build --remove-orphans 2>&1
        if ($LASTEXITCODE -eq 0) { Log "Deploy OK"; return $true }
        Log ("Deploy attempt $attempt FAILED:`n" + ($out | ForEach-Object { "$_" } | Select-Object -Last 12 | Out-String))
    }
    Log "Deploy FAILED after 2 attempts; running containers left as they are."
    return $false
}

# One watcher at a time.
$mutex = New-Object System.Threading.Mutex($false, "Global\iPhoneBackupDeployWatch")
if (-not $mutex.WaitOne(0)) { Write-Host "Another deploy-watch is already running."; exit 1 }

$failedSha = $null
if ($DeployNow -and -not (Deploy)) { $failedSha = (Invoke-Git rev-parse HEAD) }

try {
    do {
        $ok = $true
        $null = Invoke-Git fetch --quiet origin $Branch
        if ($LASTEXITCODE -ne 0) {
            Log "git fetch failed (offline?); will retry"
            $ok = $false
        } else {
            $head = (Invoke-Git rev-parse HEAD | Select-Object -First 1)
            $remote = (Invoke-Git rev-parse "origin/$Branch" | Select-Object -First 1)
            if ($head -eq $remote) {
                Log "Checked origin/${Branch}: nothing new ($($head.Substring(0,7)))"
            } else {
                $behind = [int](Invoke-Git rev-list --count "HEAD..origin/$Branch")
                $ahead = [int](Invoke-Git rev-list --count "origin/$Branch..HEAD")
                $current = (Invoke-Git rev-parse --abbrev-ref HEAD | Select-Object -First 1)
                $dirty = (Invoke-Git status --porcelain --untracked-files=no)
                if ($current -ne $Branch) { Log "On branch '$current', not '$Branch': not updating." }
                elseif ($ahead -gt 0) { Log "Local has $ahead unpushed commit(s); not updating." }
                elseif ($dirty) { Log "Uncommitted changes to tracked files; not updating." }
                elseif ($remote -eq $failedSha) { Log "origin/$Branch $($remote.Substring(0,7)) failed to deploy earlier; waiting for a newer commit." }
                elseif ($behind -gt 0) {
                    Log "$behind new commit(s) on origin/${Branch}: $($head.Substring(0,7)) -> $($remote.Substring(0,7))"
                    Log "Step 1/2: checking out origin/$Branch (fast-forward)"
                    $mergeOut = Invoke-Git merge --ff-only "origin/$Branch"
                    $mergeExit = $LASTEXITCODE
                    $landed = (Invoke-Git rev-parse HEAD | Select-Object -First 1)
                    if ($mergeExit -ne 0 -or $landed -ne $remote) {
                        Log ("Checkout FAILED, not deploying:`n" + ($mergeOut | Out-String))
                        $ok = $false
                    } else {
                        $subject = (Invoke-Git log -1 --format=%s | Select-Object -First 1)
                        $stat = (Invoke-Git diff --shortstat $head $remote | Select-Object -First 1)
                        Log "Checked out $($remote.Substring(0,7)): $subject ($("$stat".Trim()))"
                        Log "Step 2/2: deploying"
                        if (Deploy) { $failedSha = $null } else { $failedSha = $remote; $ok = $false }
                    }
                }
            }
        }
        if ($Once) { if ($ok) { exit 0 } else { exit 1 } }
        Start-Sleep -Seconds $Interval
    } while ($true)
} finally {
    $mutex.ReleaseMutex()
}
