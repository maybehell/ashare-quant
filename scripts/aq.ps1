param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$fallbackPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

if (Test-Path $venvPython) {
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $venvPython -c "import pandas" 2>$null
    $ErrorActionPreference = $previousErrorActionPreference
    if ($LASTEXITCODE -eq 0) {
        & $venvPython -m ashare_quant @Args
        exit $LASTEXITCODE
    }
}

if (Test-Path $fallbackPython) {
    & $fallbackPython -m ashare_quant @Args
} else {
    throw "No usable Python found. Run scripts\setup_env.ps1 first."
}
