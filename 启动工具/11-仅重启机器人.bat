@echo off
chcp 65001 >nul
setlocal
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
cd /d "%ROOT%"

echo [1/2] Stopping NoneBot only...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\stop.ps1"
if errorlevel 1 (
    echo Failed to stop NoneBot.
    pause
    exit /b 1
)

rem Give Uvicorn a moment to release TCP port 8080 before restarting.
timeout /t 2 /nobreak >nul

echo [2/2] Starting NoneBot only...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\start.ps1"
if errorlevel 1 (
    echo Failed to start NoneBot.
    pause
    exit /b 1
)

echo.
echo NoneBot restarted. SnowLuma and QQ were not restarted.
echo Logs: "%ROOT%\logs\bot.out.log"
echo.
pause
exit /b 0
