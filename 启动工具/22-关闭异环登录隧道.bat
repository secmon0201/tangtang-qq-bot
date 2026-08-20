@echo off
chcp 65001 >nul
setlocal
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
cd /d "%ROOT%"

echo 正在关闭异环登录隧道和本地代理...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\stop_nte_tunnel.ps1" %*
set "exitCode=%ERRORLEVEL%"
echo.
if not "%exitCode%"=="0" echo 关闭失败，请查看上面的提示。
pause
exit /b %exitCode%
