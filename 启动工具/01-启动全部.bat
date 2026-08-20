@echo off
chcp 65001 >nul
setlocal
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
cd /d "%ROOT%"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\start_all.ps1"
set "exitCode=%ERRORLEVEL%"
echo.
if not "%exitCode%"=="0" echo Startup reported an error. Review the message above.
if not "%exitCode%"=="0" goto :end

if exist "%ROOT%\data\nte_tunnel_disabled.flag" (
    echo NTE login tunnel is intentionally disabled; skipping automatic start.
) else (
    start "NTE Login Tunnel" powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\start_nte_tunnel.ps1"
)
start "QQ Bot Watchdog" powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\watch_napcat.ps1"
echo NapCat watchdog started in a separate console window. Use 32-关闭守护程序.bat or 03-关闭全部.bat to close it.
:end
pause
exit /b %exitCode%
