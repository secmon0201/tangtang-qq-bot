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
    assert not has_feature_hint("糖糖看看现在有哪些活动")
    assert has_feature_hint("糖糖看下这周直播日程")
    assert has_feature_hint("糖糖明天有人直播吗")
    assert has_feature_hint("糖糖今天有直播吗")
    assert not has_feature_hint("糖糖今天天气怎么样")
    assert not has_feature_hint("糖糖在吗")


def test_explicit_ranking_requests_are_routed_locally_with_persona_feedback():
    for text in ("糖糖发言排行",):
        decision = classify_local_feature(text)
        assert decision is not None
        assert decision.action == "group_ranking"
        assert decision.scope == "day"
        assert decision.line == "好呀，糖糖这就看看群里今天谁最能聊。"

    coast = classify_local_feature(
        "糖糖看A海岸这个月的发言榜", cluster_labels=("A海岸",)
    )
    assert coast is not None
    assert coast.action == "cluster_ranking"
    assert coast.scope == "month"
    assert coast.cluster is True
    assert coast.line == "好呀，糖糖这就看看当前集群本月谁最能聊。"


def test_personal_references_never_switch_ranking_away_from_the_group():
    for text, scope in (
        ("糖糖给我看本月发言榜", "month"),
        ("糖糖看看我的本周发言排行", "week"),
        ("糖糖看看某人本周发言统计", "week"),
        ("糖糖个人发言排行", "day"),
    ):
        decision = classify_local_feature(text)
        assert decision is not None
        assert decision.action == "group_ranking"
        assert decision.scope == scope
        assert decision.cluster is False

    assert classify_local_feature("糖糖看看我这周在五个群说了多少") is None


def test_ranking_mentions_without_a_request_still_use_ai_router():
    assert classify_local_feature("糖糖觉得今天的发言榜好看吗") is None


def test_mini_game_rankings_route_deterministically():
    cases = {
        "看看转盘榜": ("mini_game_roulette", "群"),
        "看一下转盘总榜": ("mini_game_roulette", "总"),
        "炸弹榜发一下": ("mini_game_bomb", "群"),
        "骰子总榜": ("mini_game_dice", "总"),
        "猜数榜看看": ("mini_game_guess", "群"),
    }
    for text, (action, scope) in cases.items():
        decision = classify_local_feature(text)
        assert decision is not None, text
        assert decision.action == action
        assert decision.scope == scope


def test_game_rank_requests_route_deterministically():
    nte = classify_local_feature("看一下异环最强排行")
    assert nte is not None and nte.action == "nte_rank" and nte.scope == "群"
    nte_total = classify_local_feature("异环总排行")
    assert nte_total is not None and nte_total.action == "nte_rank" and nte_total.scope == "总"
    wuwa = classify_local_feature("看看鸣潮最强排行")
    assert wuwa is not None and wuwa.action == "wuwa_rank" and wuwa.scope == "群"
    wuwa_total = classify_local_feature("鸣潮bot排行")
    assert wuwa_total is not None and wuwa_total.action == "wuwa_rank" and wuwa_total.scope == "总"


def test_game_actions_keep_their_scope_through_request_mapping():
    from bot.services.tangtang_features import FeatureDecision

    for action in (
        "mini_game_roulette",
        "mini_game_dice",
        "nte_rank",
        "wuwa_rank",
    ):
        request = request_from_decision(
            FeatureDecision("clear", action, "总", False, "line")
        )
        assert request.args == "总"
        local = request_from_decision(
            FeatureDecision("clear", action, "群", False, "line")
        )
        assert local.args == "群"


def test_parse_clear_maybe_chat_and_invalid_outputs():
    clear = TangtangFeatureClassifier._parse(
        '{"decision":"clear","action":"today_live","scope":"","cluster":false,'
        '"line":"今天的直播给你找出来啦。"}'
    )
    assert clear is not None
    assert clear.tier == "clear"
    assert clear.action == "today_live"
    assert clear.line == "今天的直播给你找出来啦。"

    maybe = TangtangFeatureClassifier._parse(
        '{"decision":"maybe","action":"cluster_ranking","scope":"month",'
        '"cluster":true,"line":"你要是想看当前集群这个月发言榜的话，我给你排一排。"}'
    )
    assert maybe is not None
    assert maybe.tier == "maybe"
    assert maybe.action == "cluster_ranking"
    assert maybe.scope == "month"
    assert maybe.cluster is True

    assert TangtangFeatureClassifier._parse('{"decision":"chat"}') is None
    assert TangtangFeatureClassifier._parse("不是 JSON") is None
    assert TangtangFeatureClassifier._parse(
        '{"decision":"clear","action":"unknown","line":"你好"}'
    ) is None
    assert TangtangFeatureClassifier._parse(
        '{"decision":"clear","action":"personal_stats","scope":"week",'
        '"cluster":true,"line":"我来看看。"}'
    ) is None
    assert TangtangFeatureClassifier._parse(
        '{"decision":"clear","action":"today_live","line":""}'
    ) is None


def test_parse_normalizes_ranking_scope_and_cluster():
    group = TangtangFeatureClassifier._parse(
        '{"decision":"clear","action":"group_ranking","scope":"week",'
        '"cluster":false,"line":"本周发言榜来了。"}'
    )
    assert group is not None
    assert group.action == "group_ranking"
    assert group.scope == "week"

    forced_cluster = TangtangFeatureClassifier._parse(
        '{"decision":"clear","action":"group_ranking","scope":"month",'
        '"cluster":true,"line":"这个月集群发言榜来了。"}'
    )
    assert forced_cluster is not None
    assert forced_cluster.action == "cluster_ranking"
    assert forced_cluster.cluster is True

    defaulted = TangtangFeatureClassifier._parse(
        '{"decision":"maybe","action":"group_ranking","scope":"",'
        '"cluster":false,"line":"发言情况我给你排一排。"}'
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
        '"cluster":false,"line":"你要是想看今天有谁直播的话，我给你找找。"}'
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
        action="cluster_ranking",
        scope="month",
        cluster=True,
        line="本月发言榜来了。",
    )
    request = request_from_decision(decision)
    assert request.action == "ranking"
    assert request.args == "月"
    assert request.cluster is True
    assert feature_label(request) == "集群发言排行 月"


def test_run_feature_call_dispatches_to_shared_helpers(monkeypatch):
    from bot.application import local_features

    calls: list[tuple[str, object]] = []

    async def fake_handler(matcher, bot, event, request):
        del matcher, bot, event
        calls.append((request.action, (request.args, request.cluster)))

    for action in (
        "zhijiang_schedule",
        "today_live",
        "tomorrow_live",
        "week_live",
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
        FeatureRequest("ranking", "周", cluster=True),
    ]
    for request in requests:
        asyncio.run(run_feature_call(matcher, bot, event, request))

    assert calls == [
        ("zhijiang_schedule", ("状态", False)),
        ("today_live", ("", False)),
        ("tomorrow_live", ("", False)),
        ("week_live", ("", False)),
        ("ranking", ("周", True)),
    ]
