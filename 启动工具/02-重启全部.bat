@echo off
chcp 65001 >nul
setlocal
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
cd /d "%ROOT%"

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\restart_all.ps1"
if errorlevel 1 goto :failed

echo SnowLuma full-stack restart completed and passed health verification.
pause
exit /b 0

:failed
echo.
echo SnowLuma full-stack restart failed. Review the message above.
pause
exit /b 1
