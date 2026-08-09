param(
    [string]$Python = (Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"),
    [string]$Venv = ".venv"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $Python)) {
    throw "Python executable not found: $Python"
}

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$env:TEMP = Join-Path $projectRoot "pip-tmp"
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = Join-Path $projectRoot "pip-cache"
New-Item -ItemType Directory -Force -Path $env:TEMP, $env:PIP_CACHE_DIR | Out-Null

if (Test-Path $Venv) {
    $existingPython = Join-Path $projectRoot "$Venv\Scripts\python.exe"
    if (-not (Test-Path $existingPython)) {
        Remove-Item -LiteralPath $Venv -Recurse -Force
    } else {
        $previousErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        & $existingPython -c "import pip" 2>$null
        $ErrorActionPreference = $previousErrorActionPreference
        if ($LASTEXITCODE -ne 0) {
            Remove-Item -LiteralPath $Venv -Recurse -Force
        }
    }
}

if (-not (Test-Path $Venv)) {
    & $Python -m venv $Venv
}

$venvPython = Join-Path $projectRoot "$Venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    throw "Virtual environment Python not found: $venvPython"
}

& $venvPython -m ensurepip --upgrade
& $venvPython -m pip install --upgrade pip setuptools wheel
& $venvPython -m pip install -r requirements.txt
& $venvPython -m pip install -e .

Write-Host "Environment ready."
Write-Host "Use: .\$Venv\Scripts\python.exe -m ashare_quant doctor"
