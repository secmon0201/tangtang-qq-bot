import pytest

from bot.config import Settings, managed_group_order


@pytest.fixture(autouse=True)
def disable_real_completion_notification(monkeypatch):
    """Keep config unit tests independent from the workstation's real QQ targets."""
    monkeypatch.setenv("CODEX_COMPLETION_NOTIFY_ENABLED", "false")
    monkeypatch.setenv("CODEX_WORKER_ENABLED", "false")
    monkeypatch.setenv("ASOUL_BILI_PUSH_A_COAST", "false")
    monkeypatch.setenv("BOT_RANDOM_REACTION_ENABLED", "false")
    for key in (
        "DUPLICATE_GROUP_IDS",
        "GAME_GROUP_IDS",
        "ACTIVITY_GROUP_IDS",
        "HOURLY_ANNOUNCEMENT_GROUP_IDS",
        "BOT_RANDOM_REACTION_GROUP_IDS",
        "ASOUL_BILI_GROUP_IDS",
    ):
        monkeypatch.delenv(key, raising=False)


def test_config_accepts_more_than_ten_seed_groups(monkeypatch):
    monkeypatch.setenv("MANAGED_GROUP_IDS", ",".join(str(value) for value in range(1, 12)))
    assert Settings.from_env().managed_group_ids == tuple(range(1, 12))


def test_config_deduplicates_ids_and_keeps_secrets_out_of_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("MANAGED_GROUP_IDS", "1001,1001,1002")
    monkeypatch.setenv("BOT_OPERATOR_IDS", "99")
    monkeypatch.setenv("GLOBAL_ANNOUNCEMENT_OPERATOR_IDS", "66,66")
    monkeypatch.setenv("ACTIVITY_ADMIN_IDS", "77,77")
    monkeypatch.setenv("ACTIVITY_ADMIN_BLACKLIST_IDS", "88,88")
    monkeypatch.delenv("STATS_GROUP_IDS", raising=False)
    monkeypatch.delenv("DUPLICATE_GROUP_IDS", raising=False)
    monkeypatch.delenv("GAME_GROUP_IDS", raising=False)
    monkeypatch.delenv("ACTIVITY_GROUP_IDS", raising=False)
    monkeypatch.delenv("ASOUL_BILI_GROUP_IDS", raising=False)
    monkeypatch.setenv("BOT_RANDOM_REACTION_ENABLED", "false")
    monkeypatch.setenv("BOT_RANDOM_REACTION_GROUP_IDS", "")
    monkeypatch.setenv("BOT_DB_PATH", str(tmp_path / "db.sqlite"))
    monkeypatch.setenv("QQ_PASSWORD", "must-not-be-read")
    config = Settings.from_env()
    assert config.managed_group_ids == (1001, 1002)
    assert config.operator_ids == frozenset({99})
    assert config.global_announcement_operator_ids == frozenset({66})
    assert config.activity_admin_ids == frozenset({77})
    assert config.activity_admin_blacklist_ids == frozenset({88})
    assert config.activity_withdraw_ack_emoji_id == "32"
    assert config.avatar_refresh_interval == 3600
    assert config.avatar_refresh_cooldown == 900
    assert config.avatar_refresh_max_per_call == 24
    assert config.avatar_refresh_concurrency == 2
    assert not hasattr(config, "password")


def test_config_orders_group_subset_by_managed_sequence():
    assert managed_group_order({1003, 1001, 1002}, (1001, 1002, 1003)) == (1001, 1002, 1003)
    assert managed_group_order({999, 1001}, (1001, 1002)) == (1001, 999)
    assert managed_group_order((1002, 1001, 1002), (1001, 1002, 1003)) == (1001, 1002)


def test_config_a_coast_profile_defaults_to_enabled(monkeypatch):
    monkeypatch.delenv("A_COAST_PROFILE_ENABLED", raising=False)
    config = Settings.from_env()
    assert config.a_coast_profile_enabled is True


def test_config_can_disable_a_coast_profile(monkeypatch):
    monkeypatch.setenv("A_COAST_PROFILE_ENABLED", "false")
    config = Settings.from_env()
    assert config.a_coast_profile_enabled is False


def test_config_rejects_invalid_activity_withdraw_reaction(monkeypatch):
    monkeypatch.setenv("ACTIVITY_WITHDRAW_ACK_EMOJI_ID", "?")
    with pytest.raises(ValueError, match="ACTIVITY_WITHDRAW_ACK_EMOJI_ID"):
        Settings.from_env()


