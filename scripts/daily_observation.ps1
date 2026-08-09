param(
    [string]$Config = "configs/default.yaml",
    [string]$AlertConfig = "configs/huatai_desktop_canary.yaml",
    [int]$MaxSymbols = 300,
    [double]$MaxRuntimeHours = 1,
    [switch]$NoMetadata,
    [switch]$SkipRefresh,
    [switch]$SkipStress,
    [switch]$SkipAlert,
    [switch]$BrokerReadOnlyRefresh
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$aqScript = Join-Path $PSScriptRoot "aq.ps1"
$outputDir = Join-Path $projectRoot "outputs"
$lockPath = Join-Path $outputDir "daily_observation.lock.json"
$statusPath = Join-Path $outputDir "daily_observation_status.csv"
$deliveryStatusPath = Join-Path $outputDir "daily_observation_delivery_status.csv"
$logPath = Join-Path $outputDir "daily_observation.log"

if ($MaxSymbols -le 0) {
    throw "MaxSymbols must be greater than zero."
}
if ($MaxRuntimeHours -le 0) {
    throw "MaxRuntimeHours must be greater than zero."
}
if (-not (Test-Path -LiteralPath $aqScript)) {
    throw "Missing runner: $aqScript"
}
$resolvedConfig = if ([IO.Path]::IsPathRooted($Config)) {
    $Config
} else {
    Join-Path $projectRoot $Config
}
if (-not (Test-Path -LiteralPath $resolvedConfig)) {
    throw "Missing config: $resolvedConfig"
}
$resolvedAlertConfig = if ([IO.Path]::IsPathRooted($AlertConfig)) {
    $AlertConfig
} else {
    Join-Path $projectRoot $AlertConfig
}
if (-not (Test-Path -LiteralPath $resolvedAlertConfig)) {
    throw "Missing alert config: $resolvedAlertConfig"
}
New-Item -ItemType Directory -Path $outputDir -Force | Out-Null

function Publish-StaleObservationLock {
    param(
        [object]$ExistingLock,
        [int]$ExistingPid,
        [string]$Reason
    )

    $now = Get-Date
    $lockStartedAt = if ($null -ne $ExistingLock) { [string]$ExistingLock.started_at } else { "" }
    $elapsedSeconds = 0.0
    if ($lockStartedAt) {
        try {
            $parsedStart = [DateTimeOffset]::Parse($lockStartedAt)
            $elapsedSeconds = [math]::Max(0.0, ($now.ToUniversalTime() - $parsedStart.UtcDateTime).TotalSeconds)
        } catch {
            $elapsedSeconds = 0.0
        }
    }
    [PSCustomObject]@{
        status = "stale_lock"
        mode = "observation_only"
        started_at = $lockStartedAt
        finished_at = $now.ToString("o")
        elapsed_seconds = [math]::Round($elapsedSeconds, 3)
        exit_code = 2
        config = $resolvedConfig
        max_symbols = $MaxSymbols
        message = "$Reason;pid=$ExistingPid"
    } | Export-Csv -LiteralPath $statusPath -NoTypeInformation -Encoding UTF8

    "$($now.ToString('o')) stale observation lock: $Reason; pid=$ExistingPid" |
        Tee-Object -FilePath $logPath -Append | Out-Null
    try {
        $reportResult = & $aqScript "--config" $resolvedConfig "report" 2>&1
        $reportResult | Tee-Object -FilePath $logPath -Append
    } catch {
        "Report refresh for stale observation lock failed: $($_.Exception.Message)" |
            Tee-Object -FilePath $logPath -Append | Out-Null
    }
    if (-not $SkipAlert) {
        try {
            $alertResult = & $aqScript "--config" $resolvedAlertConfig "observation-alert" "--strategy-output" $outputDir 2>&1
            $alertResult | Tee-Object -FilePath $logPath -Append
        } catch {
            "Stale observation lock alert failed: $($_.Exception.Message)" |
                Tee-Object -FilePath $logPath -Append | Out-Null
        }
    }
    exit 2
}

if (Test-Path -LiteralPath $lockPath) {
    $existing = $null
    try {
        $existing = Get-Content -LiteralPath $lockPath -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        $existing = $null
    }
    $existingPid = if ($null -ne $existing) { [int]$existing.pid } else { 0 }
    $existingProcess = if ($existingPid -gt 0) {
        Get-Process -Id $existingPid -ErrorAction SilentlyContinue
    } else {
        $null
    }
    if ($null -ne $existingProcess) {
        $lockStarted = $null
        try {
            $lockStartValue = if ([string]$existing.process_started_at) {
                [string]$existing.process_started_at
            } else {
                [string]$existing.started_at
            }
            $lockStarted = [DateTimeOffset]::Parse($lockStartValue)
        } catch {
            $lockStarted = $null
        }

        $processMatchesLock = $existingProcess.ProcessName -in @("powershell", "pwsh")
        if ([string]$existing.process_name) {
            $processMatchesLock = $processMatchesLock -and (
                [string]$existing.process_name -eq $existingProcess.ProcessName
            )
        }
        if ($null -ne $lockStarted) {
            try {
                $processStartUtc = $existingProcess.StartTime.ToUniversalTime()
                $processMatchesLock = $processMatchesLock -and (
                    [math]::Abs(($processStartUtc - $lockStarted.UtcDateTime).TotalSeconds) -le 5
                )
            } catch {
                $processMatchesLock = $false
            }
        } else {
            $processMatchesLock = $false
        }
        if (-not $processMatchesLock) {
            Remove-Item -LiteralPath $lockPath -Force
        } else {
            $lockAgeHours = if ($null -eq $lockStarted) {
                [double]::PositiveInfinity
            } else {
                [math]::Max(0.0, ((Get-Date).ToUniversalTime() - $lockStarted.UtcDateTime).TotalHours)
            }
            if ($lockAgeHours -gt $MaxRuntimeHours) {
                Publish-StaleObservationLock $existing $existingPid ("runtime_exceeded_" + $MaxRuntimeHours + "h")
            }
            throw "Daily observation is already running with PID $existingPid (age=$([math]::Round($lockAgeHours, 2))h)."
        }
    } else {
        Remove-Item -LiteralPath $lockPath -Force
    }
}

$startedAt = Get-Date
$currentProcess = Get-Process -Id $PID
$lock = [ordered]@{
    pid = $PID
    started_at = $startedAt.ToString("o")
    process_started_at = $currentProcess.StartTime.ToString("o")
    process_name = $currentProcess.ProcessName
    config = $resolvedConfig
    mode = "observation_only"
}
$lock | ConvertTo-Json | Set-Content -LiteralPath $lockPath -Encoding UTF8

$arguments = @(
    "--config", $resolvedConfig,
    "forward-daily",
    "--refresh",
    "--quiet"
)
if ($SkipRefresh) {
    $arguments = @($arguments | Where-Object { $_ -ne "--refresh" })
}

$exitCode = 1
$message = "not_started"
$result = @()
$previousErrorActionPreference = $ErrorActionPreference
try {
    $ErrorActionPreference = "Continue"
    $result = & $aqScript @arguments 2>&1
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorActionPreference
    $result | Tee-Object -FilePath $logPath -Append

    if ($exitCode -ne 0) {
        $errorSummary = (($result | ForEach-Object { $_.ToString().Trim() } | Where-Object { $_ }) -join " | ")
        if ($errorSummary.Length -gt 1000) {
            $errorSummary = $errorSummary.Substring(0, 1000)
        }
        $message = if ($errorSummary) { "command_failed: $errorSummary" } else { "command_failed_without_output" }
    } else {
        $message = "completed"
    }
} catch {
    $ErrorActionPreference = $previousErrorActionPreference
    $_ | Out-String | Tee-Object -FilePath $logPath -Append | Out-Null
    $message = [string]$_.Exception.Message
    $exitCode = 1
    if ([string]::IsNullOrWhiteSpace($message)) {
        $message = "observation_exception_without_message"
    }
} finally {
    $ErrorActionPreference = $previousErrorActionPreference
    $finishedAt = Get-Date
    [PSCustomObject]@{
        status = if ($exitCode -eq 0) { "completed" } else { "failed" }
        mode = "observation_only"
        started_at = $startedAt.ToString("o")
        finished_at = $finishedAt.ToString("o")
        elapsed_seconds = [math]::Round(($finishedAt - $startedAt).TotalSeconds, 3)
        exit_code = $exitCode
        config = $resolvedConfig
        max_symbols = $MaxSymbols
        message = $message
    } | Export-Csv -LiteralPath $statusPath -NoTypeInformation -Encoding UTF8
    Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
}
$observationExitCode = $exitCode
try {
    $reportResult = & $aqScript "--config" $resolvedConfig "report" 2>&1
    $reportResult | Tee-Object -FilePath $logPath -Append
} catch {
    "Report refresh after observation failed: $($_.Exception.Message)" |
        Tee-Object -FilePath $logPath -Append | Out-Null
}

try {
    $statusResult = & $aqScript "--config" $resolvedConfig "local-status" "--live-config" $resolvedAlertConfig 2>&1
    $statusResult | Tee-Object -FilePath $logPath -Append
} catch {
    "Local operator status refresh failed: $($_.Exception.Message)" |
        Tee-Object -FilePath $logPath -Append | Out-Null
}
$brokerRefreshExitCode = 0
$brokerRefreshMessage = if ($BrokerReadOnlyRefresh) { "not_started" } else { "disabled" }
if ($BrokerReadOnlyRefresh) {
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $brokerRefreshResult = & $aqScript "--config" $resolvedConfig "broker-refresh" "--live-config" $resolvedAlertConfig 2>&1
        $brokerRefreshExitCode = $LASTEXITCODE
        $brokerRefreshResult | Tee-Object -FilePath $logPath -Append
        $brokerRefreshMessage = if ($brokerRefreshExitCode -eq 0) { "completed" } else { "command_failed" }
    } catch {
        $brokerRefreshExitCode = 1
        $brokerRefreshMessage = [string]$_.Exception.Message
        "Broker read-only refresh failed: $($_.Exception.Message)" |
            Tee-Object -FilePath $logPath -Append | Out-Null
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
}
$alertExitCode = 0
$alertMessage = if ($SkipAlert) { "skipped" } else { "not_started" }
if (-not $SkipAlert) {
    try {
        $alertResult = & $aqScript "--config" $resolvedAlertConfig "observation-alert" "--strategy-output" $outputDir 2>&1
        $alertExitCode = $LASTEXITCODE
        $alertResult | Tee-Object -FilePath $logPath -Append
        $alertMessage = if ($alertExitCode -eq 0) { "completed" } else { "command_failed" }
        if ($alertExitCode -ne 0) {
            "Observation alert command failed with exit code $alertExitCode." |
                Tee-Object -FilePath $logPath -Append | Out-Null
        }
    } catch {
        $alertExitCode = 1
        $alertMessage = [string]$_.Exception.Message
        "Observation alert dispatch failed: $($_.Exception.Message)" |
            Tee-Object -FilePath $logPath -Append | Out-Null
    }
}

[PSCustomObject]@{
    status = if ($SkipAlert) { "skipped" } elseif ($alertExitCode -eq 0) { "completed" } else { "pending_retry" }
    generated_at = (Get-Date).ToString("o")
    exit_code = $alertExitCode
    message = $alertMessage
    outbox_retry_enabled = $true
    broker_readonly_refresh_enabled = [bool]$BrokerReadOnlyRefresh
    broker_readonly_refresh_exit_code = $brokerRefreshExitCode
    broker_readonly_refresh_message = $brokerRefreshMessage
} | Export-Csv -LiteralPath $deliveryStatusPath -NoTypeInformation -Encoding UTF8

# Core observation success controls market-data retries. Alert delivery is durable
# in the outbox and must not trigger another expensive full-universe refresh.
exit $observationExitCode
