from __future__ import annotations

import pytest

from bot.services import env_sync
from bot.services.env_sync import sync_feature_scope, sync_passive_group_value, update_env_value


def _env(tmp_path, text: str) -> None:
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    return path


def test_update_env_replaces_value_and_preserves_comments(monkeypatch, tmp_path):
    path = _env(
        tmp_path,
        "# 注释一行\nDUPLICATE_GROUP_IDS=1001\n# 另一段\nBOT_RANDOM_REACTION_PROBABILITY=0.1\n",
    )
    monkeypatch.setattr(env_sync, "ENV_PATH", path)
    update_env_value("DUPLICATE_GROUP_IDS", "1001,1002")
    assert path.read_text(encoding="utf-8") == (
        "# 注释一行\nDUPLICATE_GROUP_IDS=1001,1002\n# 另一段\nBOT_RANDOM_REACTION_PROBABILITY=0.1\n"
    )


def test_update_env_appends_missing_key(monkeypatch, tmp_path):
    path = _env(tmp_path, "DUPLICATE_GROUP_IDS=1001\n")
    monkeypatch.setattr(env_sync, "ENV_PATH", path)
    update_env_value("GAME_GROUP_IDS", "1002")
    assert "GAME_GROUP_IDS=1002" in path.read_text(encoding="utf-8")


def test_update_env_rejects_unwritable_keys(monkeypatch, tmp_path):
    path = _env(tmp_path, "TANGTANG_API_KEY=secret\n")
    monkeypatch.setattr(env_sync, "ENV_PATH", path)
    with pytest.raises(ValueError):
        update_env_value("TANGTANG_API_KEY", "other")
    assert path.read_text(encoding="utf-8") == "TANGTANG_API_KEY=secret\n"


def test_proactive_env_keys_are_writable_by_commands(monkeypatch, tmp_path):
    path = _env(tmp_path, "TANGTANG_PROACTIVE_ENABLED=false\n")
    monkeypatch.setattr(env_sync, "ENV_PATH", path)
    update_env_value("TANGTANG_PROACTIVE_ENABLED", "true")
    update_env_value("TANGTANG_PROACTIVE_PROBABILITY", "0.05")
    update_env_value("TANGTANG_PROACTIVE_COOLDOWN_SECONDS", "600")
    update_env_value("TANGTANG_PROACTIVE_MESSAGE_INTERVAL", "50")
    text = path.read_text(encoding="utf-8")
    assert "TANGTANG_PROACTIVE_ENABLED=true" in text
    assert "TANGTANG_PROACTIVE_PROBABILITY=0.05" in text
    assert "TANGTANG_PROACTIVE_COOLDOWN_SECONDS=600" in text
    assert "TANGTANG_PROACTIVE_MESSAGE_INTERVAL=50" in text


def test_sync_feature_scope_writes_sorted_group_ids(monkeypatch, tmp_path):
    path = _env(tmp_path, "GAME_GROUP_IDS=1001\n")
    monkeypatch.setattr(env_sync, "ENV_PATH", path)
    sync_feature_scope("game", (1002, 1001, 1003))
    assert "GAME_GROUP_IDS=1001,1002,1003" in path.read_text(encoding="utf-8")


def test_passive_feature_scope_command_writes_env(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from bot.db import Database
    from bot.services.passive_settings import PassiveSettings, PassiveSettingsStore

    monkeypatch.setattr(
        "bot.services.passive_settings.settings",
        SimpleNamespace(
            managed_group_ids=(1001, 1002),
            duplicate_group_ids=(),
            game_group_ids=(),
            game_api_group_ids=(),
            game_api_enabled=True,
            activity_group_ids=(),
            random_reaction_group_ids=(),
            hourly_announcement_group_ids=(),
        ),
    )
    db = Database(tmp_path / "bot.db")
    db.configure_groups((1001, 1002))
    env_path = tmp_path / ".env"
    env_path.write_text("DUPLICATE_GROUP_IDS=\n", encoding="utf-8")
    monkeypatch.setattr(env_sync, "ENV_PATH", env_path)
    store = PassiveSettingsStore(
        db, defaults=PassiveSettings(0.1, 120, 0.1, 3600, 100), sync_env=True
    )
    store.add_feature_group("duplicate", 1002)
    assert "DUPLICATE_GROUP_IDS=1002" in env_path.read_text(encoding="utf-8")


def test_passive_group_update_rewrites_only_its_env_value(monkeypatch, tmp_path):
    from types import SimpleNamespace

    monkeypatch.setattr(
        env_sync,
        "settings",
        SimpleNamespace(random_reaction_group_ids=(1002, 1001)),
    )
    path = _env(
        tmp_path,
        "BOT_RANDOM_REPEAT_PROBABILITY=0.01,0.02\n"
        "BOT_RANDOM_TRIPLE_REPEAT_ENABLED=true,false\n",
    )
    monkeypatch.setattr(env_sync, "ENV_PATH", path)

    sync_passive_group_value("repeat_probability", 1001, "0.05")
    sync_passive_group_value("triple_repeat_enabled", 1002, "false")

    text = path.read_text(encoding="utf-8")
    assert "BOT_RANDOM_REPEAT_PROBABILITY=0.01,0.05" in text
    assert "BOT_RANDOM_TRIPLE_REPEAT_ENABLED=false,false" in text


def test_game_api_hot_switch_writes_env(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from bot.db import Database
    from bot.services.passive_settings import PassiveSettings, PassiveSettingsStore

    monkeypatch.setattr(
        "bot.services.passive_settings.settings",
        SimpleNamespace(
            managed_group_ids=(1001, 1002),
            duplicate_group_ids=(),
            game_group_ids=(),
            game_api_group_ids=(),
            game_api_enabled=True,
            activity_group_ids=(),
            random_reaction_group_ids=(),
            hourly_announcement_group_ids=(),
        ),
    )
    db = Database(tmp_path / "bot.db")
    db.configure_groups((1001, 1002))
    env_path = tmp_path / ".env"
    env_path.write_text("GAME_API_ENABLED=true\n", encoding="utf-8")
    monkeypatch.setattr(env_sync, "ENV_PATH", env_path)
    store = PassiveSettingsStore(
        db, defaults=PassiveSettings(0.1, 120, 0.1, 3600, 100), sync_env=True
    )
    assert store.is_game_api_enabled()
    store.set_game_api_enabled(False)
    assert not store.is_game_api_enabled()
    assert "GAME_API_ENABLED=false" in env_path.read_text(encoding="utf-8")


def test_game_api_hot_switch_defaults_from_env_and_mirrors_it(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from bot.db import Database
    from bot.services.passive_settings import PassiveSettings, PassiveSettingsStore

    monkeypatch.setattr(
        "bot.services.passive_settings.settings",
        SimpleNamespace(
            managed_group_ids=(1001, 1002),
            duplicate_group_ids=(),
            game_group_ids=(),
            game_api_group_ids=(),
            game_api_enabled=False,
            activity_group_ids=(),
            random_reaction_group_ids=(),
            hourly_announcement_group_ids=(),
        ),
    )
    db = Database(tmp_path / "bot.db")
    db.configure_groups((1001, 1002))
    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(env_sync, "ENV_PATH", env_path)
    store = PassiveSettingsStore(
        db, defaults=PassiveSettings(0.1, 120, 0.1, 3600, 100), sync_env=True
    )
    assert not store.is_game_api_enabled()
    assert "GAME_API_ENABLED=false" in env_path.read_text(encoding="utf-8")
