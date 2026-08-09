param(
    [string]$EntryName = "AshareQuantDailyObservation.cmd",
    [string]$Config = "configs/default.yaml",
    [string]$AlertConfig = "configs/huatai_desktop_canary.yaml",
    [string]$OutputDir = "outputs",
    [string]$StartAt = "18:30",
    [int]$PollSeconds = 60,
    [int]$MaxSymbols = 800,
    [double]$MaxRuntimeHours = 2,
    [int]$MaxDailyAttempts = 3,
    [int]$RetryMinutes = 30,
    [switch]$SkipAlert,
    [switch]$BrokerReadOnlyRefresh,
    [switch]$Register,
    [switch]$ConfirmReplace,
    [switch]$StartNow
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$supervisor = Join-Path $PSScriptRoot "observation_supervisor.ps1"
if (-not (Test-Path -LiteralPath $supervisor)) { throw "Missing supervisor: $supervisor" }
[void][datetime]::ParseExact($StartAt, "HH:mm", [Globalization.CultureInfo]::InvariantCulture)
if ($PollSeconds -lt 10) { throw "PollSeconds must be at least 10." }
if ($MaxDailyAttempts -lt 1) { throw "MaxDailyAttempts must be positive." }
if ($RetryMinutes -lt 1) { throw "RetryMinutes must be positive." }

function Resolve-ProjectPath([string]$Value) {
    if ([IO.Path]::IsPathRooted($Value)) { return [IO.Path]::GetFullPath($Value) }
    return [IO.Path]::GetFullPath((Join-Path $projectRoot $Value))
}

$resolvedConfig = Resolve-ProjectPath $Config
$resolvedAlertConfig = Resolve-ProjectPath $AlertConfig
$resolvedOutput = Resolve-ProjectPath $OutputDir
if (-not (Test-Path -LiteralPath $resolvedConfig)) { throw "Missing config: $resolvedConfig" }
if (-not (Test-Path -LiteralPath $resolvedAlertConfig)) { throw "Missing alert config: $resolvedAlertConfig" }
$startup = [Environment]::GetFolderPath([Environment+SpecialFolder]::Startup)
if (-not $startup) { throw "Current user Startup folder is unavailable." }
$target = Join-Path $startup $EntryName

$argumentParts = @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
    "-File", ('"' + $supervisor + '"'),
    "-Config", ('"' + $resolvedConfig + '"'),
    "-AlertConfig", ('"' + $resolvedAlertConfig + '"'),
    "-OutputDir", ('"' + $resolvedOutput + '"'),
    "-StartAt", $StartAt,
    "-PollSeconds", $PollSeconds,
    "-MaxSymbols", $MaxSymbols,
    "-MaxRuntimeHours", $MaxRuntimeHours,
    "-MaxDailyAttempts", $MaxDailyAttempts,
    "-RetryMinutes", $RetryMinutes
)
if ($SkipAlert) { $argumentParts += "-SkipAlert" }
if ($BrokerReadOnlyRefresh) { $argumentParts += "-BrokerReadOnlyRefresh" }
$argumentLine = $argumentParts -join " "
$launcher = "@echo off`r`nstart `"`" /min powershell.exe $argumentLine`r`nexit /b 0`r`n"

Write-Host "Startup entry: $target"
Write-Host "Schedule: Monday-Friday at $StartAt"
Write-Host "Mode: observation_only_supervisor"
Write-Host "Broker read-only refresh: $([bool]$BrokerReadOnlyRefresh)"
Write-Host "Daily attempts: $MaxDailyAttempts; retry every $RetryMinutes minutes"
Write-Host "Command: powershell.exe $argumentLine"
if (-not $Register) {
    Write-Host "Preview only. Re-run with -Register to install the current-user Startup entry."
    exit 0
}
if ((Test-Path -LiteralPath $target) -and -not $ConfirmReplace) {
    throw "Startup entry already exists. Use -ConfirmReplace with -Register to replace it."
}
$launcher | Set-Content -LiteralPath $target -Encoding ASCII
Write-Host "Startup entry installed: $target"
if ($StartNow) {
    Start-Process powershell.exe -ArgumentList $argumentLine -WindowStyle Hidden
    Write-Host "Observation daemon started for the current session."
}