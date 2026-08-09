param(
    [string]$Config = "",
    [switch]$Loop,
    [switch]$Submit,
    [switch]$ConfirmAccountSync,
    [double]$IntervalSeconds = 60,
    [int]$MaxCycles = 0
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$fallbackPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$logDir = Join-Path $projectRoot "outputs\logs"
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logPath = Join-Path $logDir "auto_live_service_$timestamp.log"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

if ($ConfirmAccountSync -and -not $Submit) {
    throw "-ConfirmAccountSync requires -Submit."
}
if ($IntervalSeconds -le 0) {
    throw "-IntervalSeconds must be greater than zero."
}
if ($MaxCycles -lt 0) {
    throw "-MaxCycles cannot be negative."
}

$python = if (Test-Path -LiteralPath $venvPython) {
    $venvPython
} elseif (Test-Path -LiteralPath $fallbackPython) {
    $fallbackPython
} else {
    throw "No usable Python found. Run scripts\setup_env.ps1 first."
}

New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$arguments = @("-m", "ashare_quant")
if ($Config) {
    $arguments += @("--config", $Config)
}
$arguments += "auto-live-service"
if ($Loop) {
    $arguments += "--loop"
}
if ($Submit) {
    $arguments += "--submit"
}
if ($ConfirmAccountSync) {
    $arguments += "--confirm-account-sync"
}
$arguments += @("--interval-seconds", $IntervalSeconds.ToString([Globalization.CultureInfo]::InvariantCulture))
$arguments += @("--max-cycles", $MaxCycles.ToString([Globalization.CultureInfo]::InvariantCulture))

Push-Location $projectRoot
try {
    & $python @arguments 2>&1 | Tee-Object -FilePath $logPath
    $exitCode = $LASTEXITCODE
} finally {
    Pop-Location
}

Write-Host "Auto-live service log: $logPath"
exit $exitCode
