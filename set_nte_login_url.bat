@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if "%~1"=="" (
    echo 用法: set_nte_login_url.bat "https://公网地址" [-WrapTencent]
    echo 示例: set_nte_login_url.bat "https://xxx.trycloudflare.com"
    pause
    exit /b 2
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\set_nte_login_url.ps1" %*
set "exitCode=%ERRORLEVEL%"
echo.
if not "%exitCode%"=="0" echo 设置失败，请查看上面的提示。
pause
exit /b %exitCode%
