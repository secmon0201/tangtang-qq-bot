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

echo All configured components were requested through the unified startup script.
:end
pause
exit /b %exitCode%
