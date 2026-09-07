param(
    [switch]$NoOpen
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$TemplatePath = Join-Path $Root '.env.example'
$LocalPath = Join-Path $Root '.env'

if (-not (Test-Path -LiteralPath $TemplatePath -PathType Leaf)) {
    throw "Configuration template not found: $TemplatePath"
}

if (Test-Path -LiteralPath $LocalPath -PathType Leaf) {
    Write-Output 'Local .env already exists; it was not overwritten.'
}
else {
    Copy-Item -LiteralPath $TemplatePath -Destination $LocalPath -ErrorAction Stop
    Write-Output 'Created local .env from the public empty template.'
}

Write-Output 'Fill the required identity and connection fields described in docs\运维-首次初始化与隐私配置.md.'
Write-Output 'The .env file is ignored by Git and must remain local.'
if (-not $NoOpen) {
    Start-Process -FilePath 'notepad.exe' -ArgumentList @($LocalPath) -WindowStyle Normal
}
