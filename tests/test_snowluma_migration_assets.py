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


def test_active_transport_contract_contains_no_retired_napcat_path():
    active_paths = (
        "bot/config.py",
        "scripts/qq_transport.ps1",
        "scripts/start_qq_transport.ps1",
        "scripts/stop_qq_transport.ps1",
        "scripts/validate_qq_config.py",
    )
    for relative in active_paths:
        assert "napcat" not in source(relative).lower()

    for retired in (
        "scripts/napcat_process.ps1",
        "scripts/start_napcat_transport.ps1",
        "scripts/stop_napcat_transport.ps1",
        "scripts/switch_back_to_napcat.ps1",
        "scripts/watch_napcat.ps1",
        "scripts/prepare_snowluma_migration.ps1",
        "scripts/begin_snowluma_cutover.ps1",
        "scripts/complete_snowluma_cutover.ps1",
    ):
        assert not (ROOT / retired).exists()


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
    historical_backfill = source("scripts/backfill_bot_message_stats.py")

    assert "['QQ_ACCOUNT_ID']" in lagrange
    assert "NAPCAT_" not in lagrange
    for smoke in (smoke_game, smoke_gsuid):
        assert 'values.get("QQ_ACCOUNT_ID")' in smoke
        assert "NAPCAT_" not in smoke
    assert '"QQ_ACCOUNT_ID"' in organizer
    assert '"QQ_TRANSPORT_MAINTENANCE_ENABLED"' in organizer
    assert "NAPCAT_" not in organizer
    assert 'parser.add_argument("--log-dir", type=Path, required=True)' in historical_backfill
    assert 'ROOT / "NapCat.Shell"' not in historical_backfill
