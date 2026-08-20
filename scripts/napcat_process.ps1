Set-StrictMode -Version Latest

function Get-BotLaunchSettings {
    param([Parameter(Mandatory)][string]$Root)

    $envPath = Join-Path $Root ".env"
    if (-not (Test-Path -LiteralPath $envPath)) {
        throw "Missing configuration file: $envPath"
    }

    $values = @{}
    foreach ($line in Get-Content -LiteralPath $envPath -Encoding utf8) {
        $entry = $line.Trim()
        if (-not $entry -or $entry.StartsWith("#") -or -not $entry.Contains("=")) {
            continue
        }
        $key, $value = $entry.Split("=", 2)
        $value = $value.Trim()
        if ($value.Length -ge 2 -and $value[0] -eq $value[-1] -and $value[0] -in @('"', "'")) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        $values[$key.Trim()] = $value
    }

    $accountId = [string]$values["NAPCAT_QQ_ID"]
    if ($accountId -notmatch "^\d{5,12}$") {
        throw "NAPCAT_QQ_ID must be a 5-12 digit QQ number."
    }
    $napcatDir = [string]$values["NAPCAT_DIR"]
    if (-not $napcatDir) {
        throw "NAPCAT_DIR is empty."
    }
    if (-not [IO.Path]::IsPathRooted($napcatDir)) {
        $napcatDir = Join-Path $Root $napcatDir
    }
    $port = 8080
    if ($values.ContainsKey("PORT") -and $values["PORT"] -match "^\d+$") {
        $port = [int]$values["PORT"]
    }
    if ($port -lt 1 -or $port -gt 65535) {
        throw "PORT must be between 1 and 65535."
    }

    return [pscustomobject]@{
        Root = $Root
        AccountId = $accountId
        NapCatDir = [IO.Path]::GetFullPath($napcatDir)
        Port = $port
        StatePath = Join-Path $Root "data\quick-napcat-state.json"
    }
}

function Get-NapCatLauncherPath {
    param([Parameter(Mandatory)][string]$NapCatDir)

    # The elevated launcher can lose trailing arguments when it relaunches itself.
    # Prefer NapCat's user launcher so the configured quick-login account reaches QQ.
    foreach ($name in @("launcher-win10-user.bat", "launcher.bat")) {
        $candidate = Join-Path $NapCatDir $name
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }
    throw "NapCat launcher not found in: $NapCatDir"
}

function Get-AccountHints {
    param([string]$CommandLine)

    $hints = [System.Collections.Generic.HashSet[string]]::new()
    foreach ($pattern in @(
        "(?:^|\s)-q\s+(\d{5,12})(?:\s|$)",
        "--(?:qq|uin|uid|account)(?:=|\s+)(\d{5,12})(?:\s|$)",
        "qq(?:nt)?[_-](\d{5,12})"
    )) {
        foreach ($match in [regex]::Matches($CommandLine, $pattern, [Text.RegularExpressions.RegexOptions]::IgnoreCase)) {
            [void]$hints.Add($match.Groups[1].Value)
        }
    }
    return @($hints | Sort-Object)
}

function Get-CreationIdentity {
    param([Parameter(Mandatory)][datetime]$CreationDate)

    $utc = $CreationDate.ToUniversalTime()
    return [pscustomobject]@{
        Display = $utc.ToString("o")
        Ticks = $utc.Ticks
    }
}

function Get-QqRootProcesses {
    $allProcesses = @(Get-CimInstance Win32_Process)
    $processById = @{}
    $qqPids = [System.Collections.Generic.HashSet[int]]::new()
    foreach ($process in $allProcesses) {
        $processById[[int]$process.ProcessId] = $process
        if ($process.Name -ieq "QQ.exe") {
            [void]$qqPids.Add([int]$process.ProcessId)
        }
    }

    $roots = foreach ($process in $allProcesses) {
        if ($process.Name -ine "QQ.exe" -or $qqPids.Contains([int]$process.ParentProcessId)) {
            continue
        }
        try {
            $identity = Get-CreationIdentity -CreationDate ([datetime]$process.CreationDate)
        } catch {
            # A malformed WMI creation timestamp must not block bot startup.
            continue
        }
        [pscustomobject]@{
            Pid = [int]$process.ProcessId
            CreationDate = $identity.Display
            CreationTicks = $identity.Ticks
            AccountIds = @(Get-AccountHints ([string]$process.CommandLine))
        }
    }
    return @($roots)
}