def test_config_feature_groups_must_be_managed_subset(monkeypatch):
    monkeypatch.setenv("MANAGED_GROUP_IDS", "1001,1002")
    monkeypatch.setenv("STATS_GROUP_IDS", "1002")
    monkeypatch.setenv("DUPLICATE_GROUP_IDS", "1001")
    monkeypatch.setenv("GAME_GROUP_IDS", "1003")
    with pytest.raises(ValueError, match="GAME_GROUP_IDS"):
        Settings.from_env()


def test_config_combines_external_and_a_coast_bilibili_groups(monkeypatch):
    monkeypatch.setenv(
        "MANAGED_GROUP_IDS",
        "9999,1128870029,1077416717,1083457871,1090284567,278824712",
    )
    for name in ("DUPLICATE_GROUP_IDS", "GAME_GROUP_IDS", "ACTIVITY_GROUP_IDS"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("BOT_RANDOM_REACTION_GROUP_IDS", "")
    monkeypatch.setenv("BOT_RANDOM_REACTION_ENABLED", "false")
    monkeypatch.setenv("ASOUL_BILI_GROUP_IDS", "9999")
    monkeypatch.setenv("ASOUL_BILI_PUSH_A_COAST", "true")

    config = Settings.from_env()

    assert config.asoul_bili_group_ids == (9999,)
    assert config.asoul_bili_effective_group_ids == (
        9999,
        1128870029,
        1077416717,
        1083457871,
        1090284567,
        278824712,
    )


def test_config_allows_explicit_a_coast_bilibili_group_when_switch_is_off(monkeypatch):
    monkeypatch.setenv("MANAGED_GROUP_IDS", "1128870029")
    for name in ("DUPLICATE_GROUP_IDS", "GAME_GROUP_IDS", "ACTIVITY_GROUP_IDS"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("ASOUL_BILI_GROUP_IDS", "1128870029")
    monkeypatch.setenv("ASOUL_BILI_PUSH_A_COAST", "false")
    monkeypatch.setenv("BOT_RANDOM_REACTION_GROUP_IDS", "")
    monkeypatch.setenv("BOT_RANDOM_REACTION_ENABLED", "false")

    config = Settings.from_env()

    assert config.asoul_bili_effective_group_ids == (1128870029,)


def test_config_uses_custom_a_coast_bilibili_scope(monkeypatch):
    monkeypatch.setenv("MANAGED_GROUP_IDS", "1001,1002")
    for name in ("DUPLICATE_GROUP_IDS", "GAME_GROUP_IDS", "ACTIVITY_GROUP_IDS"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("BOT_RANDOM_REACTION_GROUP_IDS", "")
    monkeypatch.setenv("BOT_RANDOM_REACTION_ENABLED", "false")
    monkeypatch.setenv("ASOUL_BILI_GROUP_IDS", "1001")
    monkeypatch.setenv("ASOUL_BILI_A_COAST_GROUP_IDS", "1002")
    monkeypatch.setenv("ASOUL_BILI_PUSH_A_COAST", "true")

    config = Settings.from_env()

    assert config.asoul_bili_effective_group_ids == (1001, 1002)


def test_config_validates_activity_admin_ids(monkeypatch):
    monkeypatch.setenv("ACTIVITY_ADMIN_IDS", "not-a-qq-number")
    with pytest.raises(ValueError, match="invalid QQ/group ID"):
        Settings.from_env()


def test_config_validates_global_announcement_operator_ids(monkeypatch):
    monkeypatch.setenv("GLOBAL_ANNOUNCEMENT_OPERATOR_IDS", "not-a-qq-number")
    with pytest.raises(ValueError, match="invalid QQ/group ID"):
        Settings.from_env()


def test_config_validates_activity_admin_blacklist_ids(monkeypatch):
    monkeypatch.setenv("ACTIVITY_ADMIN_BLACKLIST_IDS", "not-a-qq-number")
    with pytest.raises(ValueError, match="invalid QQ/group ID"):
        Settings.from_env()


def test_config_rejects_response_delay_range_in_reverse_order(monkeypatch):
    monkeypatch.setenv("BOT_RESPONSE_DELAY_MIN_SECONDS", "7")
    monkeypatch.setenv("BOT_RESPONSE_DELAY_MAX_SECONDS", "3")
    with pytest.raises(ValueError, match="MAX_SECONDS"):
        Settings.from_env()


def test_config_rejects_command_response_delay_range_in_reverse_order(monkeypatch):
    monkeypatch.setenv("BOT_COMMAND_RESPONSE_DELAY_MIN_SECONDS", "2")
    monkeypatch.setenv("BOT_COMMAND_RESPONSE_DELAY_MAX_SECONDS", "1")
    with pytest.raises(ValueError, match="COMMAND_RESPONSE_DELAY_MAX_SECONDS"):
        Settings.from_env()


def test_config_defaults_to_requested_humanized_response_ranges(monkeypatch):
    monkeypatch.delenv("BOT_RESPONSE_DELAY_MIN_SECONDS", raising=False)
    monkeypatch.delenv("BOT_RESPONSE_DELAY_MAX_SECONDS", raising=False)
    monkeypatch.delenv("BOT_COMMAND_RESPONSE_DELAY_MIN_SECONDS", raising=False)
    monkeypatch.delenv("BOT_COMMAND_RESPONSE_DELAY_MAX_SECONDS", raising=False)

    config = Settings.from_env()

    assert (config.response_delay_min_seconds, config.response_delay_max_seconds) == (2, 5)
    assert (
        config.command_response_delay_min_seconds,
        config.command_response_delay_max_seconds,
    ) == (1, 2)


def test_config_defaults_to_hash_commands_without_a_mention(monkeypatch):
    monkeypatch.delenv("BOT_COMMAND_PREFIX", raising=False)
    monkeypatch.delenv("BOT_REQUIRE_MENTION", raising=False)
    config = Settings.from_env()
    assert config.command_prefix == "#"
    assert not config.require_mention


def test_config_rejects_legacy_mention_command_mode(monkeypatch):
    monkeypatch.setenv("BOT_COMMAND_PREFIX", "")
    with pytest.raises(ValueError, match="BOT_COMMAND_PREFIX"):
        Settings.from_env()

    monkeypatch.setenv("BOT_COMMAND_PREFIX", "#")
    monkeypatch.setenv("BOT_REQUIRE_MENTION", "true")
    with pytest.raises(ValueError, match="BOT_REQUIRE_MENTION"):
        Settings.from_env()


def test_config_requires_at_least_one_mention_reaction(monkeypatch):
    monkeypatch.setenv("BOT_MENTION_ACK_EMOJI_IDS", "")
    with pytest.raises(ValueError, match="MENTION_ACK"):
        Settings.from_env()


def test_config_rejects_random_reaction_groups_outside_managed_scope(monkeypatch):
    monkeypatch.setenv("MANAGED_GROUP_IDS", "1001")
    monkeypatch.setenv("STATS_GROUP_IDS", "")
    monkeypatch.setenv("DUPLICATE_GROUP_IDS", "")
    monkeypatch.setenv("GAME_GROUP_IDS", "")
    monkeypatch.setenv("ACTIVITY_GROUP_IDS", "")
    monkeypatch.delenv("ASOUL_BILI_GROUP_IDS", raising=False)
    monkeypatch.setenv("BOT_RANDOM_REACTION_GROUP_IDS", "1002")
    with pytest.raises(ValueError, match="RANDOM_REACTION_GROUP_IDS"):
        Settings.from_env()


def test_config_validates_hourly_announcement_window(monkeypatch):
    monkeypatch.setenv("HOURLY_ANNOUNCEMENT_START", "25:00")
    with pytest.raises(ValueError, match="HOURLY_ANNOUNCEMENT_START"):
        Settings.from_env()


def test_config_rejects_hourly_groups_outside_managed_scope(monkeypatch):
    monkeypatch.setenv("MANAGED_GROUP_IDS", "1001")
    for name in ("STATS_GROUP_IDS", "DUPLICATE_GROUP_IDS", "GAME_GROUP_IDS", "ACTIVITY_GROUP_IDS"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("HOURLY_ANNOUNCEMENT_GROUP_IDS", "1002")
    with pytest.raises(ValueError, match="HOURLY_ANNOUNCEMENT_GROUP_IDS"):
        Settings.from_env()


def test_config_allows_zero_passive_cooldowns_and_repeat_interval(monkeypatch):
    monkeypatch.setenv("BOT_RANDOM_REACTION_COOLDOWN_SECONDS", "0")
    monkeypatch.setenv("BOT_RANDOM_REPEAT_COOLDOWN_SECONDS", "0")
    monkeypatch.setenv("BOT_RANDOM_REPEAT_MESSAGE_INTERVAL", "0")

    config = Settings.from_env()

    assert config.random_reaction_cooldown_seconds == 0
    assert config.random_repeat_cooldown_seconds == 0
    assert config.random_repeat_message_interval == 0


def test_config_maps_repeat_values_to_random_reaction_group_order(monkeypatch):
    monkeypatch.setenv("MANAGED_GROUP_IDS", "1001,1002")
    monkeypatch.setenv("ASOUL_BILI_GROUP_IDS", "")
    monkeypatch.setenv("BOT_RANDOM_REACTION_GROUP_IDS", "1002,1001")
    monkeypatch.setenv("BOT_RANDOM_REPEAT_PROBABILITY", "0.01,0.02")
    monkeypatch.setenv("BOT_RANDOM_REPEAT_COOLDOWN_SECONDS", "120,240")
    monkeypatch.setenv("BOT_RANDOM_REPEAT_MESSAGE_INTERVAL", "10,20")
    monkeypatch.setenv("BOT_RANDOM_TRIPLE_REPEAT_ENABLED", "true,false")
    monkeypatch.setenv("BOT_RANDOM_TRIPLE_REPEAT_PROBABILITY", "0.3,0.4")

    config = Settings.from_env()

    assert config.random_repeat_probability_by_group == {1002: 0.01, 1001: 0.02}
    assert config.random_repeat_cooldown_seconds_by_group == {1002: 120, 1001: 240}
    assert config.random_repeat_message_interval_by_group == {1002: 10, 1001: 20}
    assert config.random_triple_repeat_enabled_by_group == {1002: True, 1001: False}
    assert config.random_triple_repeat_probability_by_group == {1002: 0.3, 1001: 0.4}


def test_config_rejects_repeat_value_count_that_does_not_match_groups(monkeypatch):
    monkeypatch.setenv("MANAGED_GROUP_IDS", "1001,1002")
    monkeypatch.setenv("ASOUL_BILI_GROUP_IDS", "")
    monkeypatch.setenv("BOT_RANDOM_REACTION_GROUP_IDS", "1001,1002")
    monkeypatch.setenv("BOT_RANDOM_REPEAT_PROBABILITY", "0.05")

    with pytest.raises(ValueError, match="BOT_RANDOM_REPEAT_PROBABILITY.*exactly one"):
        Settings.from_env()


def test_config_accepts_a_short_onebot_api_interval(monkeypatch):
    monkeypatch.setenv("BOT_ONEBOT_API_MIN_INTERVAL_SECONDS", "0.5")

    assert Settings.from_env().onebot_api_min_interval_seconds == 0.5


def test_config_defaults_and_validates_napcat_maintenance_interval(monkeypatch):
    monkeypatch.delenv("NAPCAT_MAINTENANCE_ENABLED", raising=False)
    monkeypatch.delenv("NAPCAT_MAINTENANCE_INTERVAL_SECONDS", raising=False)
    config = Settings.from_env()
    assert config.napcat_maintenance_enabled
    assert config.napcat_maintenance_interval_seconds == 30

    monkeypatch.setenv("NAPCAT_MAINTENANCE_INTERVAL_SECONDS", "9")
    with pytest.raises(ValueError, match="NAPCAT_MAINTENANCE_INTERVAL_SECONDS"):
        Settings.from_env()


def test_config_requires_official_credentials_when_official_transport_is_selected(monkeypatch):
    monkeypatch.setenv("BOT_TRANSPORT", "qq_openapi")
    monkeypatch.setenv("QQ_OPENAPI_APP_ID", "1903484661")
    monkeypatch.delenv("QQ_OPENAPI_TOKEN", raising=False)
    monkeypatch.delenv("QQ_OPENAPI_APP_SECRET", raising=False)
    with pytest.raises(ValueError, match="QQ_OPENAPI_TOKEN"):
        Settings.from_env()


def test_config_accepts_complete_official_transport(monkeypatch):
    monkeypatch.setenv("BOT_TRANSPORT", "qq_openapi")
    monkeypatch.setenv("QQ_OPENAPI_APP_ID", "1903484661")
    monkeypatch.setenv("QQ_OPENAPI_TOKEN", "example-token")
    monkeypatch.setenv("QQ_OPENAPI_APP_SECRET", "example-secret")
    monkeypatch.setenv("QQ_OPENAPI_PORT", "8081")
    config = Settings.from_env()
    assert config.transport == "qq_openapi"
    assert config.official_app_id == "1903484661"
    assert config.official_port == 8081


def test_config_rejects_non_http_zhijiang_schedule_source(monkeypatch):
    monkeypatch.setenv("ZHIJIANG_SCHEDULE_URL", "file:///not-a-schedule.json")
    with pytest.raises(ValueError, match="ZHIJIANG_SCHEDULE_URL"):
        Settings.from_env()
