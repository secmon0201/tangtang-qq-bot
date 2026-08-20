param(
    [Parameter(Mandatory)]
    [string]$PublicBaseUrl,
    [switch]$WrapTencent
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$ConfigPath = Join-Path $Root "GsUID.Core\data\NTEUID\config.json"

$url = $PublicBaseUrl.Trim()
if ($url -notmatch '^https?://[^\s]+$') {
    throw "PublicBaseUrl must start with http:// or https://"
}
$url = $url.TrimEnd('/')

if (-not (Test-Path -LiteralPath $ConfigPath)) {
    throw "NTEUID config not found: $ConfigPath"
}

$config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($null -eq $config.NTELoginUrl) {
    throw "NTELoginUrl key is missing from NTEUID config"
}

$config.NTELoginUrl.data = $url
if ($WrapTencent) {
    if ($null -eq $config.NTETencentWord) {
        throw "NTETencentWord key is missing from NTEUID config"
    }
    $config.NTETencentWord.data = $true
}

$json = $config | ConvertTo-Json -Depth 20
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($ConfigPath, $json, $Utf8NoBom)

Write-Output "NTELoginUrl set to: $url"
Write-Output "NTETencentWord: $($config.NTETencentWord.data)"
Write-Output ""
Write-Output "Before restarting Core, make sure the public endpoint forwards ONLY /nte/*"
Write-Output "to 127.0.0.1:8765 and blocks /ws/* and /api/send_msg."
Write-Output "Then run:"
Write-Output "  .\scripts\stop_gsuid_core.ps1"
Write-Output "  .\scripts\start_gsuid_core.ps1"
Write-Output "And verify with: #nte登录"
