param(
    [string]$EntryName = "AshareQuantHistoryBackfill.cmd",
    [string]$Config = "configs/extended_history_validation.yaml",
    [string]$OutputDir = "outputs_extended_history_validation",
    [int]$MaxSymbols = 800,
    [int]$IntervalMinutes = 60,
    [int]$MaxRuntimeMinutes = 30,
    [switch]$Register,
    [switch]$ConfirmReplace,
    [switch]$StartNow,
    [int]$VerifySeconds = 15
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$daemon = Join-Path $PSScriptRoot "history_backfill_daemon.ps1"
if (-not (Test-Path -LiteralPath $daemon)) { throw "Missing daemon: $daemon" }
if ($IntervalMinutes -lt 15) { throw "IntervalMinutes must be at least 15." }
if ($MaxRuntimeMinutes -lt 5) { throw "MaxRuntimeMinutes must be at least 5." }

function Resolve-ProjectPath([string]$Value) {
    if ([IO.Path]::IsPathRooted($Value)) { return [IO.Path]::GetFullPath($Value) }
    return [IO.Path]::GetFullPath((Join-Path $projectRoot $Value))
}
$resolvedConfig = Resolve-ProjectPath $Config
$resolvedOutput = Resolve-ProjectPath $OutputDir
if (-not (Test-Path -LiteralPath $resolvedConfig)) { throw "Missing config: $resolvedConfig" }
$startup = [Environment]::GetFolderPath([Environment+SpecialFolder]::Startup)
if (-not $startup) { throw "Current user Startup folder is unavailable." }
$target = Join-Path $startup $EntryName
$parts = @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
    "-File", ('"' + $daemon + '"'),
    "-Config", ('"' + $resolvedConfig + '"'),
    "-OutputDir", ('"' + $resolvedOutput + '"'),
    "-MaxSymbols", $MaxSymbols,
    "-IntervalMinutes", $IntervalMinutes,
    "-MaxRuntimeMinutes", $MaxRuntimeMinutes
)
$argumentLine = $parts -join " "
$launcher = "@echo off" + [Environment]::NewLine +
    "start " + [char]34 + [char]34 + " /min powershell.exe $argumentLine" + [Environment]::NewLine +
    "exit /b 0" + [Environment]::NewLine

Write-Host "Startup entry: $target"
Write-Host "Mode: research_history_backfill"
Write-Host "Interval: $IntervalMinutes minutes; max batch runtime: $MaxRuntimeMinutes minutes"
Write-Host "The daemon exits automatically after progress reaches complete."
if (-not $Register) {
    Write-Host "Preview only. Re-run with -Register to install the current-user Startup entry."
    exit 0
}
if ((Test-Path -LiteralPath $target) -and -not $ConfirmReplace) {
    throw "Startup entry already exists. Use -ConfirmReplace to replace it."
}
[IO.File]::WriteAllText($target, $launcher, [Text.Encoding]::ASCII)
if ((Get-Content -LiteralPath $target -Raw -Encoding ASCII) -ne $launcher) {
    throw "Startup entry verification failed: $target"
}
Write-Host "Startup entry installed: $target"
if ($StartNow) {
    Start-Process powershell.exe -ArgumentList $argumentLine -WindowStyle Hidden
    $statusPath = Join-Path $resolvedOutput "history_backfill_daemon_status.json"
    $deadline = (Get-Date).AddSeconds($VerifySeconds)
    $verified = $false
    do {
        Start-Sleep -Milliseconds 250
        try {
            $status = Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $age = (Get-Date) - [DateTimeOffset]::Parse([string]$status.updated_at).LocalDateTime
            $process = Get-Process -Id ([int]$status.pid) -ErrorAction SilentlyContinue
            $verified = [string]$status.mode -eq "research_history_backfill" -and $age.TotalSeconds -le $VerifySeconds -and $null -ne $process
        } catch {
            $verified = $false
        }
    } while (-not $verified -and (Get-Date) -lt $deadline)
    if (-not $verified) { throw "History backfill daemon did not publish a fresh live status." }
    Write-Host "History backfill daemon started and verified (PID $($status.pid))."
}
