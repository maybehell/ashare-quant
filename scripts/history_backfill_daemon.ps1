param(
    [string]$Config = "configs/extended_history_validation.yaml",
    [string]$OutputDir = "outputs_extended_history_validation",
    [int]$MaxSymbols = 800,
    [int]$IntervalMinutes = 60,
    [int]$MaxRuntimeMinutes = 30,
    [int]$MaxValidationRuntimeMinutes = 120,
    [int]$SourceFailureBackoffMinutes = 240,
    [string]$QuietStart = "17:30",
    [string]$QuietEnd = "21:30",
    [switch]$RunOnce,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$aqScript = Join-Path $PSScriptRoot "aq.ps1"
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

function Resolve-ProjectPath([string]$Value) {
    if ([IO.Path]::IsPathRooted($Value)) { return [IO.Path]::GetFullPath($Value) }
    return [IO.Path]::GetFullPath((Join-Path $projectRoot $Value))
}
if ($IntervalMinutes -lt 15) { throw "IntervalMinutes must be at least 15." }
if ($MaxRuntimeMinutes -lt 5) { throw "MaxRuntimeMinutes must be at least 5." }
if ($MaxValidationRuntimeMinutes -lt 10) { throw "MaxValidationRuntimeMinutes must be at least 10." }
if ($SourceFailureBackoffMinutes -lt $IntervalMinutes) { throw "SourceFailureBackoffMinutes must be at least IntervalMinutes." }
if ($MaxSymbols -lt 1) { throw "MaxSymbols must be positive." }
if (-not (Test-Path -LiteralPath $aqScript)) { throw "Missing aq runner: $aqScript" }
if (-not (Test-Path -LiteralPath $python)) { throw "Missing project Python: $python" }
$quietStartTime = [datetime]::ParseExact($QuietStart, "HH:mm", [Globalization.CultureInfo]::InvariantCulture).TimeOfDay
$quietEndTime = [datetime]::ParseExact($QuietEnd, "HH:mm", [Globalization.CultureInfo]::InvariantCulture).TimeOfDay
$resolvedConfig = Resolve-ProjectPath $Config
$resolvedOutput = Resolve-ProjectPath $OutputDir
if (-not (Test-Path -LiteralPath $resolvedConfig)) { throw "Missing config: $resolvedConfig" }
New-Item -ItemType Directory -Path $resolvedOutput -Force | Out-Null

$progressPath = Join-Path $projectRoot "data\cache\history_backfill_progress_extended_history_validation.parquet"
$statusPath = Join-Path $resolvedOutput "history_backfill_daemon_status.json"
$lockPath = Join-Path $resolvedOutput "history_backfill_daemon.lock"
$stopPath = Join-Path $resolvedOutput "history_backfill_daemon.stop"
$logPath = Join-Path $resolvedOutput "history_backfill_daemon.log"
$observationLock = Join-Path $projectRoot "outputs\daily_observation.lock.json"
$lockStream = $null
$sourceFailureStreak = 0
$sourceBackoffMinutes = 0
$sourceResumeAt = $null
$script:lastBatchSourceUnavailable = $false
if (Test-Path -LiteralPath $statusPath) {
    try {
        $previousStatus = Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $previousNextRun = [string]$previousStatus.next_run_at
        if (
            [string]$previousStatus.state -eq "source_unavailable" -and
            $previousNextRun -and
            [DateTimeOffset]::Parse($previousNextRun).LocalDateTime -gt (Get-Date)
        ) {
            $sourceFailureStreak = [Math]::Max([int]$previousStatus.source_failure_streak, 1)
            $sourceBackoffMinutes = [Math]::Max([int]$previousStatus.source_backoff_minutes, $SourceFailureBackoffMinutes)
            $sourceResumeAt = [DateTimeOffset]::Parse($previousNextRun).LocalDateTime
        }
    } catch {
        $sourceFailureStreak = 0
        $sourceBackoffMinutes = 0
        $sourceResumeAt = $null
    }
}

function Write-Status([string]$State, [string]$Message, [object]$Progress, [object]$ExitCode, [object]$NextRunAt) {
    $payload = [ordered]@{
        schema_version = 1
        state = $State
        mode = "research_history_backfill"
        pid = $PID
        config = $resolvedConfig
        updated_at = (Get-Date).ToString("o")
        last_exit_code = $ExitCode
        next_run_at = if ($null -eq $NextRunAt) { $null } else { ([datetime]$NextRunAt).ToString("o") }
        source_failure_streak = $sourceFailureStreak
        source_backoff_minutes = $sourceBackoffMinutes
        progress = $Progress
        message = $Message
    }
    $temporary = "$statusPath.tmp.$PID"
    $payload | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $statusPath -Force
}
function Read-Progress {
    if (-not (Test-Path -LiteralPath $progressPath)) { return $null }
    $code = "import json,pandas as pd,sys; print(json.dumps(pd.read_parquet(sys.argv[1]).iloc[-1].to_dict(), ensure_ascii=True, default=str))"
    $raw = & $python -c $code $progressPath
    if ($LASTEXITCODE -ne 0 -or -not $raw) { return $null }
    try { return ($raw | ConvertFrom-Json) } catch { return $null }
}
function Test-QuietWindow([datetime]$Now) {
    if ($quietStartTime -le $quietEndTime) {
        return $Now.TimeOfDay -ge $quietStartTime -and $Now.TimeOfDay -lt $quietEndTime
    }
    return $Now.TimeOfDay -ge $quietStartTime -or $Now.TimeOfDay -lt $quietEndTime
}
function Invoke-BackfillBatch {
    $script:lastBatchSourceUnavailable = $false
    if ($DryRun) {
        Write-Status "dry_run" "Dry run: no network request was made." (Read-Progress) 0 $null
        return 0
    }
    $stdout = Join-Path $resolvedOutput "history_backfill.stdout.$PID.log"
    $stderr = Join-Path $resolvedOutput "history_backfill.stderr.$PID.log"
    Remove-Item -LiteralPath $stdout,$stderr -Force -ErrorAction SilentlyContinue
    $arguments = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $aqScript,
        "--config", $resolvedConfig, "fetch", "--cached-universe",
        "--no-metadata", "--max-symbols", [string]$MaxSymbols
    )
    Write-Status "running" "Bounded research history batch is running." (Read-Progress) $null $null
    $process = Start-Process powershell.exe -ArgumentList $arguments -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    $null = $process.Handle
    $deadline = (Get-Date).AddMinutes($MaxRuntimeMinutes)
    while (-not $process.HasExited -and (Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 5
        $process.Refresh()
    }
    if (-not $process.HasExited) {
        Stop-Process -Id $process.Id -Force
        $process.WaitForExit()
        $exitCode = 124
        $message = "Batch timed out; validated partial data remains resumable."
    } else {
        $process.WaitForExit()
        $exitCode = $process.ExitCode
        $message = if ($exitCode -eq 0) { "Bounded history batch completed." } else { "History batch failed and will be retried." }
    }
    foreach ($path in @($stdout, $stderr)) {
        if (Test-Path -LiteralPath $path) {
            Get-Content -LiteralPath $path -Encoding UTF8 | Add-Content -LiteralPath $logPath -Encoding UTF8
            Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
        }
    }
    $progress = Read-Progress
    $script:lastBatchSourceUnavailable = (
        $null -ne $progress -and
        [string]$progress.status -ne "complete" -and
        [string]$progress.source_status -eq "source_unavailable"
    )
    if ($script:lastBatchSourceUnavailable) {
        $exitCode = 75
        $message = "Historical market source is unavailable; validated partial data is preserved and the next request is delayed."
    }
    $state = if ($null -ne $progress -and [string]$progress.status -eq "complete") { "complete" } elseif ($script:lastBatchSourceUnavailable) { "source_unavailable" } elseif ($exitCode -eq 0) { "waiting" } else { "failed" }
    Write-Status $state $message $progress $exitCode $null
    return $exitCode
}

function Invoke-HistoryValidation {
    if ($DryRun) {
        Write-Status "dry_run" "Dry run: completed history was not backtested." (Read-Progress) 0 $null
        return 0
    }
    $stdout = Join-Path $resolvedOutput "history_validation.stdout.$PID.log"
    $stderr = Join-Path $resolvedOutput "history_validation.stderr.$PID.log"
    Remove-Item -LiteralPath $stdout,$stderr -Force -ErrorAction SilentlyContinue
    $arguments = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $aqScript,
        "--config", $resolvedConfig, "history-validate"
    )
    $progress = Read-Progress
    Write-Status "validating" "Fixed-strategy long-history validation is running." $progress $null $null
    $process = Start-Process powershell.exe -ArgumentList $arguments -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    $null = $process.Handle
    $deadline = (Get-Date).AddMinutes($MaxValidationRuntimeMinutes)
    while (-not $process.HasExited -and (Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 5
        $process.Refresh()
    }
    if (-not $process.HasExited) {
        Stop-Process -Id $process.Id -Force
        $process.WaitForExit()
        $exitCode = 124
        $message = "Fixed-strategy history validation timed out and will be retried."
    } else {
        $process.WaitForExit()
        $exitCode = $process.ExitCode
        $message = if ($exitCode -eq 0) { "Fixed-strategy history validation completed." } else { "Fixed-strategy history validation failed and will be retried." }
    }
    foreach ($path in @($stdout, $stderr)) {
        if (Test-Path -LiteralPath $path) {
            Get-Content -LiteralPath $path -Encoding UTF8 | Add-Content -LiteralPath $logPath -Encoding UTF8
            Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
        }
    }
    Write-Status $(if ($exitCode -eq 0) { "complete" } else { "validation_failed" }) $message $progress $exitCode $null
    return $exitCode
}

try {
    try {
        $lockStream = [IO.File]::Open($lockPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    } catch [IO.IOException] {
        try {
            $stale = [IO.File]::Open($lockPath, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
            $stale.Dispose()
            Remove-Item -LiteralPath $lockPath -Force
            $lockStream = [IO.File]::Open($lockPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        } catch [IO.IOException] {
            throw "Another history backfill daemon is already running."
        }
    }
    $bytes = [Text.Encoding]::ASCII.GetBytes([string]$PID)
    $lockStream.Write($bytes, 0, $bytes.Length)
    $lockStream.Flush()
    while ($true) {
        $progress = Read-Progress
        if ($null -ne $progress -and [string]$progress.status -eq "complete") {
            $validationExitCode = Invoke-HistoryValidation
            if ($validationExitCode -eq 0) { exit 0 }
            if ($RunOnce) { exit $validationExitCode }
            $nextRun = (Get-Date).AddMinutes($IntervalMinutes)
            Write-Status "validation_failed" "Waiting to retry fixed-strategy history validation." $progress $validationExitCode $nextRun
            Start-Sleep -Seconds ($IntervalMinutes * 60)
            continue
        }
        if (Test-Path -LiteralPath $stopPath) {
            Write-Status "stopped" "Stop marker is present; daemon is exiting." $progress 0 $null
            exit 0
        }
        $now = Get-Date
        if ($null -ne $sourceResumeAt -and $now -lt $sourceResumeAt) {
            Write-Status "source_unavailable" "Persisted historical source outage backoff is still active." $progress 75 $sourceResumeAt
            if ($RunOnce) { exit 75 }
            $waitSeconds = [Math]::Max([int][Math]::Ceiling(($sourceResumeAt - $now).TotalSeconds), 1)
            Start-Sleep -Seconds $waitSeconds
            $sourceResumeAt = $null
            continue
        }
        if ((Test-Path -LiteralPath $observationLock) -or (Test-QuietWindow $now)) {
            $nextRun = $now.AddMinutes($IntervalMinutes)
            Write-Status "deferred" "Daily observation window is active; research backfill yielded." $progress $null $nextRun
            if ($RunOnce) { exit 0 }
            Start-Sleep -Seconds ($IntervalMinutes * 60)
            continue
        }
        $exitCode = Invoke-BackfillBatch
        $progress = Read-Progress
        if ($null -ne $progress -and [string]$progress.status -eq "complete") {
            continue
        }
        if ($script:lastBatchSourceUnavailable) {
            $sourceFailureStreak += 1
            $sourceBackoffMinutes = $SourceFailureBackoffMinutes
            $nextRun = (Get-Date).AddMinutes($sourceBackoffMinutes)
            $sourceResumeAt = $nextRun
            Write-Status "source_unavailable" "Historical market source is unavailable; waiting before a bounded retry." $progress $exitCode $nextRun
            if ($RunOnce) { exit $exitCode }
            Start-Sleep -Seconds ($sourceBackoffMinutes * 60)
            continue
        }
        $sourceFailureStreak = 0
        $sourceBackoffMinutes = 0
        $sourceResumeAt = $null
        if ($RunOnce) { exit $exitCode }
        $nextRun = (Get-Date).AddMinutes($IntervalMinutes)
        Write-Status "waiting" "Waiting before the next bounded history batch." $progress $exitCode $nextRun
        Start-Sleep -Seconds ($IntervalMinutes * 60)
    }
} catch {
    Write-Status "failed" $_.Exception.Message (Read-Progress) 1 $null
    exit 1
} finally {
    if ($null -ne $lockStream) {
        $lockStream.Dispose()
        Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
    }
}
