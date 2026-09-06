@echo off
chcp 65001 >nul
setlocal
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
cd /d "%ROOT%"

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\stop_all.ps1"
set "exitCode=%ERRORLEVEL%"
echo.
if not "%exitCode%"=="0" echo SnowLuma full shutdown failed. Review the message above.
if "%exitCode%"=="0" echo SnowLuma bot stack is fully stopped. QQ login state and bot data were preserved.
pause
exit /b %exitCode%
