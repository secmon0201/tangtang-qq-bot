Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Virtual environment not found.' }

& $python (Join-Path $PSScriptRoot 'validate_qq_config.py') '--env' (Join-Path $root '.env')
if ($LASTEXITCODE -ne 0) { throw 'QQ configuration validation failed.' }
& (Join-Path $PSScriptRoot 'backup.ps1')
if ($LASTEXITCODE -ne 0) { throw 'SQLite backup failed; transport was not switched.' }
& (Join-Path $PSScriptRoot 'configure_lagrange_onebot.ps1')

$envPath = Join-Path $root '.env'
$envLines = [System.Collections.Generic.List[string]](Get-Content -LiteralPath $envPath -Encoding utf8)
$transportLine = 'QQ_PLATFORM_TRANSPORT=lagrange'
$matchedTransport = $false
for ($index = 0; $index -lt $envLines.Count; $index++) {
    if ($envLines[$index] -match '^\s*QQ_PLATFORM_TRANSPORT\s*=') {
        $envLines[$index] = $transportLine
        $matchedTransport = $true
    }
}
if (-not $matchedTransport) { $envLines.Add($transportLine) }
$envLines | Set-Content -LiteralPath $envPath -Encoding utf8

# The NoneBot reverse-WS listener stays up; only the verified current transport stops.
& (Join-Path $PSScriptRoot 'stop_napcat_transport.ps1')
& (Join-Path $PSScriptRoot 'start_lagrange_onebot.ps1')
Write-Output 'Transport handoff started. Run the QQ platform smoke command after Lagrange is online.'
