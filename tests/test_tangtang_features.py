from __future__ import annotations

import asyncio
from types import SimpleNamespace

import nonebot

nonebot.init()

from bot.application.local_features import (
    FeatureRequest,
    feature_label,
    request_from_decision,
    run_feature_call,
)
from bot.services.tangtang_chat import TangtangConfig
from bot.services.tangtang_features import (
    FeatureDecision,
    TangtangFeatureClassifier,
    classify_local_feature,
    has_feature_hint,
)


def test_feature_hint_prefilter():
    assert has_feature_hint("糖糖看一下今天有谁在播？")
    assert has_feature_hint("糖糖今天群里发言情况怎么样")
    assert has_feature_hint("糖糖看一下a海岸这个月的发言榜")
    assert has_feature_hint("糖糖看看现在有哪些活动")
    assert has_feature_hint("糖糖看下这周直播日程")
    assert has_feature_hint("糖糖明天有人直播吗")
    assert has_feature_hint("糖糖今天有直播吗")
    assert not has_feature_hint("糖糖今天天气怎么样")
    assert not has_feature_hint("糖糖在吗")


def test_explicit_ranking_requests_are_routed_locally_with_persona_feedback():
    for text in ("糖糖给我看发言榜", "糖糖我要看发言排行", "糖糖发言排行"):
        decision = classify_local_feature(text)
        assert decision is not None
        assert decision.action == "group_ranking"
        assert decision.scope == "day"
        assert decision.line == "好呀，糖糖这就看看群里今天谁最能聊。"

    coast = classify_local_feature("糖糖帮我看A海岸这个月的发言榜")
    assert coast is not None
    assert coast.action == "a_coast_ranking"
    assert coast.scope == "month"
    assert coast.a_coast is True
    assert coast.line == "好呀，糖糖这就看看A海岸本月谁最能聊。"


def test_ranking_mentions_without_a_request_still_use_ai_router():
    assert classify_local_feature("糖糖觉得今天的发言榜好看吗") is None


def test_parse_clear_maybe_chat_and_invalid_outputs():
    clear = TangtangFeatureClassifier._parse(
        '{"decision":"clear","action":"today_live","scope":"","a_coast":false,'
        '"line":"今天的直播给你找出来啦。"}'
    )
    assert clear is not None
    assert clear.tier == "clear"
    assert clear.action == "today_live"
    assert clear.line == "今天的直播给你找出来啦。"

    maybe = TangtangFeatureClassifier._parse(
        '{"decision":"maybe","action":"a_coast_ranking","scope":"month",'
        '"a_coast":true,"line":"你要是想看A海岸这个月发言榜的话，我给你排一排。"}'
    )
    assert maybe is not None
    assert maybe.tier == "maybe"
    assert maybe.action == "a_coast_ranking"
    assert maybe.scope == "month"
    assert maybe.a_coast is True

    assert TangtangFeatureClassifier._parse('{"decision":"chat"}') is None
    assert TangtangFeatureClassifier._parse("不是 JSON") is None
    assert TangtangFeatureClassifier._parse(
        '{"decision":"clear","action":"unknown","line":"你好"}'
    ) is None
    assert TangtangFeatureClassifier._parse(
        '{"decision":"clear","action":"today_live","line":""}'
    ) is None


def test_parse_normalizes_ranking_scope_and_a_coast():
    group = TangtangFeatureClassifier._parse(
        '{"decision":"clear","action":"group_ranking","scope":"week",'
        '"a_coast":false,"line":"本周发言榜来了。"}'
    )
    assert group is not None
    assert group.action == "group_ranking"
    assert group.scope == "week"

    forced_coast = TangtangFeatureClassifier._parse(
        '{"decision":"clear","action":"group_ranking","scope":"month",'
        '"a_coast":true,"line":"这个月A海岸的发言榜来了。"}'
    )
    assert forced_coast is not None
    assert forced_coast.action == "a_coast_ranking"
    assert forced_coast.a_coast is True

    defaulted = TangtangFeatureClassifier._parse(
        '{"decision":"maybe","action":"group_ranking","scope":"",'
        '"a_coast":false,"line":"发言情况我给你排一排。"}'
    )
    assert defaulted is not None
    assert defaulted.scope == "day"


def test_classifier_returns_decision_and_usage():
    class FakeProvider:
        def __init__(self, response):
            self.response = response
            self.calls = 0

        async def generate(self, config, persona, prompt):
            self.calls += 1
            return self.response, {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "reasoning_tokens": 0,
                "total_tokens": 15,
            }

    provider = FakeProvider(
        '{"decision":"maybe","action":"today_live","scope":"",'
        '"a_coast":false,"line":"你要是想看今天有谁直播的话，我给你找找。"}'
    )
    classifier = TangtangFeatureClassifier(provider=provider)
    decision, usage = asyncio.run(
        classifier.classify(TangtangConfig.disabled("test"), "糖糖今天有直播吗")
    )
    assert provider.calls == 1
    assert decision is not None
    assert decision.tier == "maybe"
    assert decision.action == "today_live"
    assert usage["total_tokens"] == 15


def test_request_from_decision_maps_scope_to_chinese():
    decision = FeatureDecision(
        tier="clear",
        action="a_coast_ranking",
        scope="month",
        a_coast=True,
        line="本月发言榜来了。",
    )
    request = request_from_decision(decision)
    assert request.action == "ranking"
    assert request.args == "月"
    assert request.a_coast is True
    assert feature_label(request) == "A海岸发言排行 月"


def test_run_feature_call_dispatches_to_shared_helpers(monkeypatch):
    from bot.application import local_features

    calls: list[tuple[str, object]] = []

    async def fake_handler(matcher, bot, event, request):
        del matcher, bot, event
        calls.append((request.action, (request.args, request.a_coast)))

    for action in (
        "zhijiang_schedule",
        "today_live",
        "tomorrow_live",
        "week_live",
        "activity_hall",
        "ranking",
    ):
        monkeypatch.setitem(local_features._handlers, action, fake_handler)

    matcher = SimpleNamespace()
    bot = SimpleNamespace()
    event = SimpleNamespace()
    requests = [
        FeatureRequest("zhijiang_schedule", "状态"),
        FeatureRequest("today_live"),
        FeatureRequest("tomorrow_live"),
        FeatureRequest("week_live"),
        FeatureRequest("activity_hall"),
        FeatureRequest("ranking", "周", a_coast=True),
    ]
    for request in requests:
        asyncio.run(run_feature_call(matcher, bot, event, request))

    assert calls == [
        ("zhijiang_schedule", ("状态", False)),
        ("today_live", ("", False)),
        ("tomorrow_live", ("", False)),
        ("week_live", ("", False)),
        ("activity_hall", ("", False)),
        ("ranking", ("周", True)),
    ]
