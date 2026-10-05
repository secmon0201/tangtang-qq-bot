@echo off
chcp 65001 >nul
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\scripts\stack.ps1" -Action open-console
set "result=%errorlevel%"
if not "%result%"=="0" pause
exit /b %result%
