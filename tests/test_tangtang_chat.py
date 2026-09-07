from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from time import time

import pytest
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageSegment

from bot.plugins.tangtang_chat import is_call_event, is_proactive_event
from bot.services.replies import has_reply_segment
from bot.services.tangtang_chat import (
    AgentResult,
    RESOURCE_DIR,
    TOOL_SCHEMAS,
    TangtangConfig,
    TangtangConfigLoader,
    TangtangProvider,
    TangtangService,
    classify_call,
    parse_decision,
    render_message_text,
    resolve_at_labels,
)
from bot.services.tangtang_db import TangtangDb
from bot.services.tangtang_media import ImageReference, MediaResolution, VisionImage


def group_message(
    *,
    group_id: int,
    to_me: bool = False,
    text: str = "test",
    timestamp: int | None = None,
    user_id: int = 3,
    message_id: int = 4,
) -> GroupMessageEvent:
    return GroupMessageEvent.model_validate(
        {
            "time": int(time()) if timestamp is None else timestamp,
            "self_id": 2,
            "post_type": "message",
            "sub_type": "normal",
            "user_id": user_id,
            "message_type": "group",
            "message_id": message_id,
            "message": text,
            "original_message": text,
            "raw_message": text,
            "font": 14,
            "sender": {
                "user_id": user_id,
                "nickname": "tester",
                "card": "",
                "sex": "unknown",
                "age": 0,
                "area": "",
                "level": "",
                "role": "member",
                "title": "",
            },
            "to_me": to_me,
            "group_id": group_id,
        }
    )


def enabled_config(**overrides) -> TangtangConfig:
    values = {
        "TANGTANG_ENABLED": "true",
        "TANGTANG_MODE": "d",
        "TANGTANG_CALL_KEYWORD": "糖糖",
        "TANGTANG_GROUP_IDS": "1001",
        "TANGTANG_IGNORE_PROBABILITY": "0.10",
        "TANGTANG_C_PROBABILITY": "0.40",
        "TANGTANG_API_URL": "https://example.invalid/responses",
        "TANGTANG_API_KEY": "test-only",
        "TANGTANG_API_STYLE": "responses",
        "TANGTANG_MODEL": "deepseek-v4-flash",
        "TANGTANG_REASONING_EFFORT": "none",
        "TANGTANG_MAX_OUTPUT_TOKENS": "128",
    }
    values.update(overrides)
    return TangtangConfig.from_values(values, (1001, 1002))


class FakeProvider:
    def __init__(
        self,
        response: str = "[接话]\n好的呀",
        tool_sequence: list[tuple[str, list[dict]]] | None = None,
    ) -> None:
        self.response = response
        self.tool_sequence = tool_sequence or []
        self.calls = 0
        self.histories: list[list[dict]] = []
        self.prompts: list[str] = []
        self.images: list[tuple[VisionImage, ...]] = []

    async def generate(self, config, persona, prompt, images=()):
        self.calls += 1
        self.images.append(tuple(images))
        return self.response, {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "reasoning_tokens": 0,
            "total_tokens": 15,
            "latency_ms": 100,
        }

    async def generate_agent(
        self, config, persona, prompt, tools=(), history=(), images=()
    ):
        self.calls += 1
        self.prompts.append(prompt)
        self.histories.append(list(history))
        self.images.append(tuple(images))
        if self.tool_sequence and self.calls <= len(self.tool_sequence):
            text, calls = self.tool_sequence[self.calls - 1]
            return AgentResult(
                text=text,
                tool_calls=tuple(calls),
                usage={
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "reasoning_tokens": 0,
                    "total_tokens": 15,
                    "latency_ms": 100,
                },
            )
        return AgentResult(text=self.response, tool_calls=(), usage={
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "reasoning_tokens": 0,
            "total_tokens": 15,
            "latency_ms": 100,
        })


def make_service(
    tmp_path: Path,
    monkeypatch,
    response: str = "[接话]\n好的呀",
    provider: FakeProvider | None = None,
):
    resource_dir = tmp_path / "resources"
    usage_dir = tmp_path / "usage"
    resource_dir.mkdir()
    (resource_dir / "hard_blacklist.txt").write_text("# empty\n", encoding="utf-8")
    (resource_dir / "soft_blacklist.txt").write_text("# empty\n", encoding="utf-8")
    (resource_dir / "lines.txt").write_text("台词一\n台词二\n", encoding="utf-8")
    (resource_dir / "persona.md").write_text("你是糖糖。", encoding="utf-8")
    provider = provider or FakeProvider(response)
    service = TangtangService(
        loader=SimpleNamespace(load=lambda: enabled_config()),
        db=TangtangDb(tmp_path / "tangtang.db"),
        provider=provider,
        resource_dir=resource_dir,
        usage_dir=usage_dir,
    )
    sent: list = []
    monkeypatch.setattr("bot.services.tangtang_chat.random.random", lambda: 0.5)

    async def fake_send(bot, api, **kwargs):
        sent.append(kwargs["message"])

    monkeypatch.setattr("bot.services.tangtang_chat.call_qq_action", fake_send)
    return service, sent, provider, usage_dir


def usage_events(usage_dir: Path) -> list[dict]:
    events: list[dict] = []
    for path in usage_dir.glob("*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            events.append(json.loads(line))
    return events


def enable_plugin_group_features(monkeypatch, *group_ids: int) -> None:
    enabled = tuple(group_ids or (1001,))
    domains = SimpleNamespace(
        all_group_ids=lambda: enabled,
        feature_enabled=lambda group_id, feature: int(group_id) in enabled,
        effective_feature_enabled=lambda group_id, feature: int(group_id) in enabled,
    )
    monkeypatch.setattr("bot.plugins.tangtang_chat.group_domains", lambda: domains)
    monkeypatch.setattr(
        "bot.plugins.tangtang_chat.passive_settings",
        lambda: SimpleNamespace(is_chat_globally_enabled=lambda _feature: True),
    )


def test_config_validation():
    config = enabled_config()
    assert config.enabled and config.mode == "d"
    assert config.call_keyword == "糖糖"
    assert config.ignore_probability == 0.10
    assert config.soft_blacklist_ignore_probability == 0.0
    disabled = TangtangConfig.from_values({"TANGTANG_ENABLED": "false"}, (1001,))
    assert not disabled.enabled
    assert disabled.ignore_probability == 0.05
    import pytest

    with pytest.raises(ValueError):
        enabled_config(TANGTANG_MODE="x")
    with pytest.raises(ValueError):
        enabled_config(TANGTANG_GROUP_IDS="9999")
    with pytest.raises(ValueError):
        enabled_config(TANGTANG_API_KEY="")


def test_proactive_config_defaults_and_validation():
    config = enabled_config()
    assert config.proactive_enabled is False
    assert config.proactive_probability == 0.50
    assert config.proactive_cooldown_seconds == 900
    assert config.proactive_message_interval == 30
    assert config.humanize_enabled is True

    config = enabled_config(TANGTANG_HUMANIZE_ENABLED="false")
    assert config.humanize_enabled is False

    config = enabled_config(
        TANGTANG_PROACTIVE_ENABLED="true",
        TANGTANG_PROACTIVE_PROBABILITY="0.05",
        TANGTANG_PROACTIVE_COOLDOWN_SECONDS="600",
        TANGTANG_PROACTIVE_MESSAGE_INTERVAL="50",
    )
    assert config.proactive_enabled is True
    assert config.proactive_probability == 0.05
    assert config.proactive_cooldown_seconds == 600
    assert config.proactive_message_interval == 50

    with pytest.raises(ValueError):
        enabled_config(TANGTANG_PROACTIVE_PROBABILITY="1.1")
    with pytest.raises(ValueError):
        enabled_config(TANGTANG_PROACTIVE_COOLDOWN_SECONDS="86401")
    with pytest.raises(ValueError):
        enabled_config(TANGTANG_PROACTIVE_MESSAGE_INTERVAL="10001")


def test_proactive_config_maps_values_to_tangtang_group_order():
    config = enabled_config(
        TANGTANG_GROUP_IDS="1002,1001",
        TANGTANG_PROACTIVE_PROBABILITY="0.01,0.05",
        TANGTANG_PROACTIVE_COOLDOWN_SECONDS="60,1800",
        TANGTANG_PROACTIVE_MESSAGE_INTERVAL="5,35",
    )

    assert config.group_order == (1002, 1001)
    assert config.proactive_values_for(1002) == (0.01, 60, 5)
    assert config.proactive_values_for(1001) == (0.05, 1800, 35)

    with pytest.raises(ValueError, match="TANGTANG_PROACTIVE_PROBABILITY.*exactly one"):
        enabled_config(
            TANGTANG_GROUP_IDS="1001,1002",
            TANGTANG_PROACTIVE_PROBABILITY="0.05",
        )


def test_call_ignore_probability_can_be_overridden_per_group():
    config = enabled_config(
        TANGTANG_GROUP_IDS="1002,1001",
        TANGTANG_IGNORE_PROBABILITY="0.20",
        TANGTANG_CALL_IGNORE_PROBABILITY="0.20,0",
    )

    assert config.call_ignore_probability_for(1002) == 0.20
    assert config.call_ignore_probability_for(1001) == 0.0

    with pytest.raises(ValueError, match="TANGTANG_CALL_IGNORE_PROBABILITY.*exactly one"):
        enabled_config(
            TANGTANG_GROUP_IDS="1001,1002",
            TANGTANG_CALL_IGNORE_PROBABILITY="0",
        )


def test_proactive_db_claim_respects_interval_probability_and_cooldown(tmp_path):
    db = TangtangDb(tmp_path / "tangtang.db")
    for _ in range(29):
        assert db.claim_proactive_reply(1001, 2000, 0.1, 1800, 30, 0.0) == "message_interval"
    assert db.claim_proactive_reply(1001, 2000, 0.1, 1800, 30, 0.2) == "probability"
    assert db.claim_proactive_reply(1001, 2000, 0.1, 1800, 30, 0.0) == "claimed"
    assert db.claim_proactive_reply(1001, 2001, 0.1, 1800, 30, 0.0) == "cooldown"


def test_config_loader_hot_reloads(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "TANGTANG_ENABLED=true\n"
        "TANGTANG_GROUP_IDS=1001\n"
        "TANGTANG_API_URL=https://example.invalid/responses\n"
        "TANGTANG_API_KEY=test-only\n"
        "TANGTANG_MODEL=deepseek-v4-flash\n",
        encoding="utf-8",
    )
    loader = TangtangConfigLoader(env_path, managed_group_ids=(1001,))
    assert loader.load().enabled is True
    env_path.write_text("TANGTANG_ENABLED=false\n", encoding="utf-8")
    assert loader.load().enabled is False


def test_classify_call():
    assert classify_call("糖糖，嘉然今天怎么样？") == "question"
    assert classify_call("糖糖帮我看看") == "question"
    assert classify_call("糖糖在吗") == "casual"
    assert classify_call("糖糖 晚上好") == "casual"
    assert classify_call("糖糖今天天气不错") == "statement"


def test_parse_decision():
    assert parse_decision("[接话]\n你好呀") == (True, "你好呀")
    assert parse_decision("[沉默]") == (False, "")
    assert parse_decision("沉默 不想说") == (False, "")
    assert parse_decision("随便说点什么") == (True, "随便说点什么")
    assert parse_decision("让我想想，这条值得接。[接话]\n在的呀") == (True, "在的呀")
    assert parse_decision("先判断一下……[沉默]") == (False, "")
    assert parse_decision("这条不该沉默，[接话]\n聊聊吧") == (True, "聊聊吧")
    assert parse_decision("先想想……[沉默] 不对，[接话]\n在的呀") == (True, "在的呀")
    assert parse_decision("先想想……[接话] 算了，[沉默]") == (False, "")


def test_extract_text_skips_reasoning_items():
    data = {
        "output": [
            {"type": "reasoning", "content": [{"type": "reasoning_text", "text": "思考过程[接话]"}]},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "哈哈，在的呀"}],
            },
        ]
    }
    assert TangtangProvider._extract_text(data) == "哈哈，在的呀"


