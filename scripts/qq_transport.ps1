Set-StrictMode -Version Latest

function Read-BotEnvValues {
    param([Parameter(Mandatory)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Missing configuration file: $Path"
    }
    $values = @{}
    foreach ($line in Get-Content -LiteralPath $Path -Encoding utf8) {
        $entry = $line.Trim()
        if (-not $entry -or $entry.StartsWith('#') -or -not $entry.Contains('=')) { continue }
        $key, $value = $entry.Split('=', 2)
        $value = $value.Trim()
        if ($value.Length -ge 2 -and $value[0] -eq $value[-1] -and $value[0] -in @('"', "'")) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        $values[$key.Trim()] = $value
    }
    return $values
}

function Resolve-BotLocalPath {
    param(
        [Parameter(Mandatory)][string]$Root,
        [Parameter(Mandatory)][string]$Value
    )

    $candidate = $Value
    if (-not [IO.Path]::IsPathRooted($candidate)) { $candidate = Join-Path $Root $candidate }
    return [IO.Path]::GetFullPath($candidate)
}

function Get-QqTransportSettings {
    param([Parameter(Mandatory)][string]$Root)

    $rootPath = [IO.Path]::GetFullPath($Root)
    $values = Read-BotEnvValues -Path (Join-Path $rootPath '.env')
    $transport = [string]$values['QQ_PLATFORM_TRANSPORT']
    if (-not $transport) {
        $hasLegacyNapCatSettings = [string]$values['NAPCAT_QQ_ID'] -or [string]$values['NAPCAT_DIR']
        $transport = if ($hasLegacyNapCatSettings) { 'napcat' } else { 'snowluma' }
    }
    $transport = $transport.Trim().ToLowerInvariant()
    if ($transport -notin @('snowluma', 'napcat', 'lagrange')) {
        throw 'QQ_PLATFORM_TRANSPORT must be snowluma, napcat or lagrange.'
    }

    $accountId = [string]$values['QQ_ACCOUNT_ID']
    if (-not $accountId) { $accountId = [string]$values['NAPCAT_QQ_ID'] }
    if ($accountId -notmatch '^\d{5,12}$') {
        throw 'QQ_ACCOUNT_ID must be a 5-12 digit QQ number.'
    }
    $port = 8080
    if ([string]$values['PORT'] -match '^\d+$') { $port = [int]$values['PORT'] }
    if ($port -lt 1 -or $port -gt 65535) { throw 'PORT must be between 1 and 65535.' }
    $webUiPort = 5099
    if ([string]$values['SNOWLUMA_WEBUI_PORT'] -match '^\d+$') {
        $webUiPort = [int]$values['SNOWLUMA_WEBUI_PORT']
    }
    if ($webUiPort -lt 1 -or $webUiPort -gt 65535) {
        throw 'SNOWLUMA_WEBUI_PORT must be between 1 and 65535.'
    }
    if ($webUiPort -eq $port) { throw 'SNOWLUMA_WEBUI_PORT must differ from PORT.' }

    $snowLumaDir = [string]$values['SNOWLUMA_DIR']
    if (-not $snowLumaDir) { $snowLumaDir = 'SnowLuma' }
    $lagrangeDir = [string]$values['LAGRANGE_DIR']
    if (-not $lagrangeDir) { $lagrangeDir = 'Lagrange.OneBot' }
    $napCatDir = [string]$values['NAPCAT_DIR']
    if (-not $napCatDir) { $napCatDir = 'NapCat.Shell' }

    return [pscustomobject]@{
        Root = $rootPath
        EnvPath = Join-Path $rootPath '.env'
        Values = $values
        Transport = $transport
        AccountId = $accountId
        Port = $port
        OneBotUrl = "ws://127.0.0.1:$port/onebot/v11/ws"
        OneBotAccessToken = [string]$values['ONEBOT_ACCESS_TOKEN']
        SnowLumaDir = Resolve-BotLocalPath -Root $rootPath -Value $snowLumaDir
        SnowLumaWebUiPort = $webUiPort
        LagrangeDir = Resolve-BotLocalPath -Root $rootPath -Value $lagrangeDir
        NapCatDir = Resolve-BotLocalPath -Root $rootPath -Value $napCatDir
        StatePath = Join-Path $rootPath ("data\qq-transport-{0}-state.json" -f $transport)
    }
}

function Get-OneBotClientConnections {
    param([Parameter(Mandatory)][int]$Port)

    try {
        return @(Get-NetTCPConnection -State Established -ErrorAction Stop | Where-Object {
            [int]$_.RemotePort -eq $Port -and
            $_.RemoteAddress -in @('127.0.0.1', '::1', '::ffff:127.0.0.1')
        })
    } catch {
        return @()
    }
}

function Get-QqProcessSnapshot {
    return @(Get-CimInstance Win32_Process -Filter "Name = 'QQ.exe'" -ErrorAction SilentlyContinue)
}

function Get-ProcessCreationTicks {
    param([Parameter(Mandatory)][object]$Process)

    try { return ([datetime]$Process.CreationDate).ToUniversalTime().Ticks } catch { return 0L }
}

function Get-QqTransportProcessId {
    param([Parameter(Mandatory)][object]$Process)

    if ($Process.PSObject.Properties.Name -contains 'ProcessId') {
        return [int]$Process.ProcessId
    }
    if ($Process.PSObject.Properties.Name -contains 'Pid') {
        return [int]$Process.Pid
    }
    throw 'QQ transport process object does not contain a process ID.'
}

function Get-TrackedTransportProcess {
    param([Parameter(Mandatory)][object]$Settings)

    if (-not (Test-Path -LiteralPath $Settings.StatePath)) { return $null }
    try {
        $state = Get-Content -LiteralPath $Settings.StatePath -Raw -Encoding utf8 | ConvertFrom-Json
    } catch {
        return $null
    }
    if ([string]$state.transport -ne [string]$Settings.Transport -or -not $state.pid) { return $null }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$state.pid)" -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $null }
    if ($state.PSObject.Properties.Name -contains 'creation_ticks') {
        $ticks = Get-ProcessCreationTicks -Process $process
        if ($ticks -le 0 -or $ticks -ne [int64]$state.creation_ticks) { return $null }
    }
    if ($state.PSObject.Properties.Name -contains 'executable' -and [string]$state.executable) {
        if (-not $process.ExecutablePath) { return $null }
        if ([IO.Path]::GetFullPath([string]$process.ExecutablePath) -ne [IO.Path]::GetFullPath([string]$state.executable)) {
            return $null
        }
    }
    return $process
}

