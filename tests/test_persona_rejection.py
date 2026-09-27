"""Refusals and skill openers must sound like the active persona."""
from __future__ import annotations

from bot.services.tangtang_features import (
    classify_local_feature,
    classify_extra_feature,
    persona_rejection,
)


def test_rejection_differs_between_personas():
    for reason in ("unknown_skill", "group_disabled", "upstream_unavailable"):
        sugar = persona_rejection(reason, call_keyword="糖糖")
        denia = persona_rejection(reason, call_keyword="娅娅")
        assert sugar and denia
        assert sugar != denia


def test_unknown_skill_refusal_is_in_character():
    sugar = persona_rejection("unknown_skill", call_keyword="糖糖")
    denia = persona_rejection("unknown_skill", call_keyword="娅娅")
    assert "糖糖" in sugar
    assert "现编" in sugar or "不会" in sugar
    assert "唔" in denia
    assert "做不到" in denia
    # Neither persona should use the previous flat system phrasing.
    for text in (sugar, denia):
        assert "不能凭空执行" not in text
        assert "没有对应的本地技能" not in text


def test_extra_feature_openers_are_persona_aware():
    for text in (
        "看看转盘榜",
    ):
        extra = classify_extra_feature(text)
        assert extra is not None
        assert extra.line == ""


def test_local_classifier_openers_follow_the_persona():
    sugar = classify_local_feature("看看转盘榜", call_keyword="糖糖")
    denia = classify_local_feature("看看转盘榜", call_keyword="娅娅")
    assert sugar is not None and denia is not None
    assert sugar.line.startswith("好呀")
    assert "唔" in denia.line
    assert sugar.line != denia.line

    denia_ranking = classify_local_feature("看看发言排行", call_keyword="娅娅")
    assert denia_ranking is not None
    assert "糖糖" not in denia_ranking.line
