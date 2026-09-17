import pytest

from bot.db import Database
from bot.services.passive_settings import PassiveSettings, PassiveSettingsStore

TEST_GROUP_IDS = (1001, 1002)


@pytest.fixture(autouse=True)
def isolated_scope_defaults(monkeypatch):
    """Use empty feature-scope defaults regardless of the workstation .env."""
    from types import SimpleNamespace

    monkeypatch.setattr(
        "bot.services.passive_settings.settings",
        SimpleNamespace(
            managed_group_ids=TEST_GROUP_IDS,
            duplicate_group_ids=(),
            game_group_ids=(),
            game_api_group_ids=(),
            game_api_enabled=True,
            activity_group_ids=(),
            random_reaction_group_ids=(),
            hourly_announcement_group_ids=(),
            random_reaction_probability=0.1,
            random_reaction_cooldown_seconds=120,
            random_repeat_probability=0.1,
            random_repeat_cooldown_seconds=3600,
            random_repeat_message_interval=100,
            random_triple_repeat_probability=0.3,
        ),
    )


def test_passive_settings_updates_are_persistent(tmp_path):
    db = Database(tmp_path / "bot.db")
    defaults = PassiveSettings(0.3, 10, 0.1, 900, 50)
    store = PassiveSettingsStore(db, defaults)
    group_id = TEST_GROUP_IDS[0]
    other_group_id = TEST_GROUP_IDS[1]
    store.add_group(group_id)
    store.add_group(other_group_id)

    store.set_reaction_probability(group_id, 0.25)
    store.set_reaction_cooldown_seconds(group_id, 20)
    store.set_repeat_probability(group_id, 0.05)
    store.set_repeat_cooldown_seconds(group_id, 1200)
    store.set_repeat_message_interval(group_id, 80)

    restarted = PassiveSettingsStore(db, defaults)
    assert restarted.for_group(group_id) == PassiveSettings(0.25, 20, 0.05, 1200, 80)
    assert restarted.for_group(other_group_id) == defaults


def test_passive_settings_persist_zero_cooldowns_and_repeat_interval(tmp_path):
    db = Database(tmp_path / "bot.db")
    defaults = PassiveSettings(0.3, 10, 0.1, 900, 50)
    store = PassiveSettingsStore(db, defaults)
    group_id = TEST_GROUP_IDS[0]
    store.add_group(group_id)

    store.set_reaction_cooldown_seconds(group_id, 0)
    store.set_repeat_cooldown_seconds(group_id, 0)
    store.set_repeat_message_interval(group_id, 0)

    assert PassiveSettingsStore(db, defaults).for_group(group_id) == PassiveSettings(0.3, 0, 0.1, 0, 0)


def test_triple_repeat_switch_is_persistent(tmp_path):
    db = Database(tmp_path / "bot.db")
    defaults = PassiveSettings(0.3, 10, 0.1, 900, 50)
    store = PassiveSettingsStore(db, defaults)
    group_id = TEST_GROUP_IDS[0]
    store.add_group(group_id)

    assert not store.for_group(group_id).triple_repeat_enabled
    store.set_triple_repeat_enabled(group_id, True)

    assert PassiveSettingsStore(db, defaults).for_group(group_id).triple_repeat_enabled


def test_triple_repeat_probability_is_persistent(tmp_path):
    db = Database(tmp_path / "bot.db")
    defaults = PassiveSettings(0.3, 10, 0.1, 900, 50)
    store = PassiveSettingsStore(db, defaults)
    group_id = TEST_GROUP_IDS[0]
    store.add_group(group_id)

    assert store.for_group(group_id).triple_repeat_probability == 0.30
    store.set_triple_repeat_probability(group_id, 0.5)

    assert PassiveSettingsStore(db, defaults).for_group(group_id).triple_repeat_probability == 0.5


def test_passive_group_settings_are_materialized_as_independent_records(tmp_path):
    db = Database(tmp_path / "bot.db")
    store = PassiveSettingsStore(db, PassiveSettings(0.3, 10, 0.1, 900, 50))
    group_id = TEST_GROUP_IDS[0]

    store.add_group(group_id)

    assert set(db.passive_group_settings(group_id)) == {
        "reaction_probability",
        "reaction_cooldown_seconds",
        "repeat_probability",
        "repeat_cooldown_seconds",
        "repeat_message_interval",
        "triple_repeat_enabled",
        "triple_repeat_probability",
    }


