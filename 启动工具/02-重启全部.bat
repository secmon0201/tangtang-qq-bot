@echo off
chcp 65001 >nul
setlocal
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
cd /d "%ROOT%"

echo [1/3] Stopping the bot, watchdog, and verified QQ transport...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\stop_all.ps1"
if errorlevel 1 goto :failed

timeout /t 2 /nobreak >nul

echo [2/3] Starting the bot and configured QQ transport...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\start_all.ps1"
if errorlevel 1 goto :failed

echo [3/3] Startup script restored the QQ transport watchdog and configured tunnels.
echo Restart completed. Complete any QQ login verification manually if prompted.
pause
exit /b 0

:failed
echo.
echo Restart reported an error. Review the message above.
pause
exit /b 1
