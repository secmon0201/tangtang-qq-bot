param([Parameter(Mandatory=$true)][string]$ConfigPath, [int]$Port = 8090, [switch]$Apply)
$ErrorActionPreference = 'Stop'
$harnessRoot = Split-Path -Parent $PSScriptRoot
$resolved = (Resolve-Path -LiteralPath $ConfigPath).Path
$config = Get-Content -LiteralPath $resolved -Raw -Encoding utf8 | ConvertFrom-Json
$url = "ws://127.0.0.1:$Port/onebot/v11/ws"
$clients = @($config.networks.wsClients)
$found = @($clients | Where-Object { $_.url -eq $url })
if ($found.Count -gt 0) { Write-Output "Harness 连接已存在：$url，enabled=$($found[0].enabled)"; exit 0 }
Write-Output "缺少独立 Harness 连接：$url；当前保留 $($clients.Count) 条连接。"
if (-not $Apply) { Write-Output '预览完成，使用 -Apply 显式增补，或在 SnowLuma WebUI 添加。'; exit 0 }
$backupDir = Join-Path $harnessRoot 'data\backups'
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
Copy-Item -LiteralPath $resolved -Destination (Join-Path $backupDir ("snowluma-{0}.json" -f (Get-Date -Format 'yyyyMMdd-HHmmss')))
$token = ''
$settingsPath = Join-Path $harnessRoot 'config\settings.json'
if (Test-Path -LiteralPath $settingsPath) { $settings = Get-Content -LiteralPath $settingsPath -Raw -Encoding utf8 | ConvertFrom-Json; $token = $settings.onebot_access_token }
$entry = [pscustomobject]@{name='tangtang-harness';enabled=$true;url=$url;role='Universal';reconnectIntervalMs=5000;accessToken=$token;messageFormat='array';reportSelfMessage=$false}
$config.networks.wsClients = @($clients) + @($entry)
[IO.File]::WriteAllText($resolved, ($config | ConvertTo-Json -Depth 30), (New-Object Text.UTF8Encoding($false)))
Write-Output '已增补独立连接。请在 WebUI 确认连接；旧全栈启动可能覆盖此配置，之后可再次显式检查。'
