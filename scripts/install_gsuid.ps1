param(
    [switch]$SkipDependencies,
    [switch]$SkipRepositorySync
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$LockPath = Join-Path $Root "config\upstream-lock.json"
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Write-Utf8NoBom {
    param(
        [string]$Path,
        [string]$Value
    )
    [System.IO.File]::WriteAllText($Path, $Value, $Utf8NoBom)
}

if (-not (Test-Path -LiteralPath $Python)) {
    throw "The project virtual environment was not found: $Python"
}

if (-not (Test-Path -LiteralPath $LockPath)) {
    throw "The upstream lock file was not found: $LockPath"
}

$UpstreamLock = Get-Content -LiteralPath $LockPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($UpstreamLock.schema_version -ne 1 -or $null -eq $UpstreamLock.repositories) {
    throw "The upstream lock file is malformed or unsupported: $LockPath"
}

function Resolve-UpstreamPath {
    param([string]$RelativePath)
    $candidate = [System.IO.Path]::GetFullPath((Join-Path $Root ($RelativePath -replace '/', '\')))
    $rootPrefix = $Root.TrimEnd('\') + '\'
    if (-not $candidate.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Unsafe upstream repository path in lock file: $RelativePath"
    }
    return $candidate
}

$CoreEntries = @($UpstreamLock.repositories | Where-Object { $_.name -eq "GsUID Core" })
if ($CoreEntries.Count -ne 1) {
    throw "The upstream lock must contain exactly one GsUID Core repository."
}
$CoreDir = Resolve-UpstreamPath ([string]$CoreEntries[0].path)

function Sync-Repository {
    param(
        [string]$Url,
        [string]$Path,
        [string]$Branch = ""
    )
    if (Test-Path -LiteralPath (Join-Path $Path ".git")) {
        Write-Host "Updating $Path"
        $dirty = @(& git -C $Path status --porcelain --untracked-files=all)
        $statusExitCode = $LASTEXITCODE
        if ($statusExitCode -ne 0) {
            throw "Could not inspect upstream repository $Path (exit code $statusExitCode)."
        }
        if ($dirty.Count -gt 0) {
            throw "Local changes or untracked files found in upstream repository $Path. Move adapters into the QQ bot repository before updating."
        }

        $stashes = @(& git -C $Path stash list)
        $stashExitCode = $LASTEXITCODE
        if ($stashExitCode -ne 0) {
            throw "Could not inspect stashes in upstream repository $Path (exit code $stashExitCode)."
        }
        if ($stashes.Count -gt 0) {
            throw "Stashes found in upstream repository $Path. Upstream repositories must not retain local patches."
        }

        $actualUrl = (& git -C $Path remote get-url origin).Trim()
        $urlExitCode = $LASTEXITCODE
        if ($urlExitCode -ne 0) {
            throw "Could not read origin URL for upstream repository $Path (exit code $urlExitCode)."
        }
        if ($actualUrl -ne $Url) {
            throw "Upstream repository $Path has origin $actualUrl; expected $Url."
        }

        $currentBranch = (& git -C $Path branch --show-current).Trim()
        $branchExitCode = $LASTEXITCODE
        if ($branchExitCode -ne 0) {
            throw "Could not read the current branch for upstream repository $Path (exit code $branchExitCode)."
        }
        if ($Branch -and $currentBranch -ne $Branch) {
            throw "Upstream repository $Path is on branch $currentBranch; expected $Branch."
        }

        $targetBranch = if ($Branch) { $Branch } else { $currentBranch }
        & git -C $Path fetch --prune origin $targetBranch
        $fetchExitCode = $LASTEXITCODE
        if ($fetchExitCode -ne 0) {
            throw "Could not fetch origin/$targetBranch for $Path (exit code $fetchExitCode)."
        }

        $expectedTracking = "origin/$targetBranch"
        $tracking = (& git -C $Path rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>$null)
        $trackingExitCode = $LASTEXITCODE
        if ($trackingExitCode -ne 0 -or $tracking.Trim() -ne $expectedTracking) {
            & git -C $Path branch --set-upstream-to=$expectedTracking $targetBranch
            $setTrackingExitCode = $LASTEXITCODE
            if ($setTrackingExitCode -ne 0) {
                throw "Could not set tracking branch $expectedTracking for $Path (exit code $setTrackingExitCode)."
            }
        }

        & git -C $Path merge-base --is-ancestor HEAD "refs/remotes/origin/$targetBranch"
        $historyExitCode = $LASTEXITCODE
        if ($historyExitCode -eq 1) {
            throw "Upstream repository $Path has local-only commits or diverged history. Move project changes out of upstream; no reset, merge, or replay was attempted."
        }
        if ($historyExitCode -ne 0) {
            throw "Could not compare $Path with origin/$targetBranch (exit code $historyExitCode)."
        }

        & git -C $Path pull --ff-only origin $targetBranch
        $pullExitCode = $LASTEXITCODE
        if ($pullExitCode -ne 0) {
            throw "Could not fast-forward $Path (exit code $pullExitCode)."
        }
        return
    }
    if (Test-Path -LiteralPath $Path) {
        throw "Path exists but is not a git repository: $Path"
    }
    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    Write-Host "Cloning $Url"
    if ($Branch) {
        & git clone --depth 1 --single-branch --branch $Branch $Url $Path
    } else {
        & git clone --depth 1 --single-branch $Url $Path
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Could not clone $Url"
    }
}

if (-not $SkipRepositorySync) {
    foreach ($repository in $UpstreamLock.repositories) {
        Sync-Repository ([string]$repository.url) (Resolve-UpstreamPath ([string]$repository.path)) ([string]$repository.branch)
    }
    & $Python (Join-Path $PSScriptRoot "validate_upstream_lock.py") --write
    if ($LASTEXITCODE -ne 0) { throw "Could not refresh the upstream lock file." }
}

if (-not $SkipDependencies) {
    Write-Host "Installing GenshinUID Core dependencies"
    & $Python -m pip install -e $CoreDir
    if ($LASTEXITCODE -ne 0) { throw "GenshinUID Core dependency installation failed" }
    Write-Host "Installing the official NoneBot2 Core connector"
    & $Python -m pip install -e "$Root[gsuid]"
    if ($LASTEXITCODE -ne 0) { throw "NoneBot2 Core connector installation failed" }
}

$envPath = Join-Path $Root ".env"
if (Test-Path -LiteralPath $envPath) {
    $content = Get-Content -LiteralPath $envPath -Encoding UTF8
    $updates = @{
        "GSUID_ENABLED" = "true"
        "GSUID_CORE_DIR" = "GsUID.Core"
        "gsuid_core_host" = "127.0.0.1"
        "gsuid_core_port" = "8765"
        "gsuid_core_botid" = "QQLocalDataBot"
    }
    foreach ($key in $updates.Keys) {
        $found = $false
        $content = @($content | ForEach-Object {
            if ($_ -match "^\s*${key}=") {
                $found = $true
                "${key}=$($updates[$key])"
            } else {
                $_
            }
        })
        if (-not $found) { $content += "${key}=$($updates[$key])" }
    }
    Write-Utf8NoBom $envPath ($content -join [Environment]::NewLine)
}

$coreData = Join-Path $CoreDir "data"
$coreConfig = Join-Path $coreData "config.json"
if (-not (Test-Path -LiteralPath $coreConfig)) {
    New-Item -ItemType Directory -Force -Path $coreData | Out-Null
    $operatorLine = Get-Content -LiteralPath $envPath -Encoding UTF8 -ErrorAction SilentlyContinue |
        Where-Object { $_ -match '^BOT_OPERATOR_IDS=' } | Select-Object -First 1
    $masters = @()
    if ($operatorLine) {
        $masters = @((($operatorLine -split '=', 2)[1] -split ',') | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim() })
    }
    $json = [ordered]@{
        HOST = "127.0.0.1"
        PORT = "8765"
        ENABLE_HTTP = $false
        WS_TOKEN = ""
        TRUSTED_IPS = @("localhost", "::1", "127.0.0.1")
        masters = $masters
        superusers = @()
        command_start = @()
        sv = @{}
    } | ConvertTo-Json -Depth 8
    Write-Utf8NoBom $coreConfig $json
}

# Keep Core's master list aligned with the project's operator allowlist.
$coreSettings = Get-Content -LiteralPath $coreConfig -Raw -Encoding UTF8 | ConvertFrom-Json
$operatorLine = Get-Content -LiteralPath $envPath -Encoding UTF8 -ErrorAction SilentlyContinue |
    Where-Object { $_ -match '^BOT_OPERATOR_IDS=' } | Select-Object -First 1
$coreMasters = @()
if ($operatorLine) {
    $coreMasters = @((($operatorLine -split '=', 2)[1] -split ',') |
        Where-Object { $_.Trim() } | ForEach-Object { $_.Trim() })
}
$coreSettings.masters = $coreMasters
$coreSettingsJson = $coreSettings | ConvertTo-Json -Depth 20
Write-Utf8NoBom $coreConfig $coreSettingsJson

# Resource updates can ask GsUID Core to restart itself. The upstream default
# uses the system `python`, while this project installs Core into .venv.
$corePluginConfigDir = Join-Path $coreData "configs"
$corePluginConfig = Join-Path $corePluginConfigDir "core_config.json"
New-Item -ItemType Directory -Force -Path $corePluginConfigDir | Out-Null
$restartConfig = [ordered]@{}
if (Test-Path -LiteralPath $corePluginConfig) {
    $storedConfig = Get-Content -LiteralPath $corePluginConfig -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($property in $storedConfig.PSObject.Properties) {
        $restartConfig[$property.Name] = $property.Value
    }
}
if (-not $restartConfig.Contains("is_use_custom_restart_command")) {
    $restartConfig["is_use_custom_restart_command"] = [ordered]@{
        type = "GsBoolConfig"
        title = "Use project virtualenv for Core restart"
        desc = "Keep Core restarts inside the project virtual environment"
        data = $true
        secret = $false
    }
}
if (-not $restartConfig.Contains("restart_command")) {
    $restartConfig["restart_command"] = [ordered]@{
        type = "GsStrConfig"
        title = "Core restart command"
        desc = "Project-managed Core restart command"
        data = ""
        options = @()
        regex = $null
        details = $null
        secret = $false
    }
}
$restartConfig["is_use_custom_restart_command"].data = $true
$coreRunner = Join-Path $Root "scripts\run_gsuid_core.py"
$restartConfig["restart_command"].data = '"' + $Python + '" -u "' + $coreRunner + '"'
$restartJson = $restartConfig | ConvertTo-Json -Depth 20
Write-Utf8NoBom $corePluginConfig $restartJson

# XutheringWavesUID owns the combined upstream help switch. Seed the single
# typed value on a fresh install; Core reconciles the remaining defaults.
$wavesConfigDir = Join-Path $coreData "XutheringWavesUID"
$wavesConfig = Join-Path $wavesConfigDir "config.json"
New-Item -ItemType Directory -Force -Path $wavesConfigDir | Out-Null
if (Test-Path -LiteralPath $wavesConfig) {
    $wavesSettings = Get-Content -LiteralPath $wavesConfig -Raw -Encoding UTF8 | ConvertFrom-Json
} else {
    $wavesSettings = [pscustomobject]@{}
}
$helpExtraModules = [pscustomobject][ordered]@{
    type = "GsListStrConfig"
    title = "帮助显示额外模块（重载生效）"
    desc = "显示已安装的鸣潮扩展模块"
    data = @("all")
    options = @("roversign", "todayecho", "scoreecho", "roverreminder", "all")
    secret = $false
}
if ($null -eq $wavesSettings.PSObject.Properties["HelpExtraModules"]) {
    $wavesSettings | Add-Member -NotePropertyName "HelpExtraModules" -NotePropertyValue $helpExtraModules
} else {
    $wavesSettings.HelpExtraModules.data = @("all")
}
$wavesSettingsJson = $wavesSettings | ConvertTo-Json -Depth 30
Write-Utf8NoBom $wavesConfig $wavesSettingsJson

# RoverReminder stays installed for upstream compatibility, but this project
# does not run its mail scheduler or accept persisted reminder settings.
& $Python (Join-Path $PSScriptRoot "configure_wuwa_runtime.py") --core-dir $CoreDir
if ($LASTEXITCODE -ne 0) { throw "Could not disable RoverReminder mail reminders." }

Write-Host "GenshinUID ecosystem installed under: $CoreDir"
Write-Host "Start Core first with: .\scripts\start_gsuid_core.ps1"
Write-Host "Then restart the normal QQ bot process."
