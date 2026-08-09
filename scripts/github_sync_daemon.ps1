param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$At = "22:30",
    [switch]$RunOnce
)

$ErrorActionPreference = "Stop"
$time = [DateTime]::ParseExact($At, "HH:mm", [Globalization.CultureInfo]::InvariantCulture)
$syncScript = Join-Path $PSScriptRoot "sync_github.ps1"
$logDir = Join-Path $RepoRoot "Logger"
$statusPath = Join-Path $logDir "github_sync_daemon_status.json"
$lockPath = Join-Path $logDir "github_sync_daemon.lock"
$lockStream = $null
$lastRunAt = $null
$lastExitCode = $null

New-Item -ItemType Directory -Path $logDir -Force | Out-Null

if (Test-Path -LiteralPath $statusPath -PathType Leaf) {
    try {
        $previousStatus = Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($previousStatus.last_run_at) {
            $lastRunAt = [DateTime]::Parse(
                [string]$previousStatus.last_run_at,
                [Globalization.CultureInfo]::InvariantCulture,
                [Globalization.DateTimeStyles]::RoundtripKind
            )
        }
        if ($null -ne $previousStatus.last_exit_code) {
            $lastExitCode = [int]$previousStatus.last_exit_code
        }
    }
    catch {
        $lastRunAt = $null
        $lastExitCode = $null
    }
}
function Write-DaemonStatus(
    [string]$State,
    [string]$Message,
    $ExitCode,
    $LastRunAt,
    $NextRunAt
) {
    $payload = [ordered]@{
        schema_version = 1
        state = $State
        message = $Message
        pid = $PID
        schedule = $At
        updated_at = (Get-Date).ToString("o")
        last_run_at = if ($null -ne $LastRunAt) { ([DateTime]$LastRunAt).ToString("o") } else { $null }
        last_exit_code = if ($null -ne $ExitCode) { [int]$ExitCode } else { $null }
        next_run_at = if ($null -ne $NextRunAt) { ([DateTime]$NextRunAt).ToString("o") } else { $null }
    }
    $tempPath = "$statusPath.tmp"
    $payload | ConvertTo-Json | Set-Content -LiteralPath $tempPath -Encoding UTF8
    Move-Item -LiteralPath $tempPath -Destination $statusPath -Force
}

function Invoke-SourceSync {
    $startedAt = Get-Date
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $syncScript -RepoRoot $RepoRoot | Out-Host
    $exitCode = $LASTEXITCODE
    $script:lastRunAt = $startedAt
    $script:lastExitCode = $exitCode
    Write-DaemonStatus `
        -State $(if ($exitCode -eq 0) { "completed" } else { "failed" }) `
        -Message $(if ($exitCode -eq 0) { "source sync completed" } else { "source sync failed" }) `
        -ExitCode $exitCode `
        -LastRunAt $startedAt `
        -NextRunAt $null
    return $exitCode
}

try {
    try {
        $lockStream = [System.IO.File]::Open(
            $lockPath,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::None
        )
    }
    catch [System.IO.IOException] {
        try {
            $staleStream = [System.IO.File]::Open(
                $lockPath,
                [System.IO.FileMode]::Open,
                [System.IO.FileAccess]::ReadWrite,
                [System.IO.FileShare]::None
            )
            $staleStream.Dispose()
            Remove-Item -LiteralPath $lockPath -Force
            $lockStream = [System.IO.File]::Open(
                $lockPath,
                [System.IO.FileMode]::CreateNew,
                [System.IO.FileAccess]::Write,
                [System.IO.FileShare]::None
            )
        }
        catch [System.IO.IOException] {
            throw "another GitHub sync daemon is already running: $lockPath"
        }
    }

    $pidBytes = [System.Text.Encoding]::ASCII.GetBytes([string]$PID)
    $lockStream.Write($pidBytes, 0, $pidBytes.Length)
    $lockStream.Flush()

    if ($RunOnce) {
        exit (Invoke-SourceSync)
    }

    $lastRunDate = if ($null -ne $lastRunAt) { ([DateTime]$lastRunAt).Date } else { $null }
    while ($true) {
        $now = Get-Date
        $todayTarget = $now.Date.Add($time.TimeOfDay)
        if ($now -ge $todayTarget -and $lastRunDate -ne $now.Date) {
            [void](Invoke-SourceSync)
            $lastRunDate = $now.Date
        }

        $now = Get-Date
        $nextTarget = $now.Date.Add($time.TimeOfDay)
        if ($now -ge $nextTarget) {
            $nextTarget = $nextTarget.AddDays(1)
        }
        Write-DaemonStatus `
            -State "waiting" `
            -Message "waiting for next source sync" `
            -ExitCode $lastExitCode `
            -LastRunAt $lastRunAt `
            -NextRunAt $nextTarget

        $sleepSeconds = [Math]::Min([Math]::Max([int]($nextTarget - $now).TotalSeconds, 1), 300)
        Start-Sleep -Seconds $sleepSeconds
    }
}
catch {
    Write-DaemonStatus -State "stopped" -Message $_.Exception.Message -ExitCode 1 -LastRunAt $null -NextRunAt $null
    exit 1
}
finally {
    if ($null -ne $lockStream) {
        $lockStream.Dispose()
        Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
    }
}

