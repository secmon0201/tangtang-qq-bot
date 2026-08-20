Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $root '.env'
if (-not (Test-Path -LiteralPath $envPath)) { throw "Missing configuration file: $envPath" }

$values = @{}
foreach ($line in Get-Content -LiteralPath $envPath -Encoding utf8) {
    $entry = $line.Trim()
    if (-not $entry -or $entry.StartsWith('#') -or -not $entry.Contains('=')) { continue }
    $key, $value = $entry.Split('=', 2)
    $value = $value.Trim()
    if ($value.Length -ge 2 -and $value[0] -eq $value[-1] -and $value[0] -in @('"', "'")) {
        $value = $value.Substring(1, $value.Length - 2)
    }
    $values[$key.Trim()] = $value
}

$accountId = [string]$values['NAPCAT_QQ_ID']
if ($accountId -notmatch '^\d{5,12}$') { throw 'NAPCAT_QQ_ID must be a 5-12 digit QQ number.' }
$port = if ([string]$values['PORT'] -match '^\d+$') { [int]$values['PORT'] } else { 8080 }
if ($port -lt 1 -or $port -gt 65535) { throw 'PORT must be between 1 and 65535.' }
$lagrangeDir = [string]$values['LAGRANGE_DIR']
if (-not $lagrangeDir) { $lagrangeDir = 'Lagrange.OneBot' }
if (-not [IO.Path]::IsPathRooted($lagrangeDir)) { $lagrangeDir = Join-Path $root $lagrangeDir }
$lagrangeDir = [IO.Path]::GetFullPath($lagrangeDir)
$exe = Get-ChildItem -LiteralPath $lagrangeDir -Filter 'Lagrange.OneBot.exe' -Recurse -File -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -eq $exe) { throw "Lagrange.OneBot.exe not found under: $lagrangeDir" }

$config = [ordered]@{
    '$schema' = 'https://raw.githubusercontent.com/LagrangeDev/Lagrange.Core/v1/Lagrange.OneBot/Resources/appsettings_schema.json'
    Logging = [ordered]@{ LogLevel = [ordered]@{ Default = 'Information'; Microsoft = 'Warning'; 'Microsoft.Hosting.Lifetime' = 'Information' } }
    SignServerUrl = ''
    SignProxyUrl = ''
    MusicSignServerUrl = ''
    Account = [ordered]@{ Uin = [int64]$accountId; Protocol = 'Linux'; AutoReconnect = $true; GetOptimumServer = $true }
    Message = [ordered]@{ IgnoreSelf = $true; StringPost = $false }
    QrCode = [ordered]@{ ConsoleCompatibilityMode = $false }
    Implementations = @([ordered]@{ Type = 'ReverseWebSocket'; Host = '127.0.0.1'; Port = $port; Suffix = '/onebot/v11/ws'; ReconnectInterval = 5000; HeartBeatInterval = 5000; AccessToken = [string]$values['ONEBOT_ACCESS_TOKEN'] })
}
$configPath = Join-Path $exe.DirectoryName 'appsettings.json'
$backupPath = "$configPath.before-qq-platform-migration"
if ((Test-Path -LiteralPath $configPath) -and -not (Test-Path -LiteralPath $backupPath)) {
    Copy-Item -LiteralPath $configPath -Destination $backupPath -ErrorAction Stop
}
$config | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $configPath -Encoding utf8
Write-Output "Configured Lagrange reverse WebSocket at ws://127.0.0.1:$port/onebot/v11/ws"
