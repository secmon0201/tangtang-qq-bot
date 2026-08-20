@echo off
setlocal
cd /d "%~dp0"

echo Starting or refreshing the NTE login tunnel...
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_nte_tunnel.ps1" %*
set "exitCode=%ERRORLEVEL%"
echo.
if not "%exitCode%"=="0" echo Start or refresh failed. Check the output above.
pause
exit /b %exitCode%
