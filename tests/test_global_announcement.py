from PIL import Image
import asyncio
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace

from nonebot.adapters.onebot.v11 import Message, MessageSegment
from starlette.responses import Response
from starlette.datastructures import UploadFile

from bot.config import A_COAST_GROUP_IDS
from bot.services.reports import ReportRenderer
from bot.services import roles


def announcement_plugin():
    import nonebot

    try:
        nonebot.get_driver()
    except ValueError:
        nonebot.init()
    from bot.plugins import global_announcement

    return global_announcement


def test_global_announcement_permission_allows_super_admin_or_configured_member(monkeypatch):
    monkeypatch.setattr(
        roles,
        "settings",
        SimpleNamespace(
            operator_ids=frozenset({100}),
            global_announcement_operator_ids=frozenset({200}),
            activity_admin_blacklist_ids=frozenset(),
            activity_admin_ids=frozenset(),
        ),
    )

    assert roles.is_global_announcement_operator(100)
    assert roles.is_global_announcement_operator(200)
    assert not roles.is_global_announcement_operator(300)


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
        assert image.getpixel((400, 100))[:3] == (255, 255, 255)


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


def test_global_graphic_announcement_fills_the_canvas_width_and_grows_with_content(tmp_path):
    source = tmp_path / "very_wide_source.png"
    sticker = tmp_path / "sticker.png"
    Image.new("RGB", (2400, 220), "#a9cde6").save(source)
    Image.new("RGBA", (80, 80), (240, 80, 145, 255)).save(sticker)
    renderer = ReportRenderer(tmp_path, command_prefix="#")
    short = renderer.render_global_graphic_announcement(
        "活动开放报名", "欢迎一起参加。", source, sticker=sticker
    )
    long = renderer.render_global_graphic_announcement(
        "活动开放报名",
        "这是一段用于确认自动换行和动态高度的公告正文。" * 24,
        source,
        sticker=sticker,
    )

    with Image.open(short) as short_image, Image.open(long) as long_image:
        assert short_image.width == 1080
        assert long_image.height > short_image.height
        assert short_image.format == "PNG"
        assert short_image.mode == "RGBA"
        assert short_image.getpixel((0, 0))[3] == 0
        assert short_image.getpixel((34, 34))[3] == 255
        # The source is scaled to the fixed poster width, without side letterboxing.
        assert short_image.getpixel((0, 180))[:3] == (169, 205, 230)
        assert short_image.getpixel((1079, 180))[:3] == (169, 205, 230)


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
    assert plugin._web_sticker("__none__", "__random__", allow_none=True) == (None, "无角色", None)


def test_imported_stickers_are_available_to_announcement_picker():
    plugin = announcement_plugin()
    expected = {
        "乃琳": {"啊？", "OI", "[乃琳Queen_Wink]"},
        "嘉然": {"抱头", "糖糖", "[嘉然2.0_小天使]"},
        "贝拉": {"抱抱", "智慧", "[贝拉个性装扮2.0_剑来]"},
    }
    minimum_counts = {"乃琳": 90, "嘉然": 110, "贝拉": 90}

    for member, names in expected.items():
        available = plugin.sticker_names(member)
        assert len(available) >= minimum_counts[member]
        assert names <= set(available)
        for name in names:
            with Image.open(available[name]) as image:
                assert image.format == "PNG"
                assert image.mode == "RGBA"


def test_web_preview_post_returns_the_rendered_png(monkeypatch, tmp_path):
    plugin = announcement_plugin()
    poster = tmp_path / "poster.png"
    Image.new("RGB", (10, 10), "white").save(poster)
    session = plugin.web_sessions.create(
        100,
        (plugin.AnnouncementTarget("group:1001", "测试群", "group", (1001,)),),
    )

    async def request_json():
        return {
            "text": "公告内容",
            "extra_text": "图片下方说明",
            "member": "__none__",
            "sticker": "__random__",
            "at_all": True,
            "targets": ["group:1001"],
        }

    monkeypatch.setattr(plugin, "renderer", SimpleNamespace(render_global_announcement=lambda _text, sticker=None: poster))
    response = asyncio.run(plugin.global_announcement_web_preview(session.token, SimpleNamespace(json=request_json)))

    assert isinstance(response, Response)
    assert response.media_type == "image/webp"
    assert response.headers["cache-control"] == "no-store"
    with Image.open(BytesIO(response.body)) as preview:
        assert preview.format == "WEBP"
    assert session.draft_path == poster
    assert session.draft_extra_text == "图片下方说明"
    assert session.draft_at_all is True
    assert session.draft_target_group_ids == (1001,)


