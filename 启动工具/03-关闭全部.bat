@echo off
chcp 65001 >nul
setlocal
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
cd /d "%ROOT%"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\stop_all.ps1"
set "exitCode=%ERRORLEVEL%"
echo.
if not "%exitCode%"=="0" echo Shutdown reported an error. Review the message above.
pause
exit /b %exitCode%
