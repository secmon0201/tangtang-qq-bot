@echo off
chcp 65001 >nul
setlocal
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
cd /d "%ROOT%"

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\start_all.ps1"
set "exitCode=%ERRORLEVEL%"
echo.
if not "%exitCode%"=="0" echo SnowLuma full startup failed. Review the message above.
if not "%exitCode%"=="0" goto :end

echo SnowLuma, NoneBot, OneBot, watchdog, Core, and web tunnels are ready.
:end
pause
exit /b %exitCode%
