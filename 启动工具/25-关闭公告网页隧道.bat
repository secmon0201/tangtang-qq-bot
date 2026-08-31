@echo off
setlocal
set ROOT=%~dp0..
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\stop_global_announcement_tunnel.ps1"
pause
