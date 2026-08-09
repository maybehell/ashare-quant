param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$Branch = "main"
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $RepoRoot

$logDir = Join-Path $RepoRoot "Logger"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$logPath = Join-Path $logDir "github_sync.log"
$lockPath = Join-Path $RepoRoot ".git\ashare_quant_sync.lock"
$lockStream = $null

function Write-SyncLog([string]$Message) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -LiteralPath $logPath -Value $line -Encoding UTF8
    Write-Output $line
}

function Invoke-Git([string[]]$Arguments) {
    & git @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
    }
}

try {
    if (-not (Test-Path -LiteralPath ".git" -PathType Container)) {
        throw "not a Git repository: $RepoRoot"
    }

    try {
        $lockStream = [System.IO.File]::Open(
            $lockPath,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None
        )
    }
    catch [System.IO.IOException] {
        throw "another Git sync is already running: $lockPath"
    }

    Write-SyncLog "sync started"

    $currentBranch = (& git branch --show-current).Trim()
    if ($LASTEXITCODE -ne 0 -or $currentBranch -ne $Branch) {
        throw "expected branch '$Branch', current branch is '$currentBranch'"
    }

    & git diff --cached --quiet
    if ($LASTEXITCODE -eq 1) {
        throw "pre-existing staged changes detected; refusing to mix operator work with scheduled sync"
    }
    if ($LASTEXITCODE -ne 0) {
        throw "unable to inspect staged changes"
    }

    Invoke-Git @("fetch", "origin", $Branch)
    $counts = (& git rev-list --left-right --count "HEAD...origin/$Branch").Trim() -split "\s+"
    if ($LASTEXITCODE -ne 0 -or $counts.Count -ne 2) {
        throw "unable to compare local and remote branch"
    }
    $localOnly = [int]$counts[0]
    $remoteOnly = [int]$counts[1]
    if ($remoteOnly -gt 0) {
        throw "remote branch is ahead/diverged ($remoteOnly remote-only commits); manual reconciliation required"
    }

    $sourcePaths = @(
        ".gitignore",
        "AGENTS.md",
        "README.md",
        "pyproject.toml",
        "requirements.txt",
        "requirements-minimal.txt",
        "requirements-rpa.txt",
        "launch_gui.bat",
        "run_local.cmd",
        "ashare_quant",
        "configs",
        "docs",
        "scripts",
        "tests"
    ) | Where-Object { Test-Path -LiteralPath $_ }

    Invoke-Git (@("add", "--") + $sourcePaths)
    & .\.venv\Scripts\python.exe scripts\git_sync_preflight.py --repo $RepoRoot --mode staged
    if ($LASTEXITCODE -ne 0) {
        throw "sensitive-file preflight failed; staged files were left visible for manual review"
    }

    & git diff --cached --quiet
    if ($LASTEXITCODE -eq 0) {
        Write-SyncLog "no source changes to sync"
        exit 0
    }
    if ($LASTEXITCODE -ne 1) {
        throw "unable to inspect staged source changes"
    }

    $message = "chore(sync): {0}" -f (Get-Date -Format "yyyy-MM-dd HH:mm")
    Invoke-Git @("commit", "-m", $message)
    Invoke-Git @("push", "origin", $Branch)
    Write-SyncLog "sync completed: $message"
}
catch {
    Write-SyncLog "sync FAILED: $($_.Exception.Message)"
    exit 1
}
finally {
    if ($null -ne $lockStream) {
        $lockStream.Dispose()
        Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
    }
}
