@echo off
chcp 65001 >nul
setlocal
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
cd /d "%ROOT%"

echo [1/3] Stopping the bot, watchdog, and verified NapCat QQ...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\stop_all.ps1"
if errorlevel 1 goto :failed

timeout /t 2 /nobreak >nul

echo [2/3] Starting the bot and NapCat QQ...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\start_all.ps1"
if errorlevel 1 goto :failed

echo [3/3] Starting the NapCat watchdog...
if exist "%ROOT%\data\nte_tunnel_disabled.flag" (
    echo NTE login tunnel is intentionally disabled; skipping automatic start.
) else (
    start "NTE Login Tunnel" powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\start_nte_tunnel.ps1"
)
start "QQ Bot Watchdog" powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\watch_napcat.ps1"
echo Restart completed. Complete any QQ login verification manually if prompted.
pause
exit /b 0

:failed
echo.
echo Restart reported an error. Review the message above.
pause
exit /b 1