def test_passive_group_scope_is_persistent_and_restricted_to_managed_groups(tmp_path):
    db = Database(tmp_path / "bot.db")
    store = PassiveSettingsStore(db, PassiveSettings(0.3, 10, 0.1, 900, 50))
    group_id = TEST_GROUP_IDS[0]

    store.add_group(group_id)
    assert store.is_group_enabled(group_id)
    assert PassiveSettingsStore(db).is_group_enabled(group_id)

    store.remove_group(group_id)
    assert not store.is_group_enabled(group_id)
    with pytest.raises(ValueError, match="managed scope"):
        store.add_group(999999999)


def test_feature_group_scopes_are_independent_and_persistent(tmp_path):
    db = Database(tmp_path / "bot.db")
    store = PassiveSettingsStore(db, PassiveSettings(0.3, 10, 0.1, 900, 50))
    group_id = TEST_GROUP_IDS[0]

    store.add_feature_group("duplicate", group_id)
    assert store.is_feature_group_enabled("duplicate", group_id)
    assert PassiveSettingsStore(db).is_feature_group_enabled("duplicate", group_id)

    store.remove_feature_group("duplicate", group_id)
    assert not store.is_feature_group_enabled("duplicate", group_id)
    with pytest.raises(ValueError, match="unsupported"):
        store.add_feature_group("unknown", group_id)

    assert store.is_feature_group_enabled("today_wife", group_id)
    store.remove_feature_group("today_wife", group_id)
    assert not PassiveSettingsStore(db).is_feature_group_enabled("today_wife", group_id)


def test_game_mute_defaults_to_enabled_until_explicitly_disabled(tmp_path):
    db = Database(tmp_path / "bot.db")
    store = PassiveSettingsStore(db, PassiveSettings(0.3, 10, 0.1, 900, 50))
    group_id = TEST_GROUP_IDS[0]

    store.add_feature_group("game", group_id)
    assert store.is_game_mute_enabled(group_id)

    store.add_feature_group("game_mute_disabled", group_id)
    assert not store.is_game_mute_enabled(group_id)

    store.remove_feature_group("game_mute_disabled", group_id)
    assert store.is_game_mute_enabled(group_id)


def test_global_game_switch_is_enabled_by_default_and_persistent(tmp_path):
    db = Database(tmp_path / "bot.db")
    store = PassiveSettingsStore(db, PassiveSettings(0.3, 10, 0.1, 900, 50))

    assert store.groups("game") == frozenset(TEST_GROUP_IDS)
    assert store.is_game_globally_enabled()
    assert not store.set_game_globally_enabled(False)
    assert not PassiveSettingsStore(db).is_game_globally_enabled()
    assert PassiveSettingsStore(db).set_game_globally_enabled(True)


def test_global_chat_switches_are_enabled_by_default_and_persistent(tmp_path):
    db = Database(tmp_path / "bot.db")
    store = PassiveSettingsStore(db, PassiveSettings(0.3, 10, 0.1, 900, 50))

    assert store.is_chat_globally_enabled("mention_chat")
    assert store.is_chat_globally_enabled("proactive_chat")

    assert not store.set_chat_globally_enabled("mention_chat", False)
    assert store.is_chat_globally_enabled("proactive_chat")
    restarted = PassiveSettingsStore(db, PassiveSettings(0.3, 10, 0.1, 900, 50))
    assert not restarted.is_chat_globally_enabled("mention_chat")
    assert restarted.is_chat_globally_enabled("proactive_chat")

    assert not restarted.set_chat_globally_enabled("proactive_chat", False)
    final = PassiveSettingsStore(db, PassiveSettings(0.3, 10, 0.1, 900, 50))
    assert not final.is_chat_globally_enabled("mention_chat")
    assert not final.is_chat_globally_enabled("proactive_chat")

    with pytest.raises(ValueError, match="unsupported chat feature"):
        final.is_chat_globally_enabled("unknown")
