param(
    [string]$TaskName = "AshareQuantDailyObservation",
    [string]$Config = "configs/default.yaml",
    [string]$AlertConfig = "configs/huatai_desktop_canary.yaml",
    [string]$StartAt = "18:30",
    [int]$MaxSymbols = 300,
    [double]$MaxRuntimeHours = 1,
    [switch]$NoMetadata,
    [switch]$SkipStress,
    [switch]$BrokerReadOnlyRefresh,
    [switch]$Register,
    [switch]$ConfirmReplace
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $PSScriptRoot "daily_observation.ps1"

if (-not (Test-Path -LiteralPath $runner)) {
    throw "Missing observation runner: $runner"
}
if ($MaxSymbols -le 0) {
    throw "MaxSymbols must be greater than zero."
}
if ($MaxRuntimeHours -le 0) {
    throw "MaxRuntimeHours must be greater than zero."
}
$parsedStart = [datetime]::ParseExact(
    $StartAt,
    "HH:mm",
    [Globalization.CultureInfo]::InvariantCulture
)
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

$argumentParts = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", ('"' + $runner + '"'),
    "-Config", ('"' + $resolvedConfig + '"'),
    "-AlertConfig", ('"' + $resolvedAlertConfig + '"'),
    "-MaxSymbols", $MaxSymbols,
    "-MaxRuntimeHours", $MaxRuntimeHours
)
if ($NoMetadata) {
    $argumentParts += "-NoMetadata"
}
if ($SkipStress) {
    $argumentParts += "-SkipStress"
}
if ($BrokerReadOnlyRefresh) {
    $argumentParts += "-BrokerReadOnlyRefresh"
}
$arguments = $argumentParts -join " "

Write-Host "Task name: $TaskName"
Write-Host "Schedule: Monday-Friday at $StartAt"
Write-Host "Mode: observation_only"
Write-Host "Broker read-only refresh: $([bool]$BrokerReadOnlyRefresh)"
Write-Host "Maximum runtime: $MaxRuntimeHours hours"
Write-Host "Executable: powershell.exe"
Write-Host "Alert config: $resolvedAlertConfig"
Write-Host "Arguments: $arguments"
Write-Host "Working directory: $projectRoot"
if (-not $Register) {
    Write-Host "Preview only. Re-run with -Register to create the scheduled task."
    exit 0
}

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -ne $existing -and -not $ConfirmReplace) {
    throw "Scheduled task already exists. Use -ConfirmReplace with -Register to replace it."
}
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $parsedStart
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours $MaxRuntimeHours)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "A-share quant preregistered forward-candidate refresh and immutable shadow observation only; no paper or broker order command." -Force | Out-Null
Write-Host "Scheduled task registered: $TaskName"