def test_responses_payload_respects_reasoning_effort():
    config = enabled_config(TANGTANG_REASONING_EFFORT="none")
    payload = TangtangProvider._responses_payload(config, "persona", "prompt")
    assert payload["reasoning"] == {"effort": "none"}
    config = enabled_config(TANGTANG_REASONING_EFFORT="high")
    payload = TangtangProvider._responses_payload(config, "persona", "prompt")
    assert payload["reasoning"] == {"effort": "high"}


def test_chat_payload_respects_reasoning_effort():
    config = enabled_config(
        TANGTANG_API_STYLE="chat_completions",
        TANGTANG_REASONING_EFFORT="none",
    )
    payload = TangtangProvider._chat_payload(config, "persona", "prompt")
    assert payload["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in payload
    config = enabled_config(
        TANGTANG_API_STYLE="chat_completions",
        TANGTANG_REASONING_EFFORT="low",
    )
    payload = TangtangProvider._chat_payload(config, "persona", "prompt")
    assert payload["thinking"] == {"type": "enabled"}
    assert payload["reasoning_effort"] == "low"


def test_reasoning_effort_accepts_official_deepseek_values():
    for effort in ("none", "low", "high", "max"):
        config = enabled_config(TANGTANG_REASONING_EFFORT=effort)
        assert config.reasoning_effort == effort
    import pytest

    for effort in ("minimal", "medium", "middle"):
        with pytest.raises(ValueError):
            enabled_config(TANGTANG_REASONING_EFFORT=effort)


def test_call_event_rule(monkeypatch):
    enable_plugin_group_features(monkeypatch, 1001)
    monkeypatch.setattr(
        "bot.plugins.tangtang_chat.loader",
        SimpleNamespace(load=lambda: enabled_config()),
    )
    assert is_call_event(group_message(group_id=1001, text="糖糖在吗"))
    assert is_call_event(group_message(group_id=1001, to_me=True, text="hello"))
    assert not is_call_event(group_message(group_id=1001, text="普通消息"))
    assert not is_call_event(group_message(group_id=1002, text="糖糖在吗"))
    assert not is_call_event(group_message(group_id=1001, text="#糖糖"))
    assert not is_call_event(group_message(group_id=1001, text="nte帮助"))
    assert not is_call_event(group_message(group_id=1001, text="NTE角色列表"))
    assert not is_call_event(
        group_message(group_id=1001, text="糖糖在吗", timestamp=int(time()) - 300)
    )
    assert not is_call_event(group_message(group_id=1001, text="报名 517"))


def test_proactive_event_rule(monkeypatch):
    enable_plugin_group_features(monkeypatch, 1001)
    monkeypatch.setattr("bot.plugins.tangtang_chat.automation_is_paused", lambda: False)
    monkeypatch.setattr(
        "bot.plugins.tangtang_chat.loader",
        SimpleNamespace(
            load=lambda: enabled_config(TANGTANG_PROACTIVE_ENABLED="true")
        ),
    )
    assert is_proactive_event(group_message(group_id=1001, text="今天天气不错"))
    assert not is_proactive_event(group_message(group_id=1001, text="糖糖在吗"))
    assert not is_proactive_event(group_message(group_id=1001, to_me=True, text="hello"))
    assert not is_proactive_event(group_message(group_id=1001, text="#帮助"))
    assert not is_proactive_event(group_message(group_id=1001, text="nte帮助"))
    assert not is_proactive_event(group_message(group_id=1001, text="NTE角色列表"))
    assert not is_proactive_event(group_message(group_id=1002, text="今天天气不错"))
    assert not is_proactive_event(
        group_message(group_id=1001, text="今天天气不错", timestamp=int(time()) - 300)
    )

    monkeypatch.setattr(
        "bot.plugins.tangtang_chat.loader",
        SimpleNamespace(load=lambda: enabled_config()),
    )
    monkeypatch.setattr(
        "bot.plugins.tangtang_chat.group_domains",
        lambda: SimpleNamespace(
            all_group_ids=lambda: (1001,),
            feature_enabled=lambda _group_id, feature: feature != "proactive_chat",
            effective_feature_enabled=lambda _group_id, _feature: True,
        ),
    )
    assert not is_proactive_event(group_message(group_id=1001, text="今天天气不错"))


def test_global_chat_switches_gate_call_and_proactive_rules(monkeypatch):
    enable_plugin_group_features(monkeypatch, 1001)
    monkeypatch.setattr("bot.plugins.tangtang_chat.automation_is_paused", lambda: False)
    monkeypatch.setattr(
        "bot.plugins.tangtang_chat.loader",
        SimpleNamespace(
            load=lambda: enabled_config(TANGTANG_PROACTIVE_ENABLED="true")
        ),
    )

    enabled = {"mention_chat": False, "proactive_chat": True}
    monkeypatch.setattr(
        "bot.plugins.tangtang_chat.passive_settings",
        lambda: SimpleNamespace(
            is_chat_globally_enabled=lambda feature: enabled[feature]
        ),
    )
    assert not is_call_event(group_message(group_id=1001, text="糖糖在吗"))
    assert is_proactive_event(group_message(group_id=1001, text="今天天气不错"))

    enabled.update(mention_chat=True, proactive_chat=False)
    assert is_call_event(group_message(group_id=1001, text="糖糖在吗"))
    assert not is_proactive_event(group_message(group_id=1001, text="今天天气不错"))


def test_proactive_reply_sends_unquoted_and_records_mode(tmp_path, monkeypatch):
    service, sent, provider, usage_dir = make_service(tmp_path, monkeypatch)
    config = enabled_config(
        TANGTANG_PROACTIVE_ENABLED="true",
        TANGTANG_PROACTIVE_PROBABILITY="1.0",
        TANGTANG_PROACTIVE_COOLDOWN_SECONDS="0",
        TANGTANG_PROACTIVE_MESSAGE_INTERVAL="0",
    )
    event = group_message(group_id=1001, text="今天天气不错")
    asyncio.run(service.handle_proactive(SimpleNamespace(self_id=2), event, config))

    assert provider.calls == 1
    assert len(sent) == 1
    assert sent[0] == "好的呀"
    assert not has_reply_segment(sent[0])
    rows = service.db.list_calls(3, 1001)
    assert rows[0]["reply_kind"] == "proactive"
    assert rows[0]["mode"] == "proactive"
    history = service.db.model_reply_lines(3, 1001, 10, 2000)
    assert "今天天气不错" in history
    assert "好的呀" in history
    events = usage_events(usage_dir)
    assert events[-1]["event"] == "proactive_reply"
    assert events[-1]["mode"] == "proactive"


def test_proactive_prompt_uses_proactive_header(tmp_path, monkeypatch):
    service, _sent, _provider, _usage = make_service(tmp_path, monkeypatch)
    event = group_message(group_id=1001, text="今天天气不错")
    prompt = service._build_prompt(event, enabled_config(), proactive=True)
    assert "[当前群友发言]" in prompt
    assert "没有人呼叫糖糖" in prompt
    assert "普通聊天默认只发一条" in prompt
    assert "[当前呼叫]" not in prompt


def test_proactive_miss_is_not_logged(tmp_path, monkeypatch):
    service, sent, provider, usage_dir = make_service(tmp_path, monkeypatch)
    config = enabled_config(
        TANGTANG_PROACTIVE_ENABLED="true",
        TANGTANG_PROACTIVE_PROBABILITY="0.0",
        TANGTANG_PROACTIVE_COOLDOWN_SECONDS="0",
        TANGTANG_PROACTIVE_MESSAGE_INTERVAL="0",
    )
    event = group_message(group_id=1001, text="今天天气不错")
    asyncio.run(service.handle_proactive(SimpleNamespace(self_id=2), event, config))

    assert provider.calls == 0
    assert sent == []
    events = usage_events(usage_dir)
    assert not any(event["event"] == "proactive_miss" for event in events)


def test_proactive_blacklist_logs_matched_term(tmp_path, monkeypatch):
    service, sent, provider, usage_dir = make_service(tmp_path, monkeypatch)
    resource_dir = tmp_path / "resources"
    (resource_dir / "hard_blacklist.txt").write_text("违禁词\n", encoding="utf-8")
    config = enabled_config(TANGTANG_PROACTIVE_ENABLED="true")
    event = group_message(group_id=1001, text="普通消息 违禁词")
    asyncio.run(service.handle_proactive(SimpleNamespace(self_id=2), event, config))

    assert provider.calls == 0
    assert sent == []
    events = usage_events(usage_dir)
    assert events[-1]["event"] == "proactive_hard_block"
    assert events[-1]["detail"] == "term:违禁词"

    (resource_dir / "hard_blacklist.txt").write_text("# empty\n", encoding="utf-8")
    (resource_dir / "soft_blacklist.txt").write_text("敏感词\n", encoding="utf-8")
    event = group_message(group_id=1001, text="普通消息 敏感词")
    asyncio.run(service.handle_proactive(SimpleNamespace(self_id=2), event, config))

    events = usage_events(usage_dir)
    assert events[-1]["event"] == "proactive_skip"
    assert events[-1]["detail"] == "term:敏感词"


def test_feature_router_runs_before_model_and_records_feature_usage(tmp_path, monkeypatch):
    service, sent, provider, usage_dir = make_service(tmp_path, monkeypatch)
    router_calls: list[str] = []

    async def router(bot, event, config, text):
        router_calls.append(text)
        return True, {
            "prompt_tokens": 12,
            "completion_tokens": 4,
            "reasoning_tokens": 0,
            "total_tokens": 16,
        }

    service.feature_router = router
    event = group_message(group_id=1001, text="糖糖 今日直播")
    asyncio.run(service.handle(None, event, enabled_config()))

    assert router_calls == ["糖糖 今日直播"]
    assert provider.calls == 0
    events = usage_events(usage_dir)
    assert events[-1]["event"] == "feature"
    assert events[-1]["prompt_tokens"] == 12
    assert events[-1]["completion_tokens"] == 4


def test_feature_history_uses_feature_reply_kind(tmp_path, monkeypatch):
    service, _sent, _provider, _usage = make_service(tmp_path, monkeypatch)
    service.record_feature(
        group_id=1001,
        user_id=3,
        message_id="9",
        call_text="糖糖 今日直播",
        reply_text="已执行本地功能：今日直播",
    )
    rows = service.db.list_calls(3, 1001)
    assert len(rows) == 1
    assert rows[0]["reply_kind"] == "feature"
    assert rows[0]["mode"] == "feature"


def test_feature_router_sends_generated_line_before_executing(monkeypatch):
    from bot.plugins import tangtang_chat as plugin
    from bot.services.tangtang_features import FeatureDecision

    sent: list[str] = []
    executed: list[object] = []
    recorded: list[dict] = []
    enable_plugin_group_features(monkeypatch, 1001)

    class FakeMatcher:
        async def send(self, message: str) -> None:
            sent.append(message)

    async def fake_classify(config, text):
        return (
            FeatureDecision(
                tier="clear",
                action="today_live",
                scope="",
                a_coast=False,
                line="今天的直播给你找出来啦。",
            ),
            {"total_tokens": 3},
        )

    async def fake_run(matcher, bot, event, request):
        executed.append(request)
        return True

    def fake_record(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(plugin, "has_feature_hint", lambda text: True)
    monkeypatch.setattr(
        plugin, "feature_classifier", SimpleNamespace(classify=fake_classify)
    )
    monkeypatch.setattr(plugin, "tangtang_call", FakeMatcher())
    monkeypatch.setattr(plugin, "run_feature_call", fake_run)
    monkeypatch.setattr(plugin.service, "record_feature", fake_record)

    event = group_message(group_id=1001, text="糖糖今天有直播吗")
    handled, usage = asyncio.run(
        plugin._feature_router(None, event, enabled_config(), "糖糖今天有直播吗")
    )

    assert handled is True
    assert sent == ["今天的直播给你找出来啦。"]
    assert len(executed) == 1
    assert usage == {"total_tokens": 3}
    assert recorded[0]["reply_text"] == "今天的直播给你找出来啦。"


def test_first_person_ranking_router_still_runs_group_ranking(monkeypatch):
    from bot.plugins import tangtang_chat as plugin

    sequence: list[str] = []
    enable_plugin_group_features(monkeypatch, 1001)

    class FakeMatcher:
        async def send(self, message: str) -> None:
            sequence.append(f"line:{message}")

    async def unexpected_classify(config, text):
        raise AssertionError("explicit ranking request must not call the AI router")

    async def fake_run(matcher, bot, event, request):
        sequence.append(f"feature:{request.action}:{request.args}")
        return True

    monkeypatch.setattr(
        plugin, "feature_classifier", SimpleNamespace(classify=unexpected_classify)
    )
    monkeypatch.setattr(plugin, "tangtang_call", FakeMatcher())
    monkeypatch.setattr(plugin, "run_feature_call", fake_run)
    monkeypatch.setattr(plugin.service, "record_feature", lambda **kwargs: None)

    event = group_message(group_id=1001, text="糖糖我要看发言排行")
    handled, usage = asyncio.run(
        plugin._feature_router(None, event, enabled_config(), "糖糖我要看发言排行")
    )

    assert handled is True
    assert usage == {}
    assert sequence == [
        "line:好呀，糖糖这就看看群里今天谁最能聊。",
        "feature:ranking:日",
    ]


def test_mentioned_member_does_not_switch_ranking_away_from_the_group(monkeypatch):
    from bot.plugins import tangtang_chat as plugin

    sequence: list[str] = []
    enable_plugin_group_features(monkeypatch, 1001)

    class FakeMatcher:
        async def send(self, message: str) -> None:
            sequence.append(f"line:{message}")

    async def fake_run(matcher, bot, event, request):
        del matcher, bot, event
        sequence.append(f"feature:{request.action}:{request.args}")
        return True

    monkeypatch.setattr(plugin, "tangtang_call", FakeMatcher())
    monkeypatch.setattr(plugin, "run_feature_call", fake_run)
    monkeypatch.setattr(plugin.service, "record_feature", lambda **kwargs: None)
    event = SimpleNamespace(
        group_id=1001,
        user_id=42,
        message_id="test",
        message=Message([
            MessageSegment.text("糖糖看看他本月发言统计"),
            MessageSegment.at("903848042"),
        ]),
    )

    handled, usage = asyncio.run(
        plugin._feature_router(SimpleNamespace(self_id=2), event, enabled_config(), "糖糖看看他本月发言统计")
    )

    assert handled is True
    assert usage == {}
    assert sequence == [
        "line:好呀，糖糖这就看看群里本月谁最能聊。",
        "feature:ranking:月",
    ]


def test_tangtang_db_migrates_old_schema_for_feature(tmp_path):
    import sqlite3

    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE tangtang_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            message_id TEXT NOT NULL DEFAULT '',
            call_text TEXT NOT NULL,
            reply_text TEXT NOT NULL,
            reply_kind TEXT NOT NULL CHECK (reply_kind IN ('model', 'canned')),
            mode TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX idx_tangtang_calls_user_group
            ON tangtang_calls (user_id, group_id, id);
        """
    )
    conn.execute(
        "INSERT INTO tangtang_calls "
        "(group_id, user_id, message_id, call_text, reply_text, reply_kind, mode, created_at) "
        "VALUES (1001, 3, '1', '旧记录', '在呢', 'model', 'd', '2026-08-08T10:00:00+08:00')"
    )
    conn.commit()
    conn.close()

    db = TangtangDb(db_path)
    db.insert_call(
        group_id=1001,
        user_id=3,
        message_id="2",
        call_text="糖糖 今日直播",
        reply_text="已执行本地功能：今日直播",
        reply_kind="feature",
        mode="feature",
        created_at="2026-08-08T11:00:00+08:00",
    )
    rows = db.list_calls(3, 1001)
    assert [row["reply_kind"] for row in rows] == ["feature", "model"]


def test_parallel_compatibility_with_passive_matcher(monkeypatch):
    from bot.plugins.random_reactions import is_passive_reaction_event
    enable_plugin_group_features(monkeypatch, 1001)

    monkeypatch.setattr(
        "bot.plugins.tangtang_chat.loader",
        SimpleNamespace(load=lambda: enabled_config()),
    )
    monkeypatch.setattr(
        "bot.plugins.random_reactions.passive",
        SimpleNamespace(is_group_enabled=lambda group_id: group_id == 1001),
    )
    monkeypatch.setattr("bot.plugins.random_reactions.automation_is_paused", lambda: False)
    keyword_call = group_message(group_id=1001, text="糖糖在吗")
    assert is_call_event(keyword_call)
    assert is_passive_reaction_event(keyword_call)
    at_call = group_message(group_id=1001, to_me=True, text="hello")
    assert is_call_event(at_call)
    assert not is_passive_reaction_event(at_call)


def test_hard_blacklist_blocks_model_and_reply(tmp_path, monkeypatch):
    service, sent, provider, usage_dir = make_service(tmp_path, monkeypatch)
    resource_dir = tmp_path / "resources"
    (resource_dir / "hard_blacklist.txt").write_text("违禁词\n", encoding="utf-8")
    asyncio.run(service.handle(SimpleNamespace(self_id=2), group_message(group_id=1001, text="糖糖 违禁词"), enabled_config()))
    assert provider.calls == 0
    assert sent == []
    events = usage_events(usage_dir)
    assert any(event["event"] == "hard_block" for event in events)


def test_ignore_probability_skips_everything(tmp_path, monkeypatch):
    service, sent, provider, usage_dir = make_service(tmp_path, monkeypatch)
    monkeypatch.setattr("bot.services.tangtang_chat.random.random", lambda: 0.0)
    asyncio.run(service.handle(SimpleNamespace(self_id=2), group_message(group_id=1001, text="糖糖在吗"), enabled_config()))
    assert provider.calls == 0
    assert sent == []
    assert any(event["event"] == "skip" for event in usage_events(usage_dir))


def test_group_call_ignore_override_can_require_a_reply(tmp_path, monkeypatch):
    service, sent, provider, _usage_dir = make_service(tmp_path, monkeypatch)
    monkeypatch.setattr("bot.services.tangtang_chat.random.random", lambda: 0.0)
    config = enabled_config(
        TANGTANG_IGNORE_PROBABILITY="1.0",
        TANGTANG_CALL_IGNORE_PROBABILITY="0",
    )

    asyncio.run(
        service.handle(
            SimpleNamespace(self_id=2), group_message(group_id=1001, text="糖糖在吗"), config
        )
    )

    assert provider.calls == 1
    assert len(sent) == 1


def test_required_call_reply_falls_back_when_model_is_silent(tmp_path, monkeypatch):
    service, sent, provider, _usage_dir = make_service(tmp_path, monkeypatch, response="[沉默]")
    config = enabled_config(TANGTANG_REQUIRED_CALL_REPLY_GROUP_IDS="1001")

    asyncio.run(
        service.handle(
            SimpleNamespace(self_id=2), group_message(group_id=1001, text="糖糖在吗"), config
        )
    )

    assert provider.calls == 1
    assert len(sent) == 1


def test_soft_blacklist_uses_canned_line(tmp_path, monkeypatch):
    service, sent, provider, usage_dir = make_service(tmp_path, monkeypatch)
    resource_dir = tmp_path / "resources"
    (resource_dir / "soft_blacklist.txt").write_text("敏感词\n", encoding="utf-8")
    monkeypatch.setattr("bot.services.tangtang_chat.random.choice", lambda seq: seq[0])
    asyncio.run(service.handle(SimpleNamespace(self_id=2), group_message(group_id=1001, text="糖糖 敏感词"), enabled_config()))
    assert provider.calls == 0
    assert "台词一" in sent[0].extract_plain_text()
    assert any(event["event"] == "canned" for event in usage_events(usage_dir))


def test_soft_blacklist_can_be_independently_ignored(tmp_path, monkeypatch):
    service, sent, provider, usage_dir = make_service(tmp_path, monkeypatch)
    resource_dir = tmp_path / "resources"
    (resource_dir / "soft_blacklist.txt").write_text("敏感词\n", encoding="utf-8")
    monkeypatch.setattr("bot.services.tangtang_chat.random.random", lambda: 0.49)

    config = enabled_config(
        TANGTANG_IGNORE_PROBABILITY="0.0",
        TANGTANG_SOFT_BLACKLIST_IGNORE_PROBABILITY="0.50",
    )
    asyncio.run(
        service.handle(
            SimpleNamespace(self_id=2),
            group_message(group_id=1001, text="糖糖 敏感词"),
            config,
        )
    )

    assert provider.calls == 0
    assert sent == []
    assert any(event["event"] == "soft_blacklist_skip" for event in usage_events(usage_dir))


def test_soft_blacklist_ignore_probability_is_validated():
    assert enabled_config(
        TANGTANG_SOFT_BLACKLIST_IGNORE_PROBABILITY="0.50"
    ).soft_blacklist_ignore_probability == 0.50
    with pytest.raises(ValueError):
        enabled_config(TANGTANG_SOFT_BLACKLIST_IGNORE_PROBABILITY="1.1")


def test_same_called_text_is_merged_for_one_minute(tmp_path, monkeypatch):
    service, sent, provider, usage_dir = make_service(tmp_path, monkeypatch)
    config = enabled_config(TANGTANG_IGNORE_PROBABILITY="0.0")

    asyncio.run(
        service.handle(
            SimpleNamespace(self_id=2),
            group_message(group_id=1001, user_id=3, message_id=4, text="糖糖 在吗"),
            config,
        )
    )
    asyncio.run(
        service.handle(
            SimpleNamespace(self_id=2),
            group_message(group_id=1001, user_id=9, message_id=5, text="糖糖   在吗"),
            config,
        )
    )

    assert provider.calls == 1
    assert len(sent) == 1
    assert any(event["event"] == "call_repeat" for event in usage_events(usage_dir))


def test_exact_tangtang_reply_echo_is_skipped(tmp_path, monkeypatch):
    service, sent, provider, usage_dir = make_service(tmp_path, monkeypatch)
    service.db.insert_call(
        group_id=1001,
        user_id=3,
        message_id="old",
        call_text="糖糖 在吗",
        reply_text="糖糖在呢",
        reply_kind="model",
        mode="d",
        created_at="2026-08-10T13:00:00+08:00",
    )

    asyncio.run(
        service.handle(
            SimpleNamespace(self_id=2),
            group_message(group_id=1001, user_id=9, message_id=5, text="糖糖在呢"),
            enabled_config(TANGTANG_IGNORE_PROBABILITY="0.0"),
        )
    )

    assert provider.calls == 0
    assert sent == []
    assert any(event["event"] == "bot_reply_echo" for event in usage_events(usage_dir))


def test_reply_echo_check_ignores_replies_older_than_the_latest_ten(tmp_path, monkeypatch):
    service, sent, provider, usage_dir = make_service(tmp_path, monkeypatch)
    for index in range(11):
        service.db.insert_call(
            group_id=1001,
            user_id=3,
            message_id=str(index),
            call_text=f"糖糖 第{index}条",
            reply_text="糖糖在呢" if index == 0 else f"近期回复 {index}",
            reply_kind="model",
            mode="d",
            created_at="2026-08-10T13:00:00+08:00",
        )

    asyncio.run(
        service.handle(
            SimpleNamespace(self_id=2),
            group_message(group_id=1001, user_id=9, message_id=12, text="糖糖在呢"),
            enabled_config(TANGTANG_IGNORE_PROBABILITY="0.0"),
        )
    )

    assert provider.calls == 1
    assert len(sent) == 1
    assert not any(event["event"] == "bot_reply_echo" for event in usage_events(usage_dir))


def test_regex_blacklist_lines_support_patterns(tmp_path, monkeypatch):
    service, sent, provider, usage_dir = make_service(tmp_path, monkeypatch)
    resource_dir = tmp_path / "resources"
    (resource_dir / "hard_blacklist.txt").write_text(
        "re:q\\d{4,}\n", encoding="utf-8"
    )
    asyncio.run(
        service.handle(
            SimpleNamespace(self_id=2),
            group_message(group_id=1001, text="糖糖 q12345"),
            enabled_config(),
        )
    )
    assert provider.calls == 0
    assert sent == []
    assert any(event["event"] == "hard_block" for event in usage_events(usage_dir))

    (resource_dir / "hard_blacklist.txt").write_text("# empty\n", encoding="utf-8")
    (resource_dir / "soft_blacklist.txt").write_text(
        "re:wxid_[A-Za-z0-9_-]+\n", encoding="utf-8"
    )
    monkeypatch.setattr("bot.services.tangtang_chat.random.choice", lambda seq: seq[0])
    asyncio.run(
        service.handle(
            SimpleNamespace(self_id=2),
            group_message(group_id=1001, text="糖糖 wxid_abc123"),
            enabled_config(),
        )
    )
    assert provider.calls == 0
    assert "台词一" in sent[0].extract_plain_text()


def test_invalid_regex_line_is_ignored(tmp_path, monkeypatch):
    service, sent, provider, _usage = make_service(tmp_path, monkeypatch)
    resource_dir = tmp_path / "resources"
    (resource_dir / "hard_blacklist.txt").write_text(
        "re:(\n普通词\n", encoding="utf-8"
    )
    asyncio.run(
        service.handle(
            SimpleNamespace(self_id=2),
            group_message(group_id=1001, text="糖糖 普通词"),
            enabled_config(),
        )
    )
    assert provider.calls == 0

    (resource_dir / "hard_blacklist.txt").write_text("re:(\n", encoding="utf-8")
    asyncio.run(
        service.handle(
            SimpleNamespace(self_id=2),
            group_message(group_id=1001, text="糖糖 随便聊聊"),
            enabled_config(),
        )
    )
    assert provider.calls == 1


def test_pure_call_uses_canned_line(tmp_path, monkeypatch):
    service, sent, provider, usage_dir = make_service(tmp_path, monkeypatch)
    monkeypatch.setattr("bot.services.tangtang_chat.random.choice", lambda seq: seq[0])
    asyncio.run(service.handle(SimpleNamespace(self_id=2), group_message(group_id=1001, text="糖糖"), enabled_config()))
    assert provider.calls == 0
    assert "台词一" in sent[0].extract_plain_text()


def test_model_reply_records_history_and_usage(tmp_path, monkeypatch):
    service, sent, provider, usage_dir = make_service(tmp_path, monkeypatch)
    asyncio.run(service.handle(SimpleNamespace(self_id=2), group_message(group_id=1001, text="糖糖 今天天气怎么样？"), enabled_config()))
    assert provider.calls == 1
    assert "好的呀" in sent[0].extract_plain_text()
    rows = service.db.list_calls(3, 1001)
    assert len(rows) == 1
    assert rows[0]["reply_kind"] == "model"
    assert rows[0]["call_text"] == "糖糖 今天天气怎么样？"
    assert any(event["event"] == "reply" for event in usage_events(usage_dir))


def test_model_silent_does_not_send(tmp_path, monkeypatch):
    service, sent, provider, usage_dir = make_service(tmp_path, monkeypatch, response="[沉默]")
    asyncio.run(service.handle(SimpleNamespace(self_id=2), group_message(group_id=1001, text="糖糖 随便说说"), enabled_config()))
    assert provider.calls == 1
    assert sent == []
    assert any(event["event"] == "silent" for event in usage_events(usage_dir))


def test_c_mode_question_replies_and_casual_rolls(tmp_path, monkeypatch):
    config = enabled_config(TANGTANG_MODE="c")
    service, sent, provider, usage_dir = make_service(tmp_path, monkeypatch)
    monkeypatch.setattr("bot.services.tangtang_chat.random.random", lambda: 0.5)
    asyncio.run(service.handle(SimpleNamespace(self_id=2), group_message(group_id=1001, text="糖糖 几点开播？"), config))
    assert provider.calls == 1
    monkeypatch.setattr("bot.services.tangtang_chat.random.random", lambda: 0.5)
    asyncio.run(service.handle(SimpleNamespace(self_id=2), group_message(group_id=1001, text="糖糖 哈哈"), config))
    assert provider.calls == 1
    monkeypatch.setattr("bot.services.tangtang_chat.random.random", lambda: 0.2)
    asyncio.run(service.handle(SimpleNamespace(self_id=2), group_message(group_id=1001, text="糖糖 哈哈"), config))
    assert provider.calls == 1
    assert any(event["event"] == "call_repeat" for event in usage_events(usage_dir))


def test_history_context_includes_model_replies_only(tmp_path, monkeypatch):
    service, _sent, _provider, _usage = make_service(tmp_path, monkeypatch)
    service.db.insert_call(
        group_id=1001,
        user_id=3,
        message_id="1",
        call_text="糖糖在吗",
        reply_text="在呢",
        reply_kind="model",
        mode="d",
        created_at="2026-08-08T10:00:00+08:00",
    )
    service.db.insert_call(
        group_id=1001,
        user_id=3,
        message_id="2",
        call_text="糖糖 敏感词",
        reply_text="这条不聊",
        reply_kind="canned",
        mode="local",
        created_at="2026-08-08T10:01:00+08:00",
    )
    event = group_message(group_id=1001, text="糖糖 再聊两句")
    prompt = service._build_prompt(event, enabled_config())
    assert "在呢" in prompt
    assert "这条不聊" not in prompt
    assert "最近 30 条" in prompt
    assert "不要输出任何分析" in prompt


def test_prompt_always_keeps_current_call_and_format(tmp_path, monkeypatch):
    config = enabled_config(TANGTANG_MAX_INPUT_CHARS="400")
    service, _sent, _provider, _usage = make_service(tmp_path, monkeypatch)
    for index in range(10):
        service.record_group_message(
            1001, "tester", f"一条很长很长的群聊消息第{index}号" + "填充" * 40
        )
    service.db.insert_call(
        group_id=1001,
        user_id=3,
        message_id="9",
        call_text="糖糖在吗",
        reply_text="在呢" + "填充" * 60,
        reply_kind="model",
        mode="d",
        created_at="2026-08-08T10:00:00+08:00",
    )
    event = group_message(group_id=1001, text="糖糖 再聊两句")
    prompt = service._build_prompt(event, config)
    assert "[当前呼叫]" in prompt
    assert "糖糖 再聊两句" in prompt
    assert "输出格式" in prompt
    assert "不要每条都带" in prompt
    assert len(prompt) <= config.max_input_chars


def test_prompt_includes_mention_media_and_reply(tmp_path, monkeypatch):
    service, _sent, _provider, _usage = make_service(tmp_path, monkeypatch)
    sender = {
        "user_id": 3,
        "nickname": "tester",
        "card": "群名片",
        "sex": "unknown",
        "age": 0,
        "area": "",
        "level": "",
        "role": "member",
        "title": "",
    }
    event = GroupMessageEvent.model_validate(
        {
            "time": int(time()),
            "self_id": 2,
            "post_type": "message",
            "sub_type": "normal",
            "user_id": 3,
            "message_type": "group",
            "message_id": 4,
            "message": [
                {"type": "text", "data": {"text": "糖糖 看看这个"}},
                {"type": "image", "data": {"url": "https://example.invalid/x.png"}},
            ],
            "original_message": [
                {"type": "text", "data": {"text": "糖糖 看看这个"}},
                {"type": "image", "data": {"url": "https://example.invalid/x.png"}},
            ],
            "raw_message": "糖糖 看看这个[CQ:image,url=https://example.invalid/x.png]",
            "font": 14,
            "sender": sender,
            "to_me": False,
            "group_id": 1001,
            "reply": {
                "time": int(time()),
                "message_type": "group",
                "message_id": 5,
                "real_id": 5,
                "sender": sender,
                "message": [{"type": "text", "data": {"text": "被引用的内容"}}],
            },
        }
    )
    prompt = service._build_prompt(event, enabled_config())
    assert "说话人昵称：群名片" in prompt
    assert "被@状态：否" in prompt
    assert "消息媒体：图片" in prompt
    assert "引用消息：被引用的内容" in prompt

    at_event = group_message(group_id=1001, to_me=True, text="糖糖 在吗")
    assert "被@状态：是" in service._build_prompt(at_event, enabled_config())


def test_prompt_injects_local_zhijiang_knowledge_for_relevant_questions(tmp_path, monkeypatch):
    service, _sent, _provider, _usage = make_service(tmp_path, monkeypatch)
    event = group_message(group_id=1001, text="糖糖 嘉然是谁")
    prompt = service._build_prompt(event, enabled_config())
    assert "[本地枝江知识" in prompt
    assert "嘉然（Diana）" in prompt
    assert "[当前呼叫]" in prompt


def test_prompt_injects_local_mingchao_meme_knowledge_for_relevant_questions(tmp_path, monkeypatch):
    service, _sent, _provider, _usage = make_service(tmp_path, monkeypatch)
    event = group_message(group_id=1001, text="糖糖 鸣潮公式是什么")
    prompt = service._build_prompt(event, enabled_config())
    assert "[本地鸣潮梗文化" in prompt
    assert "oo是这样" in prompt
    assert "[当前呼叫]" in prompt


def test_prompt_does_not_inject_blocked_carol_knowledge(tmp_path, monkeypatch):
    from bot.services.zhijiang_knowledge import entries

    service, _sent, _provider, _usage = make_service(tmp_path, monkeypatch)
    event = group_message(group_id=1001, text="糖糖 珈乐是谁")
    prompt = service._build_prompt(event, enabled_config())
    assert "[本地枝江知识" not in prompt
    assert "[本地鸣潮梗文化" not in prompt
    event = group_message(group_id=1001, text="糖糖 珈乐和鸣潮有什么关系")
    prompt = service._build_prompt(event, enabled_config())
    knowledge_start = prompt.find("[本地枝江知识")
    knowledge_end = prompt.find("[当前呼叫]")
    if knowledge_start >= 0 and knowledge_end > knowledge_start:
        knowledge_part = prompt[knowledge_start:knowledge_end]
        assert "珈乐" not in knowledge_part
        assert "皇珈骑士" not in knowledge_part
    assert any(entry.entry_id == "carol-profile" and entry.blocked for entry in entries())


def test_prompt_keeps_call_and_local_knowledge_within_budget(tmp_path, monkeypatch):
    config = enabled_config(TANGTANG_MAX_INPUT_CHARS="700")
    service, _sent, _provider, _usage = make_service(tmp_path, monkeypatch)
    event = group_message(group_id=1001, text="糖糖 贝拉是谁")
    prompt = service._build_prompt(event, config)
    assert "[当前呼叫]" in prompt
    assert "[本地枝江知识" in prompt
    assert len(prompt) <= config.max_input_chars


def test_prompt_keeps_both_knowledge_sources_within_budget(tmp_path, monkeypatch):
    config = enabled_config(TANGTANG_MAX_INPUT_CHARS="1400")
    service, _sent, _provider, _usage = make_service(tmp_path, monkeypatch)
    event = group_message(group_id=1001, text="糖糖 贝拉和鸣潮有什么梗")
    prompt = service._build_prompt(event, config)
    assert "[当前呼叫]" in prompt
    assert "[本地枝江知识" in prompt
    assert "[本地鸣潮梗文化" in prompt
    assert len(prompt) <= config.max_input_chars


def test_agent_tool_config_defaults_and_override():
    config = enabled_config()
    assert config.tools_enabled is True
    assert config.tool_loop_max == 3
    assert enabled_config(TANGTANG_TOOLS_ENABLED="false").tools_enabled is False
    assert enabled_config(TANGTANG_TOOL_LOOP_MAX="2").tool_loop_max == 2


def test_agent_loop_calls_local_tool_then_answers(tmp_path, monkeypatch):
    tool_call = {
        "call_id": "call_1",
        "name": "search_zhijiang_knowledge",
        "arguments": '{"query": "嘉然是谁"}',
    }
    provider = FakeProvider(tool_sequence=[("", [tool_call])])
    service, sent, provider, usage_dir = make_service(
        tmp_path, monkeypatch, provider=provider
    )
    asyncio.run(
        service.handle(
            SimpleNamespace(self_id=2),
            group_message(group_id=1001, text="糖糖 嘉然是谁"),
            enabled_config(),
        )
    )
    assert provider.calls == 2
    assert "好的呀" in sent[0].extract_plain_text()
    assert provider.histories[1][0]["tool_calls"] == (tool_call,)
    outputs = provider.histories[1][0]["outputs"]
    assert any("嘉然（Diana）" in output["output"] for output in outputs)
    events = usage_events(usage_dir)
    assert events[-1]["event"] == "reply"
    assert events[-1]["prompt_tokens"] == 20


def test_agent_loop_calls_mingchao_tool(tmp_path, monkeypatch):
    tool_call = {
        "call_id": "call_2",
        "name": "search_mingchao_meme_culture",
        "arguments": '{"query": "鸣潮公式是什么"}',
    }
    provider = FakeProvider(tool_sequence=[("", [tool_call])])
    service, sent, provider, _usage = make_service(
        tmp_path, monkeypatch, provider=provider
    )
    asyncio.run(
        service.handle(
            SimpleNamespace(self_id=2),
            group_message(group_id=1001, text="糖糖 鸣潮公式是什么"),
            enabled_config(),
        )
    )
    assert provider.calls == 2
    assert "好的呀" in sent[0].extract_plain_text()
    outputs = provider.histories[1][0]["outputs"]
    assert any("oo是这样" in output["output"] for output in outputs)


def test_agent_tool_result_includes_cross_references(tmp_path, monkeypatch):
    tool_call = {
        "call_id": "call_ref",
        "name": "search_mingchao_meme_culture",
        "arguments": '{"query": "潮友"}',
    }
    provider = FakeProvider(tool_sequence=[("", [tool_call])])
    service, _sent, provider, _usage = make_service(
        tmp_path, monkeypatch, provider=provider
    )
    asyncio.run(
        service.handle(
            SimpleNamespace(self_id=2),
            group_message(group_id=1001, text="糖糖 潮友是什么"),
            enabled_config(),
        )
    )
    outputs = provider.histories[1][0]["outputs"]
    assert any("wuwaves-livestreams" in output["output"] for output in outputs)
    assert any("成员直播《鸣潮》时间线" in output["output"] for output in outputs)


def test_prompt_includes_cross_reference_hint(tmp_path, monkeypatch):
    service, _sent, _provider, _usage = make_service(tmp_path, monkeypatch)
    event = group_message(group_id=1001, text="糖糖 鸣潮直播时间线")
    prompt = service._build_prompt(event, enabled_config())
    assert "（相关：" in prompt


def test_carol_question_gets_llm_black_meme_instruction(tmp_path, monkeypatch):
    provider = FakeProvider(response="[接话]\n这个嘛，不谈这个fifa人物啦")
    service, sent, provider, _usage = make_service(tmp_path, monkeypatch, provider=provider)
    asyncio.run(
        service.handle(
            SimpleNamespace(self_id=2),
            group_message(group_id=1001, text="糖糖 珈乐是谁"),
            enabled_config(),
        )
    )
    assert provider.calls == 1
    assert "[黑梗应对]" in provider.prompts[0]
    assert "fifa人物" in provider.prompts[0]
    reply = sent[0].extract_plain_text()
    assert "fifa人物" in reply
    assert "珈乐" not in reply


def test_huangjia_question_gets_llm_black_meme_instruction(tmp_path, monkeypatch):
    provider = FakeProvider(response="[接话]\n这个嘛，还是不谈这个fifa人物啦")
    service, sent, provider, _usage = make_service(tmp_path, monkeypatch, provider=provider)
    asyncio.run(
        service.handle(
            SimpleNamespace(self_id=2),
            group_message(group_id=1001, text="糖糖 皇珈骑士是什么"),
            enabled_config(),
        )
    )
    assert provider.calls == 1
    assert "[黑梗应对]" in provider.prompts[0]
    assert "fifa人物" in sent[0].extract_plain_text()


def test_lidian_question_gets_llm_black_meme_instruction(tmp_path, monkeypatch):
    provider = FakeProvider(response="[接话]\n人各有志，各自安好呀")
    service, sent, provider, _usage = make_service(tmp_path, monkeypatch, provider=provider)
    asyncio.run(
        service.handle(
            SimpleNamespace(self_id=2),
            group_message(group_id=1001, text="糖糖 李滇滇是谁"),
            enabled_config(),
        )
    )
    assert provider.calls == 1
    assert "人各有志，各自安好" in provider.prompts[0]
    assert "人各有志" in sent[0].extract_plain_text()


def test_proactive_skips_black_meme_text(tmp_path, monkeypatch):
    service, sent, provider, _usage = make_service(tmp_path, monkeypatch)
    config = enabled_config(TANGTANG_PROACTIVE_ENABLED="true")
    asyncio.run(
        service.handle_proactive(
            SimpleNamespace(self_id=2),
            group_message(group_id=1001, text="有人提到珈乐了"),
            config,
        )
    )
    assert sent == []


def test_agent_loop_respects_max_rounds(tmp_path, monkeypatch):
    tool_call = {
        "call_id": "call_loop",
        "name": "search_zhijiang_knowledge",
        "arguments": '{"query": "嘉然"}',
    }
    provider = FakeProvider(tool_sequence=[("", [tool_call]) for _ in range(5)])
    service, sent, provider, _usage = make_service(
        tmp_path, monkeypatch, provider=provider
    )
    config = enabled_config(TANGTANG_TOOL_LOOP_MAX="2")
    asyncio.run(
        service.handle(
            SimpleNamespace(self_id=2),
            group_message(group_id=1001, text="糖糖 嘉然"),
            config,
        )
    )
    assert provider.calls == 3
    assert sent == []


def test_responses_payload_supports_tools_and_history():
    config = enabled_config()
    history = (
        {
            "tool_calls": (
                {"call_id": "c1", "name": "search_zhijiang_knowledge", "arguments": "{}"},
            ),
            "outputs": ({"call_id": "c1", "output": '{"results": []}'},),
        },
    )
    payload = TangtangProvider._responses_payload(
        config, "persona", "prompt", tools=TOOL_SCHEMAS, history=history
    )
    assert payload["tools"][0]["name"] == "search_zhijiang_knowledge"
    assert payload["input"][2] == {
        "type": "function_call",
        "call_id": "c1",
        "name": "search_zhijiang_knowledge",
        "arguments": "{}",
    }
    assert payload["input"][3] == {
        "type": "function_call_output",
        "call_id": "c1",
        "output": '{"results": []}',
    }


def test_provider_payloads_include_real_multimodal_image_parts():
    config = enabled_config(TANGTANG_VISION_DETAIL="high")
    image = VisionImage(
        source="current",
        ordinal=1,
        data_url="data:image/jpeg;base64,dGVzdA==",
        mime_type="image/jpeg",
        sha256="a" * 64,
        byte_count=4,
        sender_id=3,
        sender_name="tester",
    )
    responses = TangtangProvider._responses_payload(
        config, "persona", "prompt", images=(image,)
    )
    user_parts = responses["input"][1]["content"]
    assert user_parts[-2] == {
        "type": "input_text",
        "text": "[当前消息图片 1（发送者：tester）]",
    }
    assert user_parts[-1] == {
        "type": "input_image",
        "image_url": image.data_url,
        "detail": "high",
    }
    chat = TangtangProvider._chat_payload(
        enabled_config(TANGTANG_API_STYLE="chat_completions"),
        "persona",
        "prompt",
        images=(image,),
    )
    assert chat["messages"][1]["content"][-2] == {
        "type": "text",
        "text": "[当前消息图片 1（发送者：tester）]",
    }
    assert chat["messages"][1]["content"][-1]["type"] == "image_url"


def test_model_reply_sends_ordered_bubbles_and_quotes_only_first(tmp_path, monkeypatch):
    service, sent, _provider, _usage = make_service(
        tmp_path,
        monkeypatch,
        response="[接话]\n[消息]哈哈哈\n[消息]这个确实很好笑",
    )
    config = enabled_config(
        TANGTANG_REPLY_DELAY_MIN_MS="0",
        TANGTANG_REPLY_DELAY_MAX_MS="0",
    )
    asyncio.run(
        service.handle(
            SimpleNamespace(self_id=2),
            group_message(group_id=1001, text="糖糖看这个"),
            config,
        )
    )
    assert len(sent) == 2
    assert has_reply_segment(sent[0])
    assert not has_reply_segment(sent[1])
    assert sent[0].extract_plain_text() == "哈哈哈"
    assert sent[1] == "这个确实很好笑"
    calls = service.db.list_calls(3, 1001)
    assert calls[0]["reply_text"] == "哈哈哈\n这个确实很好笑"
    parts = service.db.reply_parts(calls[0]["id"])
    assert [row["delivered"] for row in parts] == [1, 1]


def test_ordinary_model_reply_drops_bubbles_after_second(tmp_path, monkeypatch):
    service, sent, _provider, _usage = make_service(
        tmp_path,
        monkeypatch,
        response="[接话]\n[消息]第一条\n[消息]第二条\n[消息]第三条",
    )
    config = enabled_config(
        TANGTANG_REPLY_DELAY_MIN_MS="0",
        TANGTANG_REPLY_DELAY_MAX_MS="0",
    )
    asyncio.run(
        service.handle(
            SimpleNamespace(self_id=2),
            group_message(group_id=1001, text="糖糖随便聊聊"),
            config,
        )
    )
    assert sent[0].extract_plain_text() == "第一条"
    assert sent[1:] == ["第二条"]


def test_explicit_detail_request_keeps_configured_bubble_limit(tmp_path, monkeypatch):
    service, sent, _provider, _usage = make_service(
        tmp_path,
        monkeypatch,
        response="[接话]\n[消息]第一条\n[消息]第二条\n[消息]第三条",
    )
    config = enabled_config(
        TANGTANG_REPLY_MAX_BUBBLES="3",
        TANGTANG_REPLY_DELAY_MIN_MS="0",
        TANGTANG_REPLY_DELAY_MAX_MS="0",
    )
    asyncio.run(
        service.handle(
            SimpleNamespace(self_id=2),
            group_message(group_id=1001, text="糖糖详细分析一下"),
            config,
        )
    )
    assert sent[0].extract_plain_text() == "第一条"
    assert sent[1:] == ["第二条", "第三条"]


def test_model_reply_humanizes_ai_tells_before_sending(tmp_path, monkeypatch):
    service, sent, _provider, _usage = make_service(
        tmp_path,
        monkeypatch,
        response=(
            "[接话]\n[消息]说实话，这条切片有点意思。希望以上信息对你有帮助！\n"
            "[消息]可能大概也许明天还有新切片。"
        ),
    )
    config = enabled_config(
        TANGTANG_REPLY_DELAY_MIN_MS="0",
        TANGTANG_REPLY_DELAY_MAX_MS="0",
    )
    asyncio.run(
        service.handle(
            SimpleNamespace(self_id=2),
            group_message(group_id=1001, text="糖糖看这个"),
            config,
        )
    )
    assert len(sent) == 2
    assert sent[0].extract_plain_text() == "这条切片有点意思。"
    assert sent[1] == "可能明天还有新切片。"


def test_model_reply_keeps_ai_tells_when_humanize_disabled(tmp_path, monkeypatch):
    service, sent, _provider, _usage = make_service(
        tmp_path,
        monkeypatch,
        response="[接话]\n说实话，这条切片有点意思。",
    )
    config = enabled_config(
        TANGTANG_HUMANIZE_ENABLED="false",
        TANGTANG_REPLY_DELAY_MIN_MS="0",
        TANGTANG_REPLY_DELAY_MAX_MS="0",
    )
    asyncio.run(
        service.handle(
            SimpleNamespace(self_id=2),
            group_message(group_id=1001, text="糖糖看这个"),
            config,
        )
    )
    assert len(sent) == 1
    assert sent[0].extract_plain_text() == "说实话，这条切片有点意思。"


def test_partial_bubble_delivery_is_recorded_without_retrying_sent_parts(tmp_path, monkeypatch):
    service, _sent, _provider, usage_dir = make_service(
        tmp_path,
        monkeypatch,
        response="[接话]\n[消息]第一条\n[消息]第二条",
    )
    attempts = 0

    async def fail_second(bot, api, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise RuntimeError("send failed")
        return {"data": {"message_id": "sent-1"}}

    monkeypatch.setattr("bot.services.tangtang_chat.call_qq_action", fail_second)
    config = enabled_config(
        TANGTANG_REPLY_DELAY_MIN_MS="0",
        TANGTANG_REPLY_DELAY_MAX_MS="0",
    )
    asyncio.run(
        service.handle(
            SimpleNamespace(self_id=2),
            group_message(group_id=1001, text="糖糖测试分段"),
            config,
        )
    )
    assert attempts == 2
    call = service.db.list_calls(3, 1001)[0]
    assert call["reply_text"] == "第一条"
    parts = service.db.reply_parts(call["id"])
    assert [row["delivered"] for row in parts] == [1, 0]
    assert parts[0]["platform_message_id"] == "sent-1"
    assert usage_events(usage_dir)[-1]["detail"] == "partial_delivery"


def test_vision_image_is_forwarded_to_provider(tmp_path, monkeypatch):
    image = VisionImage(
        source="current",
        ordinal=1,
        data_url="data:image/jpeg;base64,dGVzdA==",
        mime_type="image/jpeg",
        sha256="a" * 64,
        byte_count=4,
    )

    class FakeResolver:
        async def resolve_references(self, references):
            assert tuple(references)
            return MediaResolution((image,), ())

    service, _sent, provider, _usage = make_service(tmp_path, monkeypatch)
    service.media_resolver = FakeResolver()
    event = group_message(group_id=1001, text="糖糖看看")
    event.message.append(
        MessageSegment("image", {"url": "https://example.test/image.png"})
    )
    asyncio.run(service.handle(SimpleNamespace(self_id=2), event, enabled_config()))
    assert provider.images[-1] == (image,)
    assert "已读取 当前消息图片 1" in provider.prompts[-1]


def test_context_images_keep_their_original_senders(tmp_path, monkeypatch):
    service, _sent, _provider, _usage = make_service(tmp_path, monkeypatch)
    service.record_group_message(
        1001,
        "甲",
        "[图片]",
        user_id=11,
        message_id="image-a",
        media_references=(ImageReference("current", 1, "https://example.test/a.png"),),
    )
    service.record_group_message(
        1001,
        "乙",
        "[图片]",
        user_id=22,
        message_id="image-b",
        media_references=(ImageReference("current", 1, "https://example.test/b.png"),),
    )

    references = service._context_image_references(
        1001,
        exclude_message_id="call",
        limit=2,
    )
    assert [(item.source, item.sender_id, item.sender_name) for item in references] == [
        ("context", 11, "甲"),
        ("context", 22, "乙"),
    ]

    resolution = MediaResolution(
        tuple(
            VisionImage(
                source=item.source,
                ordinal=item.ordinal,
                data_url="data:image/jpeg;base64,dGVzdA==",
                mime_type="image/jpeg",
                sha256=str(item.ordinal) * 64,
                byte_count=4,
                sender_id=item.sender_id,
                sender_name=item.sender_name,
            )
            for item in references
        ),
        (),
    )
    prompt = service._build_prompt(
        group_message(group_id=1001, text="糖糖刚才那些图是谁发的"),
        enabled_config(),
        media_resolution=resolution,
    )
    assert "最近群聊上下文图片 1（发送者：甲）" in prompt
    assert "最近群聊上下文图片 2（发送者：乙）" in prompt
    assert "不要把引用消息的发送者当成其他上下文图片的发送者" in prompt


def test_chat_payload_supports_tools_and_history():
    config = enabled_config(TANGTANG_API_STYLE="chat_completions")
    history = (
        {
            "tool_calls": (
                {"call_id": "c2", "name": "search_mingchao_meme_culture", "arguments": "{}"},
            ),
            "outputs": ({"call_id": "c2", "output": '{"results": []}'},),
        },
    )
    payload = TangtangProvider._chat_payload(
        config, "persona", "prompt", tools=TOOL_SCHEMAS, history=history
    )
    assert payload["tools"][0]["function"]["name"] == "search_zhijiang_knowledge"
    assert payload["messages"][2]["role"] == "assistant"
    assert (
        payload["messages"][2]["tool_calls"][0]["function"]["name"]
        == "search_mingchao_meme_culture"
    )
    assert payload["messages"][3]["role"] == "tool"
    assert payload["messages"][3]["tool_call_id"] == "c2"


def test_extract_tool_calls_from_responses_and_chat():
    responses_data = {
        "output": [
            {
                "type": "function_call",
                "call_id": "c1",
                "name": "search_zhijiang_knowledge",
                "arguments": '{"query": "嘉然"}',
            }
        ]
    }
    assert TangtangProvider._extract_tool_calls(responses_data) == (
        {"call_id": "c1", "name": "search_zhijiang_knowledge", "arguments": '{"query": "嘉然"}'},
    )
    chat_data = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "id": "c2",
                            "function": {
                                "name": "search_mingchao_meme_culture",
                                "arguments": "{}",
                            },
                        }
                    ]
                }
            }
        ]
    }
    calls = TangtangProvider._extract_tool_calls(chat_data)
    assert calls[0]["call_id"] == "c2"
    assert calls[0]["name"] == "search_mingchao_meme_culture"


def test_lines_pool_has_100_entries():
    lines = [
        line.strip()
        for line in (RESOURCE_DIR / "lines.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert len(lines) == 100
    assert all(len(line) <= 15 for line in lines)
    assert any("糖糖" not in line for line in lines)
    assert any("糖糖" in line for line in lines)


def test_render_message_text_keeps_at_mentions():
    message = (
        Message("糖糖锐评一下")
        + MessageSegment.at(123456)
        + Message("是谁呢")
    )
    assert (
        render_message_text(message, {"123456": "Secmon"})
        == "糖糖锐评一下@Secmon是谁呢"
    )
    assert render_message_text(message) == "糖糖锐评一下@123456是谁呢"
    assert render_message_text(Message("纯文本")) == "纯文本"


def test_resolve_at_labels_uses_member_info_and_all(monkeypatch):
    monkeypatch.setattr(
        "bot.services.tangtang_chat._local_member_name",
        lambda group_id, user_id: "",
    )

    class FakeBot:
        self_id = 2

        async def call_api(self, action, **params):
            assert action == "get_group_member_info"
            assert params == {"group_id": 1001, "user_id": 123456, "no_cache": False}
            return {"data": {"card": "Secmon", "nickname": "secmon"}}

    message = (
        Message("糖糖锐评一下")
        + MessageSegment.at(123456)
        + MessageSegment.at("all")
        + Message("是谁呢")
    )
    event = SimpleNamespace(group_id=1001, message=message)
    labels = asyncio.run(resolve_at_labels(FakeBot(), event))
    assert labels == {"123456": "Secmon", "all": "全体成员"}


def test_prompt_keeps_at_mention_label(tmp_path, monkeypatch):
    service, _sent, _provider, _usage = make_service(tmp_path, monkeypatch)
    sender = {
        "user_id": 3,
        "nickname": "何时是归年",
        "card": "",
        "sex": "unknown",
        "age": 0,
        "area": "",
        "level": "",
        "role": "member",
        "title": "",
    }
    event = GroupMessageEvent.model_validate(
        {
            "time": int(time()),
            "self_id": 2,
            "post_type": "message",
            "sub_type": "normal",
            "user_id": 3,
            "message_type": "group",
            "message_id": 4,
            "message": [
                {"type": "text", "data": {"text": "糖糖锐评一下"}},
                {"type": "at", "data": {"qq": "123456"}},
                {"type": "text", "data": {"text": "是谁呢"}},
            ],
            "original_message": [
                {"type": "text", "data": {"text": "糖糖锐评一下"}},
                {"type": "at", "data": {"qq": "123456"}},
                {"type": "text", "data": {"text": "是谁呢"}},
            ],
            "raw_message": "糖糖锐评一下[CQ:at,qq=123456]是谁呢",
            "font": 14,
            "sender": sender,
            "to_me": False,
            "group_id": 1001,
        }
    )
    prompt = service._build_prompt(
        event, enabled_config(), at_labels={"123456": "Secmon"}
    )
    assert "消息：糖糖锐评一下@Secmon是谁呢" in prompt


def test_high_history_chars_values_are_accepted():
    config = enabled_config(TANGTANG_HISTORY_CHARS="8192")
    assert config.history_chars == 8192
    config = enabled_config(TANGTANG_HISTORY_CHARS="24000")
    assert config.history_chars == 24000
    import pytest

    with pytest.raises(ValueError):
        enabled_config(TANGTANG_HISTORY_CHARS="24001")


def test_high_input_output_limits_are_accepted():
    config = enabled_config(
        TANGTANG_MAX_INPUT_CHARS="24000",
        TANGTANG_MAX_OUTPUT_TOKENS="24000",
        TANGTANG_MAX_RESPONSE_CHARS="24000",
    )
    assert config.max_input_chars == 24000
    assert config.max_output_tokens == 24000
    assert config.max_response_chars == 24000


def test_group_context_messages_configurable():
    assert enabled_config().group_context_messages == 30
    assert enabled_config(TANGTANG_GROUP_CONTEXT_MESSAGES="50").group_context_messages == 50
    import pytest

    with pytest.raises(ValueError):
        enabled_config(TANGTANG_GROUP_CONTEXT_MESSAGES="0")
    with pytest.raises(ValueError):
        enabled_config(TANGTANG_GROUP_CONTEXT_MESSAGES="101")


def test_prompt_uses_configured_group_context_window(tmp_path, monkeypatch):
    config = enabled_config(TANGTANG_GROUP_CONTEXT_MESSAGES="50")
    service, _sent, _provider, _usage = make_service(tmp_path, monkeypatch)
    for index in range(60):
        service.record_group_message(
            1001, "tester", f"气氛消息第{index}条", user_id=3, message_id=f"g{index}"
        )
    event = group_message(group_id=1001, text="糖糖在吗")
    prompt = service._build_prompt(event, config)
    assert "最近 50 条" in prompt
    assert "气氛消息第0条" not in prompt
    assert "气氛消息第59条" in prompt


def test_group_messages_persist_across_instances(tmp_path, monkeypatch):
    service, _sent, _provider, _usage = make_service(tmp_path, monkeypatch)
    service.record_group_message(
        1001, "何时是归年", "今天聊点啥", user_id=3, message_id="m1"
    )
    service.record_group_message(
        1001, "Secmon", "明天再说", user_id=9, message_id="m2"
    )

    second = TangtangService(
        loader=SimpleNamespace(load=lambda: enabled_config()),
        db=TangtangDb(tmp_path / "tangtang.db"),
        provider=FakeProvider(),
        resource_dir=tmp_path / "resources",
        usage_dir=tmp_path / "usage",
    )
    assert second._group_context_lines(1001, 30) == [
        "何时是归年: 今天聊点啥",
        "Secmon: 明天再说",
    ]


def test_user_history_is_per_user(tmp_path, monkeypatch):
    service, _sent, _provider, _usage = make_service(tmp_path, monkeypatch)
    service.record_group_message(
        1001, "何时是归年", "第一条", user_id=3, message_id="m1"
    )
    service.record_group_message(
        1001, "Secmon", "别人的话", user_id=9, message_id="m2"
    )
    service.record_group_message(
        1001, "何时是归年", "第二条", user_id=3, message_id="m3"
    )
    lines = service._user_history_lines(3, 1001, 20, 2000)
    assert "何时是归年：第一条" in lines
    assert "何时是归年：第二条" in lines
    assert "别人的话" not in lines


def test_prompt_includes_user_history_section(tmp_path, monkeypatch):
    service, _sent, _provider, _usage = make_service(tmp_path, monkeypatch)
    service.record_group_message(
        1001, "何时是归年", "我今天想聊枝江", user_id=3, message_id="m1"
    )
    service.record_group_message(
        1001, "Secmon", "无关的话", user_id=9, message_id="m2"
    )
    event = group_message(group_id=1001, text="糖糖在吗")
    prompt = service._build_prompt(event, enabled_config())
    assert "[该群友最近发言" in prompt
    assert "何时是归年：我今天想聊枝江" in prompt
    history_part = prompt.split("[该群友最近发言", 1)[1]
    assert "无关的话" not in history_part
