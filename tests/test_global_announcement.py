from PIL import Image
import asyncio
from types import SimpleNamespace

from nonebot.adapters.onebot.v11 import Message, MessageSegment

from bot.config import A_COAST_GROUP_IDS
from bot.services.reports import ReportRenderer


def announcement_plugin():
    import nonebot

    try:
        nonebot.get_driver()
    except ValueError:
        nonebot.init()
    from bot.plugins import global_announcement

    return global_announcement


def test_global_announcement_poster_is_a_nonempty_png(tmp_path):
    path = ReportRenderer(tmp_path, command_prefix="#").render_global_announcement(
        "宜宝 re 鸣潮演唱会了，aakk 往里进往里进"
    )
    assert path.exists()
    with Image.open(path) as image:
        assert image.width >= 480
        assert image.height > ReportRenderer.HEADER_HEIGHT
        assert image.getbbox() is not None
        # The announcement poster has no top gradient bar; the header area is plain white.
        assert image.getpixel((400, 100)) == (255, 255, 255)


def test_global_announcement_width_adapts_to_longest_line(tmp_path):
    renderer = ReportRenderer(tmp_path, command_prefix="#")
    short = renderer.render_global_announcement("短")
    long = renderer.render_global_announcement("这行文字非常长" * 12)
    with Image.open(short) as one_line, Image.open(long) as wide_line:
        assert one_line.width >= 480
        assert wide_line.width > one_line.width


def test_global_announcement_preserves_explicit_line_breaks(tmp_path):
    renderer = ReportRenderer(tmp_path, command_prefix="#")
    single = renderer.render_global_announcement("宜宝aakk")
    split = renderer.render_global_announcement("宜宝\naakk")
    with Image.open(single) as one_line, Image.open(split) as two_lines:
        assert two_lines.height > one_line.height


def test_global_announcement_pastes_sticker_on_the_left(tmp_path):
    sticker = tmp_path / "sticker.png"
    Image.new("RGBA", (64, 64), (255, 0, 0, 255)).save(sticker)
    path = ReportRenderer(tmp_path, command_prefix="#").render_global_announcement(
        "测试", sticker=sticker
    )
    with Image.open(path) as image:
        pixels = image.load()
        found = any(
            pixels[x, y][0] > 200 and pixels[x, y][1] < 100 and pixels[x, y][2] < 100
            for y in range(120, 300)
            for x in range(60, 260)
        )
    assert found


def test_parse_announcement_args_splits_leading_member(monkeypatch):
    plugin = announcement_plugin()
    monkeypatch.setattr(plugin, "available_sticker_members", lambda: ("心宜", "嘉然"))
    monkeypatch.setattr(
        plugin,
        "sticker_names",
        lambda member: {"哭哭": plugin.STICKER_DIR / "心宜" / "哭哭-0_sticker_static.png"}
        if member == "心宜"
        else {},
    )
    assert plugin.parse_announcement_args("心宜 内容") == ("内容", "心宜", None, False)
    assert plugin.parse_announcement_args("心宜\n内容") == ("内容", "心宜", None, False)
    assert plugin.parse_announcement_args("内容") == ("内容", None, None, False)
    assert plugin.parse_announcement_args("别人 内容") == ("别人 内容", None, None, False)
    assert plugin.parse_announcement_args("心宜 @全体 内容") == ("内容", "心宜", None, True)
    assert plugin.parse_announcement_args("@all 内容") == ("内容", None, None, True)
    assert plugin.parse_announcement_args("内容 @全体") == ("内容", None, None, True)
    assert plugin.parse_announcement_args("心宜 哭哭 内容") == ("内容", "心宜", "哭哭", False)
    assert plugin.parse_announcement_args("心宜 大哭 内容") == ("内容", "心宜", "哭哭", False)
    assert plugin.parse_announcement_args("心宜 未知 内容") == ("未知 内容", "心宜", None, False)


