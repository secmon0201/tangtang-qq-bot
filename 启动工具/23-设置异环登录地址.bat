@echo off
chcp 65001 >nul
setlocal
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
cd /d "%ROOT%"

if "%~1"=="" (
    echo 用法: 23-设置异环登录地址.bat "https://公网地址" [-WrapTencent]
    echo 示例: 23-设置异环登录地址.bat "https://xxx.trycloudflare.com"
    pause
    exit /b 2
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%\scripts\set_nte_login_url.ps1" %*
set "exitCode=%ERRORLEVEL%"
echo.
if not "%exitCode%"=="0" echo 设置失败，请查看上面的提示。
pause
exit /b %exitCode%