function Save-QqTransportProcessState {
    param(
        [Parameter(Mandatory)][object]$Settings,
        [Parameter(Mandatory)][object]$Process,
        [Parameter(Mandatory)][string]$Executable
    )

    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Settings.StatePath) | Out-Null
    [pscustomobject]@{
        transport = [string]$Settings.Transport
        pid = Get-QqTransportProcessId -Process $Process
        creation_ticks = Get-ProcessCreationTicks -Process $Process
        executable = [IO.Path]::GetFullPath($Executable)
        account_id = [string]$Settings.AccountId
        started_at = [DateTime]::UtcNow.ToString('o')
    } | ConvertTo-Json | Set-Content -LiteralPath $Settings.StatePath -Encoding utf8
}

function Get-ConfiguredTransportProcesses {
    param([Parameter(Mandatory)][object]$Settings)

    $tracked = Get-TrackedTransportProcess -Settings $Settings
    if ($null -ne $tracked) { return @($tracked) }

    if ($Settings.Transport -eq 'snowluma') {
        $nodePath = Join-Path $Settings.SnowLumaDir 'node.exe'
        $nodePattern = [regex]::Escape([IO.Path]::GetFullPath($nodePath))
        return @(Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" -ErrorAction SilentlyContinue | Where-Object {
            $_.ExecutablePath -and [IO.Path]::GetFullPath([string]$_.ExecutablePath) -match "^$nodePattern$" -and
            [string]$_.CommandLine -match '(?i)index\.mjs'
        })
    }
    if ($Settings.Transport -eq 'lagrange') {
        $exe = Get-ChildItem -LiteralPath $Settings.LagrangeDir -Filter 'Lagrange.OneBot.exe' -Recurse -File -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($null -eq $exe) { return @() }
        return @(Get-CimInstance Win32_Process -Filter "Name = 'Lagrange.OneBot.exe'" -ErrorAction SilentlyContinue | Where-Object {
            $_.ExecutablePath -and [IO.Path]::GetFullPath([string]$_.ExecutablePath) -eq $exe.FullName
        })
    }

    . (Join-Path $PSScriptRoot 'napcat_process.ps1')
    $legacySettings = Get-BotLaunchSettings -Root $Settings.Root
    $roots = @(Get-QqRootProcesses)
    $verified = Get-VerifiedStateRoot -Roots $roots -StatePath $legacySettings.StatePath -AccountId $Settings.AccountId
    if ($null -ne $verified) { return @($verified) }
    return @($roots | Where-Object { $_.AccountIds -contains $Settings.AccountId })
}

function Test-OneBotConnectionOwnership {
    param(
        [Parameter(Mandatory)][object]$Settings,
        [Parameter(Mandatory)][object[]]$TransportProcesses,
        [Parameter(Mandatory)][object[]]$Connections
    )

    if ($TransportProcesses.Count -ne 1 -or $Connections.Count -ne 1) { return $false }
    if ($Settings.Transport -eq 'napcat') {
        # NapCat's OneBot socket is owned by a QQ child process, so map it back
        # to the verified QQ root instead of comparing the socket PID directly.
        . (Join-Path $PSScriptRoot 'napcat_process.ps1')
        $connectedRoots = @(Get-QqRootsConnectedToPort -Port $Settings.Port)
        if ($connectedRoots.Count -ne 1) { return $false }
        $transportRootPid = Get-QqTransportProcessId -Process $TransportProcesses[0]
        return [int]$connectedRoots[0].Pid -eq $transportRootPid
    }
    $transportPid = Get-QqTransportProcessId -Process $TransportProcesses[0]
    return [int]$Connections[0].OwningProcess -eq $transportPid
}

function Set-BotEnvValue {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Value
    )

    $lines = [System.Collections.Generic.List[string]](Get-Content -LiteralPath $Path -Encoding utf8)
    $matched = $false
    for ($index = 0; $index -lt $lines.Count; $index++) {
        if ($lines[$index] -match ("^\s*{0}\s*=" -f [regex]::Escape($Name))) {
            $lines[$index] = "$Name=$Value"
            $matched = $true
        }
    }
    if (-not $matched) { $lines.Add("$Name=$Value") }
    $lines | Set-Content -LiteralPath $Path -Encoding utf8
}