def test_resolve_announcement_sticker_prefers_member_and_falls_back_to_random(tmp_path):
    plugin = announcement_plugin()
    xinyi = tmp_path / "心宜"
    jiran = tmp_path / "嘉然"
    xinyi.mkdir()
    jiran.mkdir()
    xinyi_path = xinyi / "哭哭-0_sticker_static.png"
    jiran_path = jiran / "b.png"
    Image.new("RGBA", (8, 8), (0, 0, 0, 255)).save(xinyi_path)
    Image.new("RGBA", (8, 8), (0, 0, 0, 255)).save(jiran_path)

    assert plugin.resolve_announcement_sticker("心宜", None, tmp_path) == xinyi_path
    assert plugin.resolve_announcement_sticker("心宜", "哭哭", tmp_path) == xinyi_path
    assert plugin.resolve_announcement_sticker("心宜", "大哭", tmp_path) == xinyi_path
    assert plugin.resolve_announcement_sticker("心宜", "不存在", tmp_path) == xinyi_path
    assert plugin.resolve_announcement_sticker(None, None, tmp_path) in {xinyi_path, jiran_path}


def test_global_announcement_broadcast_sends_one_image_to_each_a_coast_group(monkeypatch, tmp_path):
    plugin = announcement_plugin()
    poster = tmp_path / "poster.png"
    Image.new("RGB", (10, 10), "white").save(poster)
    sent = []

    async def fake_call_api(_bot, action, **kwargs):
        sent.append((action, kwargs))

    monkeypatch.setattr(
        plugin,
        "renderer",
        SimpleNamespace(render_global_announcement=lambda _text, sticker=None: poster),
    )
    monkeypatch.setattr(plugin, "call_qq_action", fake_call_api)
    monkeypatch.setattr(plugin, "announcement_targets", lambda: A_COAST_GROUP_IDS)
    monkeypatch.setattr(
        plugin,
        "resolve_announcement_sticker",
        lambda member=None, sticker_name=None: None,
    )

    class FakeBot:
        self_id = 123

    sent_count, failed_count = asyncio.run(
        plugin.send_global_announcement(FakeBot(), "公告内容")
    )

    assert (sent_count, failed_count) == (len(A_COAST_GROUP_IDS), 0)
    assert [kwargs["group_id"] for action, kwargs in sent] == list(A_COAST_GROUP_IDS)
    assert all(action == "send_group_msg" for action, kwargs in sent)
    message = sent[0][1]["message"]
    assert message.type == "image"
    assert "[CQ:at" not in str(message)


def test_global_announcement_broadcast_can_mention_all(monkeypatch, tmp_path):
    plugin = announcement_plugin()
    poster = tmp_path / "poster.png"
    Image.new("RGB", (10, 10), "white").save(poster)
    sent = []

    async def fake_call_api(_bot, action, **kwargs):
        sent.append((action, kwargs))

    monkeypatch.setattr(
        plugin,
        "renderer",
        SimpleNamespace(render_global_announcement=lambda _text, sticker=None: poster),
    )
    monkeypatch.setattr(plugin, "call_qq_action", fake_call_api)
    monkeypatch.setattr(plugin, "announcement_targets", lambda: A_COAST_GROUP_IDS[:1])
    monkeypatch.setattr(
        plugin,
        "resolve_announcement_sticker",
        lambda member=None, sticker_name=None: None,
    )

    class FakeBot:
        self_id = 123

    sent_count, failed_count = asyncio.run(
        plugin.send_global_announcement(FakeBot(), "公告内容", at_all=True)
    )

    assert (sent_count, failed_count) == (1, 0)
    message = sent[0][1]["message"]
    assert "[CQ:at,qq=all]" in str(message)
    assert any(segment.type == "image" for segment in message)


