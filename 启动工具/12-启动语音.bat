@echo off
chcp 65001 >nul
setlocal
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
cd /d "%ROOT%"
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%ROOT%\scripts\start_speech.ps1" -Enable
if errorlevel 1 (
    echo Failed to start local GPT-SoVITS speech.
    pause
    exit /b 1
)
echo Local GPT-SoVITS speech is running. Readiness is verified by the bot background worker.
pause
exit /b 0
