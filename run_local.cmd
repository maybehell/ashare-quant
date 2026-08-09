@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set "PAUSE_AFTER=1"
for %%A in (%*) do if /I "%%~A"=="-NoPause" set "PAUSE_AFTER=0"
powershell.exe -NoProfile -File "%~dp0scripts\local_run.ps1" -Refresh %*
set EXIT_CODE=%ERRORLEVEL%
echo.
if not "%EXIT_CODE%"=="0" echo Local run returned exit code %EXIT_CODE%.
if "%PAUSE_AFTER%"=="1" pause
exit /b %EXIT_CODE%
