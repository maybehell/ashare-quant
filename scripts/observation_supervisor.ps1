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
    [int]$RestartDelaySeconds = 60,
    [int]$RestartWindowMinutes = 30,
    [int]$MaxRestartsPerWindow = 5,
    [switch]$SkipAlert,
    [switch]$BrokerReadOnlyRefresh
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$daemon = Join-Path $PSScriptRoot "observation_daemon.ps1"

function Resolve-ProjectPath([string]$Value) {
    if ([IO.Path]::IsPathRooted($Value)) { return [IO.Path]::GetFullPath($Value) }
    return [IO.Path]::GetFullPath((Join-Path $projectRoot $Value))
}

$resolvedConfig = Resolve-ProjectPath $Config
$resolvedAlertConfig = Resolve-ProjectPath $AlertConfig
$resolvedOutput = Resolve-ProjectPath $OutputDir
if (-not (Test-Path -LiteralPath $daemon)) { throw "Missing observation daemon: $daemon" }
if (-not (Test-Path -LiteralPath $resolvedConfig)) { throw "Missing config: $resolvedConfig" }
if (-not (Test-Path -LiteralPath $resolvedAlertConfig)) { throw "Missing alert config: $resolvedAlertConfig" }
if ($PollSeconds -lt 10) { throw "PollSeconds must be at least 10." }
if ($RestartDelaySeconds -lt 10) { throw "RestartDelaySeconds must be at least 10." }
if ($RestartWindowMinutes -lt 1 -or $MaxRestartsPerWindow -lt 1) {
    throw "Restart window and restart limit must be positive."
}
New-Item -ItemType Directory -Path $resolvedOutput -Force | Out-Null

$supervisorLockPath = Join-Path $resolvedOutput "observation_supervisor.lock.json"
$supervisorStatusPath = Join-Path $resolvedOutput "observation_supervisor_status.json"
$daemonLockPath = Join-Path $resolvedOutput "observation_daemon.lock.json"
$stopPath = Join-Path $resolvedOutput "observation_daemon.stop"
$scriptSha256 = (Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant()

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

function Get-LiveProcessFromLock([string]$Path, [string]$ExpectedMode) {
    $lock = Read-Json $Path
    if ($null -eq $lock -or [string]$lock.mode -ne $ExpectedMode) { return $null }
    $processId = [int]$lock.pid
    if ($processId -le 0) { return $null }
    $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($null -eq $process -or $process.ProcessName -notin @("powershell", "pwsh")) { return $null }
    try {
        $expectedStart = [DateTimeOffset]::Parse([string]$lock.process_started_at).LocalDateTime
        if ([math]::Abs(($process.StartTime - $expectedStart).TotalSeconds) -gt 5) { return $null }
    } catch {
        return $null
    }
    return $process
}

function Publish-Status(
    [string]$Status,
    [string]$Message,
    [Diagnostics.Process]$DaemonProcess,
    [string]$DaemonSource,
    [int]$RestartCount
) {
    Write-JsonAtomic $supervisorStatusPath ([ordered]@{
        status = $Status
        mode = "observation_only_supervisor"
        pid = $PID
        generated_at = (Get-Date).ToString("o")
        broker_readonly_refresh = [bool]$BrokerReadOnlyRefresh
        script_sha256 = $scriptSha256
        daemon_pid = if ($null -eq $DaemonProcess) { 0 } else { $DaemonProcess.Id }
        daemon_source = $DaemonSource
        restart_count_in_window = $RestartCount
        message = $Message
    })
}

$existingSupervisor = Get-LiveProcessFromLock $supervisorLockPath "observation_only_supervisor"
if ($null -ne $existingSupervisor -and $existingSupervisor.Id -ne $PID) {
    throw "Observation supervisor is already running with PID $($existingSupervisor.Id)."
}

$current = Get-Process -Id $PID
Write-JsonAtomic $supervisorLockPath ([ordered]@{
    pid = $PID
    started_at = (Get-Date).ToString("o")
    process_started_at = $current.StartTime.ToString("o")
    process_name = $current.ProcessName
    mode = "observation_only_supervisor"
    broker_readonly_refresh = [bool]$BrokerReadOnlyRefresh
    script_sha256 = $scriptSha256
})

$restartTimes = [Collections.Generic.List[datetime]]::new()
$daemonArguments = @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
    "-File", $daemon,
    "-Config", $resolvedConfig,
    "-AlertConfig", $resolvedAlertConfig,
    "-OutputDir", $resolvedOutput,
    "-StartAt", $StartAt,
    "-PollSeconds", $PollSeconds,
    "-MaxSymbols", $MaxSymbols,
    "-MaxRuntimeHours", $MaxRuntimeHours,
    "-MaxDailyAttempts", $MaxDailyAttempts,
    "-RetryMinutes", $RetryMinutes
)
if ($SkipAlert) { $daemonArguments += "-SkipAlert" }
if ($BrokerReadOnlyRefresh) { $daemonArguments += "-BrokerReadOnlyRefresh" }

try {
    while (-not (Test-Path -LiteralPath $stopPath)) {
        $daemonProcess = Get-LiveProcessFromLock $daemonLockPath "observation_only_daemon"
        $daemonSource = "adopted"
        if ($null -eq $daemonProcess) {
            $now = Get-Date
            $cutoff = $now.AddMinutes(-$RestartWindowMinutes)
            while ($restartTimes.Count -gt 0 -and $restartTimes[0] -lt $cutoff) {
                $restartTimes.RemoveAt(0)
            }
            if ($restartTimes.Count -ge $MaxRestartsPerWindow) {
                Publish-Status "backoff" "Restart limit reached; waiting before another observation-only restart." $null "none" $restartTimes.Count
                Start-Sleep -Seconds ([math]::Max($RestartDelaySeconds, 60))
                continue
            }
            $daemonProcess = Start-Process -FilePath "powershell.exe" -ArgumentList $daemonArguments -PassThru -WindowStyle Hidden
            $null = $daemonProcess.Handle
            $restartTimes.Add($now)
            $daemonSource = "started"
        }

        Publish-Status "monitoring" "Observation-only daemon is alive." $daemonProcess $daemonSource $restartTimes.Count
        Start-Sleep -Seconds $PollSeconds
        $daemonProcess.Refresh()
        if ($daemonProcess.HasExited -and -not (Test-Path -LiteralPath $stopPath)) {
            Publish-Status "restart_wait" "Observation-only daemon exited; restart is delayed." $null $daemonSource $restartTimes.Count
            Start-Sleep -Seconds $RestartDelaySeconds
        }
    }
    Publish-Status "stopped" "Observation stop marker is present; supervisor will not restart the daemon." $null "none" $restartTimes.Count
} finally {
    Remove-Item -LiteralPath $supervisorLockPath -Force -ErrorAction SilentlyContinue
}
