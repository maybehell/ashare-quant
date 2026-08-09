param(
    [string]$At = "22:30",
    [switch]$StartNow
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$daemonScript = Join-Path $PSScriptRoot "github_sync_daemon.ps1"
$startupDir = [Environment]::GetFolderPath("Startup")
$startupPath = Join-Path $startupDir "AShareQuantGitHubSync.cmd"

if (-not (Test-Path -LiteralPath $daemonScript -PathType Leaf)) {
    throw "daemon script not found: $daemonScript"
}

$command = @"
@echo off
start "" /min powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "$daemonScript" -RepoRoot "$repoRoot" -At "$At"
"@
[System.IO.File]::WriteAllText($startupPath, $command, [System.Text.Encoding]::ASCII)

if ($StartNow) {
    Start-Process `
        -FilePath "powershell.exe" `
        -ArgumentList @(
            "-NoProfile",
            "-WindowStyle", "Hidden",
            "-ExecutionPolicy", "Bypass",
            "-File", $daemonScript,
            "-RepoRoot", $repoRoot,
            "-At", $At
        ) `
        -WindowStyle Hidden
}

Write-Output "Startup sync installed: $startupPath (daily $At)"

