@echo off
chcp 65001 >nul
setlocal
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
cd /d "%ROOT%"
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%ROOT%\scripts\stop_speech.ps1"
if errorlevel 1 (
    echo Failed to stop local GPT-SoVITS speech.
    pause
    exit /b 1
)
echo Speech disabled and stopped. Background recovery is paused. NoneBot and QQ remain running.
pause
exit /b 0
