@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%SCRIPT_DIR%daily_observation.ps1" -Config "%SCRIPT_DIR%..\configs\default.yaml" -AlertConfig "%SCRIPT_DIR%..\configs\huatai_desktop_canary.yaml" -MaxSymbols 800 -MaxRuntimeHours 2
exit /b %ERRORLEVEL%
