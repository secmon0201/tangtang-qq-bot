param(
    [bool]$EnableOneBot = $true
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'qq_transport.ps1')
$settings = Get-QqTransportSettings -Root $root
$snowLumaDir = $settings.SnowLumaDir
if (-not (Test-Path -LiteralPath (Join-Path $snowLumaDir 'index.mjs'))) {
    throw "SnowLuma is not installed under: $snowLumaDir"
}

$configDir = Join-Path $snowLumaDir 'config'
New-Item -ItemType Directory -Force -Path $configDir | Out-Null
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

$runtimePath = Join-Path $configDir 'runtime.json'
if (Test-Path -LiteralPath $runtimePath) {
    try { $runtime = Get-Content -LiteralPath $runtimePath -Raw -Encoding utf8 | ConvertFrom-Json } catch { throw "Invalid SnowLuma runtime config: $runtimePath" }
    $runtimeBackup = "$runtimePath.before-bot-migration"
    if (-not (Test-Path -LiteralPath $runtimeBackup)) {
        Copy-Item -LiteralPath $runtimePath -Destination $runtimeBackup -ErrorAction Stop
    }
} else {
    $runtime = [pscustomobject]@{}
}
$runtime | Add-Member -NotePropertyName 'webuiPort' -NotePropertyValue $settings.SnowLumaWebUiPort -Force
$runtime | Add-Member -NotePropertyName 'webuiHost' -NotePropertyValue '127.0.0.1' -Force
[IO.File]::WriteAllText($runtimePath, ($runtime | ConvertTo-Json -Depth 10), $utf8NoBom)

$oneBotPath = Join-Path $configDir ("onebot_{0}.json" -f $settings.AccountId)
if (Test-Path -LiteralPath $oneBotPath) {
    $oneBotBackup = "$oneBotPath.before-bot-migration"
    if (-not (Test-Path -LiteralPath $oneBotBackup)) {
        Copy-Item -LiteralPath $oneBotPath -Destination $oneBotBackup -ErrorAction Stop
    }
}
$oneBotConfig = [ordered]@{
    mode = 'snapshot'
    networks = [ordered]@{
        httpServers = @()
        httpClients = @()
        wsServers = @()
        wsClients = @([ordered]@{
            name = 'nonebot-reverse-ws'
            enabled = $EnableOneBot
            url = $settings.OneBotUrl
            role = 'Universal'
            reconnectIntervalMs = 5000
            accessToken = $settings.OneBotAccessToken
            messageFormat = 'array'
            reportSelfMessage = $false
        })
    }
    statusCommand = [ordered]@{
        enabled = $false
        swallow = $false
        cooldownSeconds = 5
        trigger = '#sl'
    }
    historySync = [ordered]@{ enabled = $false }
    notifications = [ordered]@{ channelIds = @() }
}
[IO.File]::WriteAllText($oneBotPath, ($oneBotConfig | ConvertTo-Json -Depth 10), $utf8NoBom)
$mode = if ($EnableOneBot) { 'enabled' } else { 'disabled for pre-cutover onboarding' }
Write-Output "SnowLuma configured: WebUI=http://127.0.0.1:$($settings.SnowLumaWebUiPort); OneBot client=$mode."
