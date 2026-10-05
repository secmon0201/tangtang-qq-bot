import pytest

from tangtang_harness.router import Router, route
from tangtang_harness.types import InboundEvent


def event(text, mentions=()):
    return InboundEvent("1", 999, 101, 201, text,
        segments=tuple({"type": "at", "data": {"qq": str(user)}} for user in mentions))


@pytest.mark.parametrize("text,name,args", [
    ("#发言排行 总", "ranking", {"scope": "total", "cluster": False}),
    ("#帮助", "user_help", {}),
    ("#帮助文字", "user_help", {"format": "text"}),
    ("#管理员帮助", "user_help", {"audience": "admin"}),
    ("#超级管理员帮助", "user_help", {"audience": "admin"}),
    ("#装弹 成语 娱乐 180", "idiom_bomb_load", {"mode": "entertainment", "duration": 180}),
    ("#装弹成语 专业 120", "idiom_bomb_load", {"mode": "professional", "duration": 120}),
    ("#bili_test_atall", "bili_test", {"kind": "atall"}),
    ("#nte查询", "external_game", {"text": "#nte查询"}),
    ("#ww帮助", "external_game", {"text": "#ww帮助"}),
    ("#糖糖模型 模型1", "chat_settings", {"text": "糖糖模型 模型1"}),
    ("#人格 成长 列表", "growth_manage", {"text": "成长 列表"}),
    ("#系统设置 小游戏 全局 关", "system_settings", {"key": "mini_games_enabled", "action": "set", "value": False}),
    ("#整点报时 时段 08:00 23:00", "system_settings", {"key": "hourly_schedule", "action": "set", "value": ["08:00", "23:00"]}),
    ("#合成集群发言统计周", "ranking", {"scope": "week", "cluster": True, "cluster_name": "合成集群"}),
    ("#合成集群统计 总榜", "ranking", {"scope": "total", "cluster": True, "cluster_name": "合成集群"}),
    ("#系统设置 ww 开", "system_settings", {"key": "game_api_enabled", "action": "set", "value": True}),
    ("#系统设置 NTE 关", "system_settings", {"key": "game_api_enabled", "action": "set", "value": False}),
    ("#白名单 remove 102", "whitelist", {"action": "remove", "user_id": 102, "note": ""}),
    ("#群设置 功能", "group_feature_status", {}),
    ("#群设置 功能 小游戏 开", "group_settings", {"action": "set", "feature": "小游戏", "enabled": True}),
    ("#群设置 开 小游戏", "group_settings", {"action": "set", "feature": "小游戏", "enabled": True}),
    ("#本群设置 关闭 人格成长", "group_settings", {"action": "set", "feature": "人格成长", "enabled": False}),
])
def test_commands(text, name, args):
    decision = Router().route(event(text))
    assert decision.kind == "tool"
    assert decision.tools[0].name == name
    assert decision.tools[0].arguments == args


@pytest.mark.parametrize("text", ["#NTEbot查询 然后顺便", "ww帮助", "# wwbot卡片参数 并且最后", "NTE查询"])
def test_upstream_game_command_is_forwarded_as_one_unmodified_request(text):
    decision = route(text, event(text))
    assert decision.kind == "tool"
    assert len(decision.tools) == 1
    assert decision.tools[0].name == "external_game"
    assert decision.tools[0].arguments == {"text": text}


@pytest.mark.parametrize("text", ["#发言排行 星球", "#合成集群统计 月 周", "#白名单 写入 102"])
def test_invalid_local_command_parameters_clarify_instead_of_executing_defaults(text):
    assert route(text, event(text)).kind == "clarification"


def test_multistep_mixed_and_unresolved_requests_are_complete():
    text = "查一下今天发言排行，然后查看我的缘分，最后分析一下活跃程度"
    decision = route(text, event(text))
    assert decision.kind == "mixed"
    assert [call.name for call in decision.tools] == ["ranking", "wife_personal"]
    assert "分析" in decision.chat_text
    unresolved = route("看今天发言排行，然后清空星球", event(""))
    assert unresolved.kind == "clarification"
    assert not unresolved.tools


def test_negative_instruction_never_executes_partial_plan():
    decision = route("不要查今天发言排行，然后查看我的缘分", event(""))
    assert not decision.tools


def test_target_requires_real_single_mention():
    assert route("#丢给 @用户", event("")).kind == "clarification"
    decision = route("画蛇添足 #丢给", event("", [102]))
    assert decision.tools[0].name == "idiom_bomb_pass"
    assert decision.tools[0].arguments == {"target_user_id": 102, "idiom": "画蛇添足"}


@pytest.mark.parametrize("text,name", [("来个表情包", "expression_send"), ("发个开心表情", "expression_send"), ("第二名是谁", "tool_followup"), ("#排行后续 第二名是谁", "tool_followup"), ("#记忆 遗忘 画画", "memory_manage"), ("#画像生成", "profile_generate")])
def test_local_followups_and_memory_are_routed(text, name):
    decision = route(text, event(text))
    assert decision.kind == "tool"
    assert decision.tools[0].name == name


@pytest.mark.parametrize("text", ["不发个表情包，然后看今天发言排行", "看今天发言排行，然后别抽图", "不要发个开心表情", "别抽图，然后看我的缘分"])
def test_negative_compounds_have_no_executable_tools(text):
    assert not route(text, event(text)).tools


@pytest.mark.parametrize("parameter", ["表情", "复读", "三连"])
@pytest.mark.parametrize("value", ["开", "关"])
def test_passive_group_switch_routes_to_group_configuration(parameter, value):
    text = f"#系统设置 被动互动 201 {parameter} {value}"
    decision = route(text, event(text))
    assert decision.kind == "tool"
    assert decision.tools[0].arguments == {"key": "passive:201", "action": "configure", "parameter": parameter, "value": value}


@pytest.mark.parametrize("value", ["0%", "1%", "50%", "100%"])
def test_passive_group_probability_preserves_percentage(value):
    text = f"#系统设置 被动互动 202 表情概率 {value}"
    decision = route(text, event(text))
    assert decision.tools[0].arguments == {"key": "passive:202", "action": "configure", "parameter": "表情概率", "value": value}


def test_passive_group_status_has_an_explicit_target():
    text = "#系统设置 被动互动 201 状态"
    decision = route(text, event(text))
    assert decision.tools[0].arguments == {"key": "passive:201", "action": "configure", "parameter": "状态", "value": None}


@pytest.mark.parametrize("suffix", ["", "开", "201", "201 表情", "状态", "invalid 表情 开", "0 表情 开", "201 表情 开 202"])
def test_incomplete_passive_group_configuration_clarifies_without_execution(suffix):
    text = "#系统设置 被动互动 " + suffix
    decision = route(text, event(text))
    assert decision.kind == "clarification" and not decision.tools
