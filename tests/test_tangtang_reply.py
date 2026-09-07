from __future__ import annotations

from bot.services.tangtang_reply import (
    parse_reply_plan,
    reply_bubble_limit,
    reply_style_instruction,
)


def test_parse_marked_reply_into_ordered_bubbles():
    plan = parse_reply_plan("[接话]\n[消息]哈哈哈\n[消息]这个确实很好笑")
    assert plan.decided
    assert plan.messages == ("哈哈哈", "这个确实很好笑")
    assert plan.text == "哈哈哈\n这个确实很好笑"


def test_parse_json_reply_and_silence():
    plan = parse_reply_plan(
        '[接话]\n{"decision":"reply","messages":["嘿嘿","我知道啦"]}'
    )
    assert plan.messages == ("嘿嘿", "我知道啦")
    assert not parse_reply_plan("[沉默]").decided
    assert not parse_reply_plan('{"decision":"silent","messages":[]}').decided


def test_reply_plan_is_bounded_without_punctuation_splitting():
    plan = parse_reply_plan(
        "[接话]\n[消息]第一条\n[消息]第二条\n[消息]第三条",
        max_bubbles=2,
        max_chars=8,
    )
    assert plan.messages == ("第一条", "第二条")
    fallback = parse_reply_plan("[接话]\n一句话，不按逗号切")
    assert fallback.messages == ("一句话，不按逗号切",)
    assert parse_reply_plan("[接话]\n很长", max_chars=1).messages == ("…",)


def test_ordinary_reply_tiers_keep_group_chat_short():
    assert "极短" in reply_style_instruction("随便聊聊", 0.10)
    assert "4-12 字" in reply_style_instruction("随便聊聊", 0.50)
    assert "稍多说一点" in reply_style_instruction("随便聊聊", 0.95)
    assert "超过 20 字" in reply_style_instruction("随便聊聊", 0.95)
    assert "明确要求详细说明" in reply_style_instruction("请详细分析一下", 0.10)


def test_ordinary_chat_is_capped_at_two_bubbles_but_detail_can_use_configured_limit():
    assert reply_bubble_limit("为什么会这样", 6) == 2
    assert reply_bubble_limit("随便聊聊", 1) == 1
    assert reply_bubble_limit("请详细分析一下", 6) == 6
