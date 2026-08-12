param(
    [string]$Config = "configs/default.yaml",
    [string]$AlertConfig = "configs/huatai_desktop_canary.yaml",
    [string]$OutputDir = "outputs",
    [string]$StartAt = "18:30",
    [int]$PollSeconds = 60,
    [int]$MaxSymbols = 800,
    [double]$MaxRuntimeHours = 2,
    [int]$MaxDailyAttempts = 3,
    [int]$RetryMinutes = 30,
    [switch]$NoMetadata,
    [switch]$SkipStress,
    [switch]$SkipAlert,
    [switch]$BrokerReadOnlyRefresh,
    [switch]$Once,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$scriptSha256 = (Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant()
$runner = Join-Path $PSScriptRoot "daily_observation.ps1"
$aqScript = Join-Path $PSScriptRoot "aq.ps1"

if ($PollSeconds -lt 10) { throw "PollSeconds must be at least 10." }
if ($MaxDailyAttempts -lt 1) { throw "MaxDailyAttempts must be positive." }
if ($RetryMinutes -lt 1) { throw "RetryMinutes must be positive." }
if (-not (Test-Path -LiteralPath $runner)) { throw "Missing observation runner: $runner" }
if (-not (Test-Path -LiteralPath $aqScript)) { throw "Missing aq runner: $aqScript" }
$scheduledTime = [datetime]::ParseExact($StartAt, "HH:mm", [Globalization.CultureInfo]::InvariantCulture).TimeOfDay

function Resolve-ProjectPath([string]$Value) {
    if ([IO.Path]::IsPathRooted($Value)) { return [IO.Path]::GetFullPath($Value) }
    return [IO.Path]::GetFullPath((Join-Path $projectRoot $Value))
}

$resolvedConfig = Resolve-ProjectPath $Config
$resolvedAlertConfig = Resolve-ProjectPath $AlertConfig
$resolvedOutput = Resolve-ProjectPath $OutputDir
if (-not (Test-Path -LiteralPath $resolvedConfig)) { throw "Missing config: $resolvedConfig" }
if (-not (Test-Path -LiteralPath $resolvedAlertConfig)) { throw "Missing alert config: $resolvedAlertConfig" }
New-Item -ItemType Directory -Path $resolvedOutput -Force | Out-Null

$lockPath = Join-Path $resolvedOutput "observation_daemon.lock.json"
$statusPath = Join-Path $resolvedOutput "observation_daemon_status.json"
$statePath = Join-Path $resolvedOutput "observation_daemon_state.json"
$stopPath = Join-Path $resolvedOutput "observation_daemon.stop"
$runnerStatusPath = Join-Path $resolvedOutput "daily_observation_status.csv"

function Write-JsonAtomic([string]$Path, [object]$Value) {
    $temporary = "$Path.tmp.$PID"
    try {
        $Value | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $temporary -Encoding UTF8
        Move-Item -LiteralPath $temporary -Destination $Path -Force
    } finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
}

function Read-Json([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try { return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json }
    catch { return $null }
}

function Publish-Status([string]$Status, [string]$Message, [object]$State) {
    Write-JsonAtomic $statusPath ([ordered]@{
        status = $Status
        mode = "observation_only_daemon"
        pid = $PID
        generated_at = (Get-Date).ToString("o")
        schedule = $StartAt
        config = $resolvedConfig
        alert_config = $resolvedAlertConfig
        dry_run = [bool]$DryRun
        broker_readonly_refresh = [bool]$BrokerReadOnlyRefresh
        script_sha256 = $scriptSha256
        state = $State
        message = $Message
    })
}

function Sync-RunnerCompletion([object]$State, [datetime]$Now) {
    if (-not (Test-Path -LiteralPath $runnerStatusPath)) { return $State }
    try {
        $row = @(Import-Csv -LiteralPath $runnerStatusPath -Encoding UTF8)[-1]
        $finishedAt = [DateTimeOffset]::Parse([string]$row.finished_at).LocalDateTime
        $rowConfig = [IO.Path]::GetFullPath([string]$row.config)
        $configMatches = $rowConfig.Equals(
            $resolvedConfig,
            [StringComparison]::OrdinalIgnoreCase
        )
        $resultToday = (
            $finishedAt.Date -eq $Now.Date -and
            $finishedAt.TimeOfDay -ge $scheduledTime -and
            $configMatches
        )
        $completedToday = (
            $resultToday -and
            [string]$row.status -eq "completed" -and
            [int]$row.exit_code -eq 0
        )
        $strategyBlockedToday = (
            $resultToday -and
            [string]$row.status -eq "strategy_blocked" -and
            [int]$row.exit_code -eq 20
        )
    } catch {
        return $State
    }
    $today = $Now.ToString("yyyy-MM-dd")
    $alreadyRecovered = (
        ($completedToday -and [string]$State.last_success_date -eq $today) -or
        ($strategyBlockedToday -and [string]$State.attempt_date -eq $today -and [int]$State.last_exit_code -eq 20)
    )
    if ((-not $completedToday -and -not $strategyBlockedToday) -or $alreadyRecovered) {
        return $State
    }
    $attemptCount = if ([string]$State.attempt_date -eq $today) {
        [math]::Max(1, [int]$State.attempt_count)
    } else {
        1
    }
    $recovered = [ordered]@{
        last_success_date = if ($completedToday) { $today } else { [string]$State.last_success_date }
        attempt_date = $today
        attempt_count = $attemptCount
        next_retry_at = ""
        last_exit_code = if ($completedToday) { 0 } else { 20 }
        last_message = if ($completedToday) { "recovered_from_daily_observation_status" } else { "recovered_strategy_block_from_daily_observation_status" }
        last_finished_at = $finishedAt.ToString("o")
    }
    Write-JsonAtomic $statePath $recovered
    return $recovered
}

function Repair-PreScheduleSuccess([object]$State, [datetime]$Now) {
    if ([string]$State.last_success_date -ne $Now.ToString("yyyy-MM-dd")) { return $State }
    $finished = $null
    if ([string]$State.last_finished_at) {
        try { $finished = [DateTimeOffset]::Parse([string]$State.last_finished_at).LocalDateTime }
        catch { $finished = $null }
    }
    if ($null -ne $finished -and $finished.Date -eq $Now.Date -and $finished.TimeOfDay -ge $scheduledTime) {
        return $State
    }
    $repaired = [ordered]@{
        last_success_date = ""
        attempt_date = ""
        attempt_count = 0
        next_retry_at = ""
        last_exit_code = ""
        last_message = "discarded_pre_schedule_success"
        last_finished_at = if ($null -eq $finished) { "" } else { $finished.ToString("o") }
    }
    Write-JsonAtomic $statePath $repaired
    return $repaired
}
function Test-LockOwner([object]$Lock, [Diagnostics.Process]$Process) {
    if ($null -eq $Lock -or $null -eq $Process) { return $false }
    if ([string]$Lock.mode -ne "observation_only_daemon") { return $false }
    if ($Process.ProcessName -notin @("powershell", "pwsh")) { return $false }
    if ([string]$Lock.process_name -and [string]$Lock.process_name -ne $Process.ProcessName) { return $false }
    $lockStartValue = if ([string]$Lock.process_started_at) {
        [string]$Lock.process_started_at
    } else {
        [string]$Lock.started_at
    }
    if (-not $lockStartValue) { return $false }
    try {
        $lockStart = [DateTimeOffset]::Parse($lockStartValue).LocalDateTime
        $processStart = $Process.StartTime
    } catch {
        return $false
    }
    return [math]::Abs(($processStart - $lockStart).TotalSeconds) -le 5
}

$existingLock = Read-Json $lockPath
if ($null -ne $existingLock) {
    $existingPid = [int]$existingLock.pid
    $existingProcess = if ($existingPid -gt 0) { Get-Process -Id $existingPid -ErrorAction SilentlyContinue } else { $null }
    if ($existingPid -ne $PID -and (Test-LockOwner $existingLock $existingProcess)) {
        throw "Observation daemon is already running with PID $existingPid."
    }
    Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
}

$currentProcess = Get-Process -Id $PID
Write-JsonAtomic $lockPath ([ordered]@{
    pid = $PID
    started_at = (Get-Date).ToString("o")
    process_started_at = $currentProcess.StartTime.ToString("o")
    process_name = $currentProcess.ProcessName
    mode = "observation_only_daemon"
    config = $resolvedConfig
    broker_readonly_refresh = [bool]$BrokerReadOnlyRefresh
    script_sha256 = $scriptSha256
})

try {
    while ($true) {
        $now = Get-Date
        $state = Read-Json $statePath
        if ($null -eq $state) {
            $state = [PSCustomObject]@{
                last_success_date = ""
                attempt_date = ""
                attempt_count = 0
                next_retry_at = ""
                last_exit_code = ""
                last_message = "not_run"
            }
        }
        $state = Repair-PreScheduleSuccess $state $now
        $state = Sync-RunnerCompletion $state $now

        if (Test-Path -LiteralPath $stopPath) {
            Publish-Status "stopped" "Stop marker is present: $stopPath" $state
            break
        }

        $today = $now.ToString("yyyy-MM-dd")
        $weekday = $now.DayOfWeek -notin @([DayOfWeek]::Saturday, [DayOfWeek]::Sunday)
        $afterStart = $now.TimeOfDay -ge $scheduledTime
        $attemptCount = if ([string]$state.attempt_date -eq $today) { [int]$state.attempt_count } else { 0 }
        $lastSuccess = [string]$state.last_success_date
        $nextRetry = $null
        if ([string]$state.next_retry_at) {
            try { $nextRetry = [DateTimeOffset]::Parse([string]$state.next_retry_at).LocalDateTime } catch { $nextRetry = $null }
        }
        $retryReady = $null -eq $nextRetry -or $now -ge $nextRetry
        $strategyBlockedToday = (
            [string]$state.attempt_date -eq $today -and
            [int]$state.last_exit_code -eq 20
        )
        $calendarStatus = if ($weekday) { "unchecked" } else { "closed" }
        $calendarMessage = ""
        $calendarOpen = $weekday
        $calendarCheckNeeded = (
            $weekday -and $afterStart -and $lastSuccess -ne $today -and
            $attemptCount -lt $MaxDailyAttempts -and $retryReady -and -not $DryRun
        )
        if ($calendarCheckNeeded) {
            $previousPreference = $ErrorActionPreference
            try {
                $ErrorActionPreference = "Continue"
                $calendarResult = & $aqScript "--config" $resolvedConfig "calendar-status" `
                    "--date" $today "--refresh-missing" "--strict-exit-code" 2>&1
                $calendarExitCode = $LASTEXITCODE
            } finally {
                $ErrorActionPreference = $previousPreference
            }
            $calendarMessage = (($calendarResult | ForEach-Object { $_.ToString().Trim() } | Where-Object { $_ }) -join " | ")
            if ($calendarMessage.Length -gt 1000) { $calendarMessage = $calendarMessage.Substring(0, 1000) }
            $calendarStatus = if ($calendarExitCode -eq 0) {
                "open"
            } elseif ($calendarExitCode -eq 3) {
                "closed"
            } else {
                "unknown"
            }
            $calendarOpen = $calendarStatus -eq "open"
        }
        $due = $weekday -and $calendarOpen -and $afterStart -and $lastSuccess -ne $today -and -not $strategyBlockedToday -and $attemptCount -lt $MaxDailyAttempts -and $retryReady
        if ($due) {
            if ($DryRun) {
                Publish-Status "dry_run_due" "Observation would run now; no command executed." $state
            } else {
                $arguments = @(
                    "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", $runner,
                    "-Config", $resolvedConfig,
                    "-AlertConfig", $resolvedAlertConfig,
                    "-OutputDir", $resolvedOutput,
                    "-MaxSymbols", $MaxSymbols,
                    "-MaxRuntimeHours", $MaxRuntimeHours
                )
                if ($NoMetadata) { $arguments += "-NoMetadata" }
                if ($SkipStress) { $arguments += "-SkipStress" }
                if ($SkipAlert) { $arguments += "-SkipAlert" }
                if ($BrokerReadOnlyRefresh) { $arguments += "-BrokerReadOnlyRefresh" }
                $stdoutPath = Join-Path $resolvedOutput "observation_daemon_child.$PID.stdout.log"
                $stderrPath = Join-Path $resolvedOutput "observation_daemon_child.$PID.stderr.log"
                Remove-Item -LiteralPath $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue
                $childStartedAt = Get-Date
                $timedOut = $false
                $runningState = [ordered]@{
                    last_success_date = $lastSuccess
                    attempt_date = $today
                    attempt_count = ($attemptCount + 1)
                    next_retry_at = ""
                    last_exit_code = ""
                    last_message = "running"
                    child_pid = ""
                    child_started_at = $childStartedAt.ToString("o")
                    elapsed_seconds = 0.0
                }
                try {
                    $child = Start-Process -FilePath "powershell.exe" -ArgumentList $arguments `
                        -PassThru -WindowStyle Hidden `
                        -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
                    $null = $child.Handle
                    $runningState.child_pid = $child.Id
                    Publish-Status "running" "Observation child process is running." $runningState
                    while (-not $child.HasExited) {
                        $elapsed = ((Get-Date) - $childStartedAt).TotalSeconds
                        $runningState.elapsed_seconds = [math]::Round($elapsed, 3)
                        Publish-Status "running" "Observation child process is running." $runningState
                        if ($elapsed -gt ($MaxRuntimeHours * 3600.0)) {
                            Stop-Process -Id $child.Id -Force -ErrorAction SilentlyContinue
                            $timedOut = $true
                            break
                        }
                        Start-Sleep -Seconds ([math]::Min($PollSeconds, 30))
                        $child.Refresh()
                    }
                    $child.WaitForExit()
                    $child.Refresh()
                    $exitCode = if ($timedOut) { 124 } else { $child.ExitCode }
                    $result = @()
                    if (Test-Path -LiteralPath $stdoutPath) {
                        $result += Get-Content -LiteralPath $stdoutPath -Encoding UTF8 -ErrorAction SilentlyContinue
                    }
                    if (Test-Path -LiteralPath $stderrPath) {
                        $result += Get-Content -LiteralPath $stderrPath -Encoding UTF8 -ErrorAction SilentlyContinue
                    }
                    if ($timedOut) {
                        $result += "Observation child exceeded MaxRuntimeHours=$MaxRuntimeHours and was stopped."
                    }
                } finally {
                    Remove-Item -LiteralPath $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue
                }
                $attemptCount += 1
                $message = (($result | ForEach-Object { $_.ToString().Trim() } | Where-Object { $_ }) -join " | ")
                if ($message.Length -gt 2000) { $message = $message.Substring(0, 2000) }
                $success = $exitCode -eq 0
                $terminalStrategyBlock = $exitCode -eq 20
                $retryBase = Get-Date
                $nextRetryAt = if ($success -or $terminalStrategyBlock -or $attemptCount -ge $MaxDailyAttempts) { "" } else { $retryBase.AddMinutes($RetryMinutes).ToString("o") }
                $state = [ordered]@{
                    last_success_date = if ($success) { $today } else { $lastSuccess }
                    attempt_date = $today
                    attempt_count = $attemptCount
                    next_retry_at = $nextRetryAt
                    last_exit_code = $exitCode
                    last_message = if ($message) { $message } else { "no_output" }
                    last_finished_at = (Get-Date).ToString("o")
                }
                Write-JsonAtomic $statePath $state
                $resultStatus = if ($success) { "completed" } elseif ($terminalStrategyBlock) { "strategy_blocked" } else { "retry_wait" }
                Publish-Status $resultStatus $state.last_message $state
            }
        } else {
            $reason = if (-not $weekday) {
                "weekend"
            } elseif ($calendarStatus -eq "closed") {
                "market_closed"
            } elseif ($calendarStatus -eq "unknown") {
                "trading_calendar_unavailable: $calendarMessage"
            } elseif (-not $afterStart) {
                "waiting_for_schedule"
            } elseif ($lastSuccess -eq $today) {
                "already_completed_today"
            } elseif ($strategyBlockedToday) {
                "strategy_blocked_no_retry"
            } elseif ($attemptCount -ge $MaxDailyAttempts) {
                "daily_attempt_limit_reached"
            } else {
                "waiting_for_retry"
            }
            Publish-Status "waiting" $reason $state
        }

        if ($Once) { break }
        Start-Sleep -Seconds $PollSeconds
    }
} finally {
    Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
}