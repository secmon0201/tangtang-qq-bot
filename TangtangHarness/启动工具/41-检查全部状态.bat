@echo off
chcp 65001 >nul
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\stack.ps1" -Action status
set "result=%errorlevel%"
echo.
pause
exit /b %result%
