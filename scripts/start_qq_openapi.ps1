[CmdletBinding()]
param()

$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

# This process is intentionally separate from the personal-account OneBot bot.
# It overrides only the transport for this PowerShell session.
$env:BOT_TRANSPORT = "qq_openapi"
$Python = Join-Path $Root ".venv\Scripts\python.exe"

& $Python "scripts\validate_qq_config.py" --env (Join-Path $Root ".env")
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

& $Python -u -m bot