def test_web_graphic_preview_post_returns_the_rendered_png(monkeypatch, tmp_path):
    plugin = announcement_plugin()
    poster = tmp_path / "poster.png"
    Image.new("RGB", (10, 10), "white").save(poster)
    session = plugin.web_sessions.create(
        100,
        (plugin.AnnouncementTarget("group:1001", "测试群", "group", (1001,)),),
    )
    source = BytesIO()
    Image.new("RGB", (10, 10), "pink").save(source, format="PNG")
    source.seek(0)

    monkeypatch.setattr(plugin, "ROOT", tmp_path)
    monkeypatch.setattr(plugin, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(
        plugin,
        "renderer",
        SimpleNamespace(render_global_graphic_announcement=lambda _title, _text, _source, sticker=None: poster),
    )
    response = asyncio.run(
        plugin.global_announcement_web_graphic_preview(
            session.token,
            UploadFile(file=source, filename="source.png"),
            title="公告标题",
            text="公告正文",
            extra_text="图片下方说明",
            member="__none__",
            sticker="__random__",
            at_all="false",
            targets=json.dumps(["group:1001"]),
        )
    )

    assert isinstance(response, Response)
    assert response.media_type == "image/webp"
    assert response.headers["cache-control"] == "no-store"
    with Image.open(BytesIO(response.body)) as preview:
        assert preview.format == "WEBP"
    assert session.draft_path == poster
    assert session.draft_extra_text == "图片下方说明"
    assert session.draft_target_group_ids == (1001,)


def test_announcement_target_snapshot_supports_cluster_and_group_multiselect_without_duplicates():
    plugin = announcement_plugin()
    options = (
        plugin.AnnouncementTarget("cluster:7", "联动集群", "cluster", (1001, 1002)),
        plugin.AnnouncementTarget("group:1002", "二群", "group", (1002,), "cluster:7"),
        plugin.AnnouncementTarget("group:2001", "独群", "group", (2001,)),
    )
    session = plugin.web_sessions.create(100, options)

    resolved = plugin.web_sessions.resolve_targets(
        session, ["cluster:7", "group:1002", "group:2001"]
    )

    assert resolved == (1001, 1002, 2001)
    assert session.target_options == options


def test_announcement_target_selection_is_required_and_rejects_unknown_keys():
    plugin = announcement_plugin()
    session = plugin.web_sessions.create(
        100,
        (plugin.AnnouncementTarget("group:1001", "测试群", "group", (1001,)),),
    )

    for selected in ([], ["group:missing"]):
        try:
            plugin.web_sessions.resolve_targets(session, selected)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid announcement targets must be rejected")


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


def test_global_image_broadcast_appends_optional_text_in_the_same_message(monkeypatch, tmp_path):
    plugin = announcement_plugin()
    image_path = tmp_path / "intro.png"
    Image.new("RGB", (960, 540), "white").save(image_path)
    sent = []

    async def fake_call_api(_bot, action, **kwargs):
        sent.append((action, kwargs))

    monkeypatch.setattr(plugin, "call_qq_action", fake_call_api)
    monkeypatch.setattr(plugin, "announcement_targets", lambda: A_COAST_GROUP_IDS[:1])

    class FakeBot:
        self_id = 123

    sent_count, failed_count = asyncio.run(
        plugin.send_global_image(FakeBot(), image_path, extra_text="  补充说明\n第二行  ")
    )

    assert (sent_count, failed_count) == (1, 0)
    message = sent[0][1]["message"]
    assert [segment.type for segment in message] == ["image", "text"]
    assert message[1].data["text"] == "\n补充说明\n第二行"


def test_global_announcement_message_keeps_the_original_image_for_blank_optional_text():
    plugin = announcement_plugin()
    image = MessageSegment.image(file="file:///announcement.png")

    assert plugin.global_announcement_message(image, extra_text="  ") is image


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
