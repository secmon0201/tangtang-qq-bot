@echo off
chcp 65001 >nul
setlocal
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
cd /d "%ROOT%"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\stop_watchdog.ps1"
pause
