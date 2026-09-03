param(
    [Parameter(Mandatory)]
    [string]$PublicBaseUrl
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$ConfigPath = Join-Path $Root 'GsUID.Core\data\XutheringWavesUID\config.json'

$url = $PublicBaseUrl.Trim()
if ($url -notmatch '^https?://[^\s]+$') {
    throw 'PublicBaseUrl must start with http:// or https://'
}
$url = $url.TrimEnd('/')

if (-not (Test-Path -LiteralPath $ConfigPath)) {
    throw "XutheringWavesUID config not found: $ConfigPath"
}

$config = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($null -eq $config.WavesLoginUrl) {
    throw 'WavesLoginUrl key is missing from XutheringWavesUID config'
}
if ($null -eq $config.WavesLoginUrlSelf) {
    throw 'WavesLoginUrlSelf key is missing from XutheringWavesUID config'
}

$config.WavesLoginUrl.data = $url
$config.WavesLoginUrlSelf.data = $true

$json = $config | ConvertTo-Json -Depth 20
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($ConfigPath, $json, $utf8NoBom)

Write-Output "WavesLoginUrl set to: $url"
Write-Output "WavesLoginUrlSelf: $($config.WavesLoginUrlSelf.data)"
Write-Output 'Only the approved /waves login routes may be forwarded to 127.0.0.1:8765.'
