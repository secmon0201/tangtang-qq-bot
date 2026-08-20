Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
& (Join-Path $PSScriptRoot 'stop_lagrange_onebot.ps1')
$envPath = Join-Path $root '.env'
$envLines = [System.Collections.Generic.List[string]](Get-Content -LiteralPath $envPath -Encoding utf8)
$transportLine = 'QQ_PLATFORM_TRANSPORT=napcat'
$matchedTransport = $false
for ($index = 0; $index -lt $envLines.Count; $index++) {
    if ($envLines[$index] -match '^\s*QQ_PLATFORM_TRANSPORT\s*=') {
        $envLines[$index] = $transportLine
        $matchedTransport = $true
    }
}
if (-not $matchedTransport) { $envLines.Add($transportLine) }
$envLines | Set-Content -LiteralPath $envPath -Encoding utf8
# start_all keeps an already-running NoneBot process and starts only the configured NapCat session.
& (Join-Path $PSScriptRoot 'start_all.ps1')
Write-Output 'Rollback to NapCat was requested. Verify the OneBot connection in the bot log.'
