param(
    [string]$Config = "",
    [switch]$Submit,
    [switch]$ConfirmAccountSync
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$fallbackPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$outputDir = Join-Path $projectRoot "outputs"
$logDir = Join-Path $outputDir "logs"
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logPath = Join-Path $logDir "auto_live_cycle_$timestamp.log"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

if ($ConfirmAccountSync -and -not $Submit) {
    throw "-ConfirmAccountSync requires -Submit."
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
$arguments += "auto-live-cycle"
if ($Submit) {
    $arguments += "--submit"
}
if ($ConfirmAccountSync) {
    $arguments += "--confirm-account-sync"
}

Push-Location $projectRoot
try {
    & $python @arguments 2>&1 | Tee-Object -FilePath $logPath
    $exitCode = $LASTEXITCODE
} finally {
    Pop-Location
}

Write-Host "Auto-live cycle log: $logPath"
exit $exitCode
