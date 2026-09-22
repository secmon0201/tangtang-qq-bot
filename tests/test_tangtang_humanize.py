from __future__ import annotations

import pytest

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


def test_keeps_evidence_limits_and_uncertainty():
    for text in ("据我所知，这条有误。", "基于可用信息，目前没有直播。",
                 "以我目前所知，还没官宣。", "根据目前公开的信息，时间尚未确定。",
                 "可能明天有，我还不确定。", "唔……让我想想——暂时不确定。"):
        assert humanize_text(text) == text


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


def test_preserves_quoted_text_code_and_meaningful_acceptance():
    for text in ('“好问题！！”是他原话。', '他说"说实话，可能也许"。',
                 '```python\nvalue = "可能也许！！"\n```', '`可能也许！！`',
                 '> 说实话，完全正确！！', '    print("！！")',
                 '礼物我收下了，谢谢你。', '我没听到，可以再说一次吗？'):
        assert humanize_text(text) == text.strip()


def test_denia_closers_are_cleaned_without_stripping_character_voice():
    assert humanize_text('已经找到。如有其他问题欢迎随时问娅娅。') == '已经找到。'
    for text in ('唔，看到了——', '唔，这句我就收下了。', '这句话我就当没看到。', '——'):
        assert humanize_text(text) == text


@pytest.mark.parametrize('source,expected', [
    ('唔，看到了——', '唔，看到了'),
    ('——现在出发', '现在出发'),
    ('看到了——挺好看的。', '看到了，挺好看的。'),
    ('好。——那走吧', '好。那走吧'),
    ('想想——，还是算了', '想想，还是算了'),
    ('先等等——\n再说', '先等等\n再说'),
    ('先等等\n——再说', '先等等\n再说'),
    ('想想 — 再决定', '想想，再决定'),
    ('等一下⸺再说⸻好了', '等一下，再说，好了'),
    ('他说“先等——等等”——我同意。', '他说“先等——等等”，我同意。'),
    ('看 `a——b`——就懂了。', '看 `a——b`，就懂了。'),
    ('```text\na——b\n```', '```text\na——b\n```'),
    ('~~~text\na——b\n~~~', '~~~text\na——b\n~~~'),
    ('> 原话——如此', '> 原话——如此'),
    ('https://example.invalid/a——b', 'https://example.invalid/a——b'),
    ('版本 v1-v2，参数 --help，范围 1–3，值 -2。', '版本 v1-v2，参数 --help，范围 1–3，值 -2。'),
])
def test_denia_spoken_dash_cleanup_preserves_literal_content(source, expected):
    assert humanize_messages([source], remove_dashes=True) == (expected,)


def test_dash_only_bubbles_drop_without_changing_legacy_default():
    assert humanize_messages(['——', '今天——很好', '—'], remove_dashes=True) == ('今天，很好',)
    assert humanize_messages(['今天——很好']) == ('今天——很好',)
