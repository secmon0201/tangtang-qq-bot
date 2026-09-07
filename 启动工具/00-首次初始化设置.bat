@echo off
setlocal
cd /d "%~dp0.."
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%CD%\scripts\initialize_local_config.ps1"
if errorlevel 1 (
  echo.
  echo 初始化失败，请查看上方错误。
  pause
  exit /b 1
)
echo.
echo 本地配置已准备好。填写完成后再启动机器人。
pause
