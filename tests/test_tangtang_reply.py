from __future__ import annotations

from bot.services.tangtang_reply import parse_reply_plan, reply_style_instruction


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


def test_longer_reply_tiers_have_lower_default_weight():
    assert "极短" in reply_style_instruction("随便聊聊", 0.10)
    assert "偏短" in reply_style_instruction("随便聊聊", 0.50)
    assert "稍展开" in reply_style_instruction("随便聊聊", 0.90)
    assert "少见的长回答" in reply_style_instruction("随便聊聊", 0.99)
    assert "需要解释" in reply_style_instruction("请详细分析一下", 0.10)
    assert "15 字" in reply_style_instruction("随便聊聊", 0.10)
    assert "16 字" in reply_style_instruction("随便聊聊", 0.50)
    assert "长短交错" in reply_style_instruction("随便聊聊", 0.90)
