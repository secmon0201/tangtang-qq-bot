@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_all.ps1"
set "exitCode=%ERRORLEVEL%"
echo.
if not "%exitCode%"=="0" echo Startup reported an error. Review the message above.
if not "%exitCode%"=="0" goto :end

if exist "%~dp0data\nte_tunnel_disabled.flag" (
    echo NTE login tunnel is intentionally disabled; skipping automatic start.
) else (
    start "NTE Login Tunnel" powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_nte_tunnel.ps1"
)
start "QQ Bot Watchdog" powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\watch_napcat.ps1"
echo NapCat watchdog started in a separate console window. Use stop_watchdog.bat or stop_bot_and_napcat.bat to close it.
:end
pause
exit /b %exitCode%
