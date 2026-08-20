@echo off
chcp 65001 >nul
setlocal
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
cd /d "%ROOT%"

echo Starting or refreshing the NTE login tunnel...
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\start_nte_tunnel.ps1" %*
set "exitCode=%ERRORLEVEL%"
echo.
if not "%exitCode%"=="0" echo Start or refresh failed. Check the output above.
pause
exit /b %exitCode%
