$ErrorActionPreference = "Stop"

$repoSkill = Join-Path $PSScriptRoot "qq-bot-maintainer"
if (-not (Test-Path -LiteralPath (Join-Path $repoSkill "SKILL.md"))) {
    throw "Missing canonical skill: $repoSkill"
}

$skillHome = if ($env:CODEX_HOME) {
    Join-Path $env:CODEX_HOME "skills"
} else {
    Join-Path $env:USERPROFILE ".codex\skills"
}
$linkPath = Join-Path $skillHome "QQBot\qq-bot-maintainer"

if (Test-Path -LiteralPath $linkPath) {
    $item = Get-Item -LiteralPath $linkPath -Force
    if ($item.LinkType -ne "Junction") {
        throw "Refusing to replace a non-junction path: $linkPath"
    }
    if ($item.Target -ne $repoSkill) {
        throw "Existing junction points elsewhere: $($item.Target)"
    }
    Write-Output "Link already present: $linkPath -> $repoSkill"
    exit 0
}

New-Item -ItemType Directory -Path (Split-Path -Parent $linkPath) -Force | Out-Null
New-Item -ItemType Junction -Path $linkPath -Target $repoSkill | Out-Null
Write-Output "Linked: $linkPath -> $repoSkill"