def test_global_image_broadcast_sends_existing_image_to_each_a_coast_group(monkeypatch, tmp_path):
    plugin = announcement_plugin()
    image_path = tmp_path / "intro.png"
    Image.new("RGB", (960, 540), "white").save(image_path)
    sent = []

    async def fake_call_api(_bot, action, **kwargs):
        sent.append((action, kwargs))

    monkeypatch.setattr(plugin, "call_qq_action", fake_call_api)
    monkeypatch.setattr(plugin, "announcement_targets", lambda: A_COAST_GROUP_IDS)

    class FakeBot:
        self_id = 123

    sent_count, failed_count = asyncio.run(plugin.send_global_image(FakeBot(), image_path))

    assert (sent_count, failed_count) == (len(A_COAST_GROUP_IDS), 0)
    assert [kwargs["group_id"] for action, kwargs in sent] == list(A_COAST_GROUP_IDS)
    assert all(action == "send_group_msg" for action, kwargs in sent)
    assert all(kwargs["message"].type == "image" for action, kwargs in sent)


def test_global_image_path_must_be_inside_workspace(monkeypatch, tmp_path):
    plugin = announcement_plugin()
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside.png"
    workspace.mkdir()
    Image.new("RGB", (10, 10), "white").save(outside)
    monkeypatch.setattr(plugin, "ROOT", workspace)

    try:
        plugin.resolve_global_image(str(outside))
    except ValueError as exc:
        assert "inside the bot workspace" in str(exc)
    else:
        raise AssertionError("outside image path should be rejected")


def test_global_image_command_extracts_direct_and_replied_images():
    plugin = announcement_plugin()
    direct = Message(
        [
            MessageSegment.text(" @all "),
            MessageSegment("image", {"file": "direct.jpg", "url": "https://example/direct.jpg"}),
        ]
    )
    replied = Message(
        [MessageSegment("image", {"file": "reply.jpg", "url": "https://example/reply.jpg"})]
    )

    direct_image = plugin.extract_global_announcement_image(direct, replied)
    reply_image = plugin.extract_global_announcement_image(Message(), replied)

    assert direct_image is not None and direct_image.data["file"] == "https://example/direct.jpg"
    assert reply_image is not None and reply_image.data["file"] == "https://example/reply.jpg"
    assert plugin.image_announcement_mentions_all(direct)
    assert not plugin.image_announcement_mentions_all(Message([MessageSegment.text("普通发送")]))


def test_global_announcement_skips_groups_outside_managed_scope(monkeypatch, tmp_path):
    plugin = announcement_plugin()
    poster = tmp_path / "poster.png"
    Image.new("RGB", (10, 10), "white").save(poster)
    sent = []

    async def fake_call_api(_bot, action, **kwargs):
        sent.append((action, kwargs))

    monkeypatch.setattr(
        plugin,
        "renderer",
        SimpleNamespace(render_global_announcement=lambda _text, sticker=None: poster),
    )
    monkeypatch.setattr(plugin, "call_qq_action", fake_call_api)
    monkeypatch.setattr(plugin, "announcement_targets", lambda: A_COAST_GROUP_IDS[:-1])
    monkeypatch.setattr(
        plugin,
        "resolve_announcement_sticker",
        lambda member=None, sticker_name=None: None,
    )

    class FakeBot:
        self_id = 123

    sent_count, failed_count = asyncio.run(
        plugin.send_global_announcement(FakeBot(), "公告内容")
    )

    assert (sent_count, failed_count) == (len(A_COAST_GROUP_IDS) - 1, 0)
    assert [kwargs["group_id"] for action, kwargs in sent] == list(A_COAST_GROUP_IDS[:-1])


def test_global_announcement_rendering_failure_counts_all_groups_as_failed(monkeypatch):
    plugin = announcement_plugin()
    sent = []

    async def fake_call_api(_bot, action, **kwargs):
        sent.append((action, kwargs))

    def broken_render(_text, sticker=None):
        raise RuntimeError("render failed")

    monkeypatch.setattr(
        plugin,
        "renderer",
        SimpleNamespace(render_global_announcement=broken_render),
    )
    monkeypatch.setattr(plugin, "call_qq_action", fake_call_api)
    monkeypatch.setattr(plugin, "announcement_targets", lambda: A_COAST_GROUP_IDS)

    class FakeBot:
        self_id = 123

    sent_count, failed_count = asyncio.run(
        plugin.send_global_announcement(FakeBot(), "公告内容")
    )

    assert (sent_count, failed_count) == (0, len(A_COAST_GROUP_IDS))
    assert sent == []
