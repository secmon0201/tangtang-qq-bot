from __future__ import annotations

import asyncio
from datetime import date, datetime
from types import SimpleNamespace

from PIL import Image

from bot.services.asoul_web_render import (
    ASoulWebRenderer,
    asoul_live_web_url,
    notification_payload,
    page_html,
    schedule_payload,
)


def _item(hour: int, *, highlighted: bool = False):
    return SimpleNamespace(
        starts_at=datetime(2026, 8, 30, hour, 0),
        hosts=("心宜", "思诺"),
        content="夏末联动直播",
        label="联动",
        highlighted=highlighted,
        highlight_style="粉色" if highlighted else "",
    )


def test_schedule_payload_is_shared_by_web_and_capture_modes():
    payload = schedule_payload(
        "tomorrow",
        [(date(2026, 8, 30), [_item(20, highlighted=True)])],
        generated_at="2026-08-30 12:00",
    )

    assert payload["view"] == "tomorrow"
    assert payload["title"] == "明日直播"
    assert payload["event_count"] == 1
    assert payload["days"][0]["items"][0]["hosts"] == ["心宜", "思诺"]
    assert payload["days"][0]["items"][0]["highlighted"] is True

    interactive = page_html()
    capture = page_html(payload, capture=True)
    assert 'data-view="today"' in interactive
    assert "__ASOUL_PAYLOAD_JSON__" not in capture
    assert '<body class="capture-mode">' in capture
    assert ".capture-mode .interactive-bar { display: none !important; }" in capture
    assert "心宜直播信号" not in capture
    assert "xinyi-hero" not in capture
    assert "hardware-cluster" not in capture
    assert '<span class="xinyi-mark"' in capture
    assert "枝江日历功能由爱驼提供技术支持" in capture
    assert 'root.className = "capture-card schedule-capture"' in capture
    assert "isolation: isolate" in capture
    assert "linear-gradient(to bottom, #ead7df 0%, #d2d6dc 100%)" in capture
    assert "z-index: 1" in capture
    assert "background: #fffdfd" in capture
    assert 'class="date-month"' in capture
    assert "schedule-row::after" in capture
    assert "background: #17181c" not in interactive
    assert "repeating-linear-gradient(135deg, #f9dfe9" in interactive


def test_notification_payload_keeps_each_visual_kind_distinct():
    dynamic = notification_payload("dynamic", dynamic={"author": "心宜", "text": "动态"})
    video = notification_payload("video", video={"author": "思诺", "text": "新视频"})
    started = notification_payload("live", live={"phase": "start", "text": "开播"})
    ended = notification_payload("live", live={"phase": "end", "text": "下播"})

    assert [dynamic["mode"], video["mode"], started["mode"], ended["mode"]] == [
        "dynamic",
        "video",
        "live-start",
        "live-end",
    ]
    notification_html = page_html(dynamic, capture=True)
    assert 'class="notification-surface"' in notification_html
    assert ".notification-card {" in notification_html
    assert "repeating-linear-gradient(135deg, #f9dfe9" in notification_html
    assert "linear-gradient(105deg, #f4f5f6" in notification_html


def test_notification_media_localization_embeds_rich_emoji_nodes(tmp_path):
    source = Image.new("RGBA", (12, 12), "#ef5f8d")
    renderer = ASoulWebRenderer(tmp_path, media_loader=lambda _: source.copy())
    payload = notification_payload(
        "动态",
        dynamic={
            "rich_nodes": '[{"type":"text","text":"正文"},{"type":"emoji","text":"[表情]","url":"https://example.test/emoji.png"}]',
            "quote_rich_nodes": '[{"type":"emoji","text":"[引用表情]","url":"https://example.test/quote.png"}]',
        },
    )

    localized = renderer._localize_media(payload)

    assert "data:image/png;base64," in localized["details"]["rich_nodes"]
    assert "data:image/png;base64," in localized["details"]["quote_rich_nodes"]
    html = page_html(localized, capture=True)
    assert "function richNodes" in html
    assert 'image(node.url, "inline-emoji")' in html


def test_public_schedule_url_uses_bare_short_links():
    assert asoul_live_web_url("today") == "s.secmon.cn/r"
    assert asoul_live_web_url("tomorrow") == "s.secmon.cn/r"
    assert asoul_live_web_url("week") == "s.secmon.cn/r"


def test_html_renderer_outputs_content_without_interactive_controls(tmp_path):
    renderer = ASoulWebRenderer(tmp_path)
    payload = schedule_payload(
        "today",
        [(date(2026, 8, 30), [_item(18), _item(21)])],
        generated_at="2026-08-30 12:00",
    )
    async def render_and_close():
        try:
            return await renderer.render_payload(payload, "schedule_test")
        finally:
            await renderer.close()

    path = asyncio.run(render_and_close())

    assert path.is_file() and path.stat().st_size > 10_000
    with Image.open(path) as image:
        assert image.width == 1080
        assert image.height > 500
        assert image.getbbox() is not None


def test_warm_renderer_can_render_consecutive_payloads(tmp_path):
    async def render_twice():
        renderer = ASoulWebRenderer(tmp_path)
        try:
            first = await renderer.render_schedule(
                "today",
                [(date(2026, 8, 30), [_item(20)])],
                generated_at="2026-08-30 12:00",
            )
            second = await renderer.render_notification(
                "【B站视频】测试 UP\n第二次连续渲染",
                video={"author": "测试 UP", "text": "第二次连续渲染"},
            )
            return first, second
        finally:
            await renderer.close()

    first, second = asyncio.run(render_twice())
    assert first.is_file() and second.is_file()
    assert first.read_bytes() != second.read_bytes()


def test_schedule_sticker_is_selected_from_hosts_and_embedded_locally(tmp_path):
    sticker = tmp_path / "xinyi.png"
    Image.new("RGBA", (48, 48), "#ef5f8d").save(sticker)
    selected_hosts = []

    def select(hosts):
        selected_hosts.append(tuple(hosts))
        return sticker

    renderer = ASoulWebRenderer(tmp_path, sticker_selector=select)
    payload = schedule_payload(
        "today",
        [(date(2026, 8, 30), [_item(20)])],
        generated_at="2026-08-30 12:00",
    )

    localized = renderer.localize_schedule_stickers(payload)

    assert selected_hosts == [("心宜", "思诺")]
    assert localized["days"][0]["items"][0]["sticker_url"].startswith("data:image/png;base64,")
    assert "sticker_url" not in payload["days"][0]["items"][0]
