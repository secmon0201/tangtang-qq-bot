param(
    [string]$InstallDir = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'qq_transport.ps1')
$settings = Get-QqTransportSettings -Root $root
$lockPath = Join-Path $root 'config\snowluma-lock.json'
$lock = Get-Content -LiteralPath $lockPath -Raw -Encoding utf8 | ConvertFrom-Json
$destination = if ($InstallDir) { Resolve-BotLocalPath -Root $root -Value $InstallDir } else { $settings.SnowLumaDir }
$entryPoint = Join-Path $destination 'index.mjs'
$manifestPath = Join-Path $destination 'package.json'
if ((Test-Path -LiteralPath $entryPoint) -and (Test-Path -LiteralPath $manifestPath)) {
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding utf8 | ConvertFrom-Json
    if ([string]$manifest.version -eq ([string]$lock.version).TrimStart('v')) {
        Write-Output "SnowLuma $($lock.version) is already installed under: $destination"
        exit 0
    }
    throw "A different SnowLuma runtime already exists under: $destination"
}
if (Test-Path -LiteralPath $destination) {
    throw "Install destination already exists and is not an intact pinned SnowLuma runtime: $destination"
}

$downloadDir = Join-Path $root 'downloads'
New-Item -ItemType Directory -Force -Path $downloadDir | Out-Null
$archive = Join-Path $downloadDir ([string]$lock.asset)
$expectedSize = [int64]$lock.asset_size
if (-not (Test-Path -LiteralPath $archive) -or (Get-Item -LiteralPath $archive).Length -ne $expectedSize) {
    $curl = (Get-Command 'curl.exe' -ErrorAction Stop).Source
    $curlArgs = @('-L', '--fail', '--retry', '3', '--retry-delay', '2', '--output', $archive, [string]$lock.asset_url)
    & $curl @curlArgs
    $downloadCode = $LASTEXITCODE
    if ($downloadCode -ne 0) { throw "SnowLuma download failed with exit code $downloadCode" }
}
$actualHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualHash -ne ([string]$lock.sha256).ToLowerInvariant()) {
    throw 'SnowLuma archive SHA-256 does not match config/snowluma-lock.json.'
}

$stagingRoot = Join-Path (Join-Path $root 'data') ("snowluma-install-{0}" -f [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $stagingRoot | Out-Null
Expand-Archive -LiteralPath $archive -DestinationPath $stagingRoot -ErrorAction Stop
foreach ($required in @('index.mjs', 'node.exe', 'launcher.bat', 'package.json')) {
    if (-not (Test-Path -LiteralPath (Join-Path $stagingRoot $required))) {
        throw "SnowLuma release is missing required file: $required"
    }
}
Move-Item -LiteralPath $stagingRoot -Destination $destination -ErrorAction Stop
Write-Output "Installed SnowLuma $($lock.version) under: $destination"
Write-Output 'The EULA, privacy agreement, password change and QQ verification remain manual WebUI steps.'
