from dataclasses import replace

import pytest

from tangtang_harness.chat_settings import chat_settings_status, parse_chat_settings
from tangtang_harness.config import HarnessConfig, ModelProfile


@pytest.fixture
def config(tmp_path):
    return HarnessConfig(root=tmp_path, active_model="m3", profiles=(
        ModelProfile("m2", "模型 2", "custom", "model-a", "https://example.invalid"),
        ModelProfile("m3", "模型 3", "custom", "model-b", "https://example.invalid")),
        extra={"proactive_by_group": {"201": {"proactive_probability": .1, "proactive_strategy": "legacy"}}})


@pytest.mark.parametrize("text", ["#糖糖模型", "#糖糖模型 状态", "糖糖模型 status", "#模型 列表"])
def test_model_status_keeps_current_profile_and_lists_real_models(config, text):
    command = parse_chat_settings(text, config)
    assert command.changes == {}
    assert command.requires_operator
    status = chat_settings_status(command, config)
    assert "当前模型：模型 3" in status and "模型 2" in status and "model-b" in status


def test_model_switch_accepts_complete_profile_name(config):
    assert parse_chat_settings("#糖糖模型 模型 2", config).changes == {"active_model": "m2"}


@pytest.mark.parametrize("raw,expected", [("50%", .5), ("50", .5), ("0.5", .5), ("1%", .01), ("0%", 0), ("100%", 1)])
def test_probability_commands_keep_percentage_semantics_and_update_group_overrides(config, raw, expected):
    command = parse_chat_settings("#主动回复 概率 " + raw, config)
    extra = command.changes["extra"]
    assert extra["proactive_probability"] == expected
    assert extra["proactive_by_group"]["201"]["proactive_probability"] == expected
    assert config.extra["proactive_by_group"]["201"]["proactive_probability"] == .1


def test_cooldown_uses_minutes_and_interval_uses_messages(config):
    cooldown = parse_chat_settings("#糖糖主动回复 冷却 30", config)
    assert cooldown.changes["extra"]["proactive_cooldown_seconds"] == 1800
    assert cooldown.changes["extra"]["proactive_by_group"]["201"]["proactive_cooldown_seconds"] == 1800
    assert parse_chat_settings("#糖糖主动 间隔 50", config).changes["extra"]["proactive_message_interval"] == 50


@pytest.mark.parametrize("target,expected", [("", {"201": "active_v1"}), ("202", {"201": "legacy", "202": "active_v1"}), ("全部", {"201": "active_v1", "202": "active_v1"})])
def test_strategy_uses_current_explicit_or_all_groups(config, target, expected):
    command = parse_chat_settings("#主动回复 策略 活跃群 " + target, config,
                                  group_id=201, managed_group_ids=(201, 202))
    extra = command.changes["extra"]
    assert {group: values["proactive_strategy"] for group, values in extra["proactive_by_group"].items()} == expected
    assert "proactive_strategy" not in extra or target == "全部"
    assert config.extra["proactive_by_group"]["201"]["proactive_strategy"] == "legacy"


@pytest.mark.parametrize("suffix", ["概率 101%", "冷却 1441", "间隔 -1", "策略 活跃群 999", "策略 奇怪策略 全部"])
def test_invalid_management_parameters_do_not_produce_mutations(config, suffix):
    with pytest.raises(ValueError):
        parse_chat_settings("#主动回复 " + suffix, config, group_id=201, managed_group_ids=(201, 202))


def test_system_gate_commands_preserve_private_only_scope(config):
    command = parse_chat_settings("#系统设置 被呼叫会话 关", config)
    assert command.private_only and command.changes == {"mention_chat_enabled": False}
    changed = replace(config, **command.changes)
    assert chat_settings_status(command, changed) == "被呼叫会话：关"


def test_proactive_status_shows_effective_group_policies(config):
    command = parse_chat_settings("#主动回复 状态", config)
    status = chat_settings_status(command, config, managed_group_ids=(201, 202))
    assert "201：旧规则" in status and "202：活跃群" in status
