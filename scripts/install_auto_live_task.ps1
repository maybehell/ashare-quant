param(
    [string]$TaskName = "AshareQuantAutoLive",
    [string]$Config = "",
    [string]$StartAt = "09:20",
    [int]$IntervalSeconds = 60,
    [int]$MaxCycles = 360,
    [switch]$Submit,
    [switch]$ConfirmSubmit
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$serviceScript = Join-Path $PSScriptRoot "auto_live_service.ps1"

if (-not (Test-Path -LiteralPath $serviceScript)) {
    throw "Missing service script: $serviceScript"
}
if ($Submit -and -not $ConfirmSubmit) {
    throw "Registering a submit-capable task requires both -Submit and -ConfirmSubmit."
}
if ($IntervalSeconds -le 0 -or $MaxCycles -le 0) {
    throw "IntervalSeconds and MaxCycles must be greater than zero for a scheduled loop."
}

$parsedStart = [datetime]::ParseExact($StartAt, "HH:mm", [Globalization.CultureInfo]::InvariantCulture)
$argumentParts = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", ('"' + $serviceScript + '"'),
    "-Loop",
    "-IntervalSeconds", $IntervalSeconds,
    "-MaxCycles", $MaxCycles
)
if ($Config) {
    $resolvedConfig = if ([IO.Path]::IsPathRooted($Config)) { $Config } else { Join-Path $projectRoot $Config }
    $argumentParts += @("-Config", ('"' + $resolvedConfig + '"'))
}
if ($Submit) {
    $argumentParts += "-Submit"
}

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument ($argumentParts -join " ") -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $parsedStart
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 8)
$description = if ($Submit) {
    "A-share quant unattended submit service. Normal strategy, adapter, risk, account and trading-window gates remain mandatory."
} else {
    "A-share quant unattended audit service. Broker submission is disabled."
}

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description $description -Force | Out-Null
Write-Host "Scheduled task registered: $TaskName"
Write-Host "Mode: $(if ($Submit) { 'submit-capable (still gated)' } else { 'audit-only' })"
Write-Host "Start: $StartAt, interval: $IntervalSeconds seconds, max cycles: $MaxCycles"