function Get-QqRootForProcess {
    param(
        [Parameter(Mandatory)][int]$ProcessId,
        [Parameter(Mandatory)][hashtable]$ProcessById
    )

    $current = $ProcessById[$ProcessId]
    while ($null -ne $current) {
        if ($current.Name -ieq "QQ.exe") {
            $parent = $ProcessById[[int]$current.ParentProcessId]
            if ($null -eq $parent -or $parent.Name -ine "QQ.exe") {
                try {
                    $identity = Get-CreationIdentity -CreationDate ([datetime]$current.CreationDate)
                } catch {
                    return $null
                }
                return [pscustomobject]@{
                    Pid = [int]$current.ProcessId
                    CreationDate = $identity.Display
                    CreationTicks = $identity.Ticks
                    AccountIds = @(Get-AccountHints ([string]$current.CommandLine))
                }
            }
        }
        $current = $ProcessById[[int]$current.ParentProcessId]
    }
    return $null
}

function Get-QqRootsConnectedToPort {
    param([Parameter(Mandatory)][int]$Port)

    try {
        $connections = @(Get-NetTCPConnection -State Established -ErrorAction Stop)
    } catch {
        return @()
    }
    $allProcesses = @(Get-CimInstance Win32_Process)
    $processById = @{}
    foreach ($process in $allProcesses) {
        $processById[[int]$process.ProcessId] = $process
    }

    $roots = @{}
    foreach ($serverConnection in $connections) {
        if ([int]$serverConnection.LocalPort -ne $Port -or $serverConnection.LocalAddress -notin @("127.0.0.1", "::1")) {
            continue
        }
        $clientConnection = $connections | Where-Object {
            [int]$_.LocalPort -eq [int]$serverConnection.RemotePort -and
            [int]$_.RemotePort -eq $Port -and
            $_.State -eq "Established"
        } | Select-Object -First 1
        if ($null -eq $clientConnection) {
            continue
        }
        $root = Get-QqRootForProcess -ProcessId ([int]$clientConnection.OwningProcess) -ProcessById $processById
        if ($null -ne $root) {
            $roots[$root.Pid] = $root
        }
    }
    return @($roots.Values)
}

function Get-QuickNapCatState {
    param([Parameter(Mandatory)][string]$StatePath)

    if (-not (Test-Path -LiteralPath $StatePath)) {
        return $null
    }
    try {
        return Get-Content -LiteralPath $StatePath -Raw -Encoding utf8 | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Save-QuickNapCatState {
    param(
        [Parameter(Mandatory)][string]$StatePath,
        [Parameter(Mandatory)][object]$Root,
        [Parameter(Mandatory)][string]$AccountId
    )

    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $StatePath) | Out-Null
    [pscustomobject]@{
        pid = [int]$Root.Pid
        creation_date = [string]$Root.CreationDate
        creation_ticks = [int64]$Root.CreationTicks
        account_id = $AccountId
        recorded_at = [DateTime]::UtcNow.ToString("o")
    } | ConvertTo-Json | Set-Content -LiteralPath $StatePath -Encoding utf8
}

function Get-VerifiedStateRoot {
    param(
        [Parameter(Mandatory)][AllowEmptyCollection()][object[]]$Roots,
        [Parameter(Mandatory)][string]$StatePath,
        [Parameter(Mandatory)][string]$AccountId
    )

    $state = Get-QuickNapCatState -StatePath $StatePath
    if ($null -eq $state -or [string]$state.account_id -ne $AccountId) {
        return $null
    }
    $stateTicks = 0L
    if ($state.PSObject.Properties.Name -contains "creation_ticks") {
        $stateTicks = [int64]$state.creation_ticks
    }
    return $Roots | Where-Object {
        $_.Pid -eq [int]$state.pid -and (
            ($stateTicks -gt 0 -and $_.CreationTicks -eq $stateTicks) -or
            ($stateTicks -eq 0 -and $_.CreationDate -eq [string]$state.creation_date)
        )
    } | Select-Object -First 1
}

function Stop-VerifiedProcessTree {
    param([Parameter(Mandatory)][int]$ProcessId)

    & "$env:SystemRoot\System32\taskkill.exe" /PID $ProcessId /T /F | Out-Host
    if ($LASTEXITCODE -eq 0) {
        return $true
    }
    try {
        $elevated = Start-Process -FilePath "$env:SystemRoot\System32\taskkill.exe" -ArgumentList @("/PID", $ProcessId, "/T", "/F") -Verb RunAs -Wait -PassThru
        return $elevated.ExitCode -eq 0
    } catch {
        Write-Warning "Could not elevate taskkill for PID ${ProcessId}: $($_.Exception.Message)"
        return $false
    }
}
