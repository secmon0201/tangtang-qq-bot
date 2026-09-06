from __future__ import annotations

from bot.services.tangtang_humanize import humanize_messages, humanize_text


def test_strips_chatbot_closers_without_changing_claims():
    assert humanize_text("嘉然今晚有直播。希望以上信息对你有帮助！") == "嘉然今晚有直播。"
    assert humanize_text("当然可以。如有其他问题欢迎随时问我～") == "当然可以。"
    assert humanize_text("很高兴帮到你。") == ""


def test_strips_praise_hooks_and_fake_candid_openers():
    assert humanize_text("你说得太对了！这个切片确实有意思。") == "这个切片确实有意思。"
    assert humanize_text("说实话，我也觉得一般。") == "我也觉得一般。"
    assert humanize_text("好问题！糖糖觉得是两码事。") == "糖糖觉得是两码事。"


def test_strips_unsourced_attributions_and_filler_prefixes():
    assert humanize_text("综上所述，这条信息有误。") == "这条信息有误。"
    assert humanize_text("需要注意的是，明天还有一场。") == "明天还有一场。"
    assert humanize_text("有研究表明这个说法不成立。") == "这个说法不成立。"
    assert humanize_text("此外，明天还有一场。") == "明天还有一场。"


def test_strips_knowledge_cutoff_disclaimers():
    assert humanize_text("据我所知，这条有误。") == "这条有误。"
    assert humanize_text("基于可用信息，目前没有直播。") == "目前没有直播。"
    assert humanize_text("以我目前所知，还没官宣。") == "还没官宣。"


def test_collapses_stacked_qualifiers_and_punctuation_runs():
    assert humanize_text("可能大概也许明天有。") == "可能明天有。"
    assert humanize_text("应该，可能，是的。") == "应该是的。"
    assert humanize_text("啊啊啊！！！！！") == "啊啊啊！"


def test_keeps_plain_casual_text_and_quotes_untouched():
    assert humanize_text("说实话我也想笑。") == "说实话我也想笑。"
    assert humanize_text("他说“好问题”，然后笑了。") == "他说“好问题”，然后笑了。"
    assert humanize_text("这个确实有意思。") == "这个确实有意思。"


def test_humanize_messages_drops_emptied_bubbles_and_keeps_order():
    cleaned = humanize_messages(
        ["说实话，第一条。", "希望以上信息对你有帮助！", "第二条！！", "讲真，第三条。"]
    )
    assert cleaned == ("第一条。", "第二条！", "第三条。")
