from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_snowluma_release_is_pinned_by_version_commit_size_and_hash():
    lock = json.loads(source("config/snowluma-lock.json"))

    assert lock == {
        "repository": "https://github.com/SnowLuma/SnowLuma",
        "version": "v1.14.15",
        "source_commit": "fb5f9b21558134db8803d216dfc19c6bce11f2c0",
        "asset": "SnowLuma-v1.14.15-win-x64.zip",
        "asset_size": 37806025,
        "asset_url": "https://github.com/SnowLuma/SnowLuma/releases/download/v1.14.15/SnowLuma-v1.14.15-win-x64.zip",
        "sha256": "ab657f8121f8b503c8637ae9bf47d8982e4925897d9aa5d99056e04638f05809",
        "webui_port": 5099,
    }


def test_snowluma_installer_verifies_archive_before_extracting():
    installer = source("scripts/install_snowluma.ps1")

    assert installer.index("Get-FileHash") < installer.index("Expand-Archive")
    assert "actualHash" in installer
    assert "asset_size" in installer
    assert "index.mjs" in installer
    assert "node.exe" in installer
    assert "SNOWLUMA_ACCEPT_EULA" not in installer
    assert "SNOWLUMA_ACCEPT_PRIVACY" not in installer


def test_snowluma_config_is_reverse_ws_only_and_blocks_duplicate_gateways():
    configure = source("scripts/configure_snowluma.ps1")
    status = source("scripts/status_qq_transport.ps1")

    assert "httpServers = @()" in configure
    assert "wsServers = @()" in configure
    assert "name = 'nonebot-reverse-ws'" in configure
    assert "url = $settings.OneBotUrl" in configure
    assert "accessToken = $settings.OneBotAccessToken" in configure
    assert "reportSelfMessage = $false" in configure
    assert "OneBot client process IDs" not in status
    assert "onebot_connection_count" in status
    assert "onebot_client_matches_transport" in status
    assert "Test-OneBotConnectionOwnership" in status
    assert "snowLumaInstalled" in status
    assert "unexpected_onebot_client" in source("scripts/watch_qq_transport.ps1")


def test_cutover_is_two_phase_and_keeps_watchdog_out_of_manual_login():
    prepare = source("scripts/prepare_snowluma_migration.ps1")
    begin = source("scripts/begin_snowluma_cutover.ps1")
    complete = source("scripts/complete_snowluma_cutover.ps1")

    assert "-EnableOneBot:$false" in prepare
    assert begin.index("stop_watchdog.ps1") < begin.index("stop_qq_transport.ps1")
    select_snowluma = (
        "Set-BotEnvValue -Path $settings.EnvPath -Name 'QQ_PLATFORM_TRANSPORT' "
        "-Value 'snowluma'"
    )
    assert begin.index("stop_qq_transport.ps1") < begin.index(select_snowluma)
    assert begin.index("webui.json") < begin.index("stop_qq_transport.ps1")
    assert begin.index("consent.json") < begin.index("stop_qq_transport.ps1")
    assert "mustChangePassword" in begin
    assert "The watchdog remains stopped" in begin
    assert "Expected one OneBot client" in complete
    assert "not the tracked SnowLuma process" in complete
    assert complete.index("stop.ps1") < complete.index("start.ps1")
    assert complete.index("start.ps1") < complete.index("start_watchdog.ps1")


def test_failed_cutover_restores_exact_environment_and_runtime_chain():
    begin = source("scripts/begin_snowluma_cutover.ps1")

    catch_block = begin.split("} catch {", 1)[1]
    assert "Copy-Item -LiteralPath $envBackup -Destination $settings.EnvPath -Force" in catch_block
    assert "Get-ConfiguredTransportProcesses -Settings $snowSettings" in catch_block
    assert "previous QQ gateway was not restarted to avoid duplicate clients" in catch_block
    assert "if ($environmentRestored -and $snowLumaStopped)" in catch_block
    assert "watchdog remains stopped because the original environment or exclusive gateway state was not restored" in catch_block
    assert catch_block.index("stop.ps1") < catch_block.index("start.ps1")
    assert catch_block.index("start.ps1") < catch_block.index("start_qq_transport.ps1")
    assert catch_block.index("start_qq_transport.ps1") < catch_block.index("start_watchdog.ps1")


def test_napcat_rollback_status_checks_socket_ownership_through_qq_process_tree():
    transport = source("scripts/qq_transport.ps1")
    ownership = transport.split("function Test-OneBotConnectionOwnership", 1)[1].split(
        "function Set-BotEnvValue", 1
    )[0]

    assert "Get-QqRootsConnectedToPort -Port $Settings.Port" in ownership
    assert "connectedRoots[0].Pid" in ownership
    assert "return $true" not in ownership


def test_environment_template_selects_snowluma_without_login_material():
    template = source(".env.example")

    assert "QQ_PLATFORM_TRANSPORT=snowluma" in template
    assert "QQ_ACCOUNT_ID=" in template
    assert "SNOWLUMA_DIR=SnowLuma" in template
    assert "SNOWLUMA_WEBUI_PORT=5099" in template
    assert "NAPCAT_QQ_ID=" not in template
    assert "QQ_PASSWORD" not in template


def test_active_maintenance_tools_prefer_transport_neutral_account_settings():
    lagrange = source("scripts/configure_lagrange_onebot.ps1")
    smoke_game = source("scripts/smoke_game_api.py")
    smoke_gsuid = source("scripts/smoke_test_gsuid.py")
    organizer = source("scripts/organize_env.py")

    assert lagrange.index("['QQ_ACCOUNT_ID']") < lagrange.index("['NAPCAT_QQ_ID']")
    for smoke in (smoke_game, smoke_gsuid):
        assert 'values.get("QQ_ACCOUNT_ID")' in smoke
    assert '"QQ_ACCOUNT_ID"' in organizer
    assert '"QQ_TRANSPORT_MAINTENANCE_ENABLED"' in organizer


def test_repository_does_not_depend_on_the_ignored_napcat_runtime_for_tests():
    assert not (ROOT / "tests" / "test_napcat_reply_lookup.py").exists()
