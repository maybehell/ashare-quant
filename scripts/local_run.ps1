param(
    [switch]$Refresh,
    [switch]$SkipBroker,
    [switch]$SkipStress,
    [int]$MaxSymbols = 800,
    [switch]$NoMetadata,
    [switch]$NoOpen,
    [switch]$NoPause,
    [switch]$Help
)

if ($Help) {
    Write-Host "A-share quant local operator"
    Write-Host "Usage: run_local.cmd [-SkipBroker] [-SkipStress] [-NoMetadata] [-NoOpen] [-NoPause] [-MaxSymbols N]"
    Write-Host "Double-clicking refreshes cached-universe market data and runs the guarded local workflow."
    exit 0
}

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$fallbackPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$logDir = Join-Path $projectRoot "outputs\logs"
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logPath = Join-Path $logDir "local_run_$timestamp.log"
$statusPath = Join-Path $projectRoot "outputs\local_operator_status.html"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

if ($MaxSymbols -le 0) {
    throw "-MaxSymbols must be greater than zero."
}

$python = if (Test-Path -LiteralPath $venvPython) {
    $venvPython
} elseif (Test-Path -LiteralPath $fallbackPython) {
    $fallbackPython
} else {
    throw "No usable Python found. Run scripts\setup_env.ps1 first."
}

New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$arguments = @("-m", "ashare_quant", "local-run")
if ($Refresh) {
    $arguments += @("--refresh", "--cached-universe", "--max-symbols", $MaxSymbols.ToString())
}
if ($NoMetadata) {
    $arguments += "--no-metadata"
}
if ($SkipStress) {
    $arguments += "--skip-stress"
}
if ($SkipBroker) {
    $arguments += "--skip-broker"
}

Push-Location $projectRoot
try {
    # Native tools may use stderr for progress. Windows PowerShell wraps those
    # lines as non-terminating errors, so only the process exit code is fatal.
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $python @arguments 2>&1 | Tee-Object -FilePath $logPath
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "Local run log: $logPath"
if (-not $NoOpen -and (Test-Path -LiteralPath $statusPath)) {
    Start-Process -FilePath $statusPath
}
exit $exitCode
