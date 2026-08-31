@echo off
setlocal
set ROOT=%~dp0..
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\stop_operator_web_tunnel.ps1"
pause
