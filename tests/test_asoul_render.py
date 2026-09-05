from io import BytesIO

from PIL import Image

from bot.services.asoul_render import ASoulImageRenderer


def test_asoul_renderer_uses_ak_bot_branding():
    assert ASoulImageRenderer.BRAND_HEADER == "AK-BOT FUNCTION"
    assert ASoulImageRenderer.BRAND_FOOTER == "AK bot"


class _ImageResponse:
    def __init__(self) -> None:
        buffer = BytesIO()
        Image.new("RGBA", (4, 4), "#ef5f8d").save(buffer, format="PNG")
        self.payload = buffer.getvalue()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload


def test_remote_bilibili_image_retries_with_headers_and_caches(monkeypatch, tmp_path):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append((request.full_url, request.headers, timeout))
        if len(calls) == 1:
            raise OSError("temporary CDN failure")
        return _ImageResponse()

    monkeypatch.setattr("bot.services.asoul_render.urlopen", fake_urlopen)
    renderer = ASoulImageRenderer(tmp_path)

    first = renderer._remote_image("http://i0.hdslb.com/test.png")
    second = renderer._remote_image("http://i0.hdslb.com/test.png")

    assert first is not None and first.size == (4, 4)
    assert second is not None and second.size == (4, 4)
    assert len(calls) == 2
    assert calls[0][0].startswith("https://")
    assert calls[0][1]["Referer"] == "https://www.bilibili.com/"
    assert calls[0][2] == 10


def test_remote_bilibili_image_falls_back_to_a_cdn_mirror(monkeypatch, tmp_path):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        if "i2.hdslb.com" in request.full_url:
            raise OSError("primary CDN unavailable")
        return _ImageResponse()

    monkeypatch.setattr("bot.services.asoul_render.urlopen", fake_urlopen)
    monkeypatch.setattr("bot.services.asoul_render.sleep", lambda _: None)
    renderer = ASoulImageRenderer(tmp_path)

    result = renderer._remote_image("http://i2.hdslb.com/bfs/archive/cover.jpg")

    assert result is not None and result.size == (4, 4)
    assert calls[:3] == ["https://i2.hdslb.com/bfs/archive/cover.jpg"] * 3
    assert calls[3] == "https://i0.hdslb.com/bfs/archive/cover.jpg"


def test_schedule_sticker_selection_uses_one_per_matching_host_and_caps_at_three(tmp_path, monkeypatch):
    sticker_root = tmp_path / "stickers"
    xinyi = sticker_root / "心宜"
    bella = sticker_root / "贝拉"
    xinyi.mkdir(parents=True)
    bella.mkdir()
    xinyi_sticker = xinyi / "xinyi.png"
    bella_sticker = bella / "bella.png"
    Image.new("RGBA", (16, 16), "#ef5f8d").save(xinyi_sticker)
    Image.new("RGBA", (16, 16), "#18a9c5").save(bella_sticker)
    jiran = sticker_root / "嘉然"
    nairin = sticker_root / "乃琳"
    jiran.mkdir()
    nairin.mkdir()
    jiran_sticker = jiran / "jiran.png"
    nairin_sticker = nairin / "nairin.png"
    Image.new("RGBA", (16, 16), "#f1a5bc").save(jiran_sticker)
    Image.new("RGBA", (16, 16), "#9a78c8").save(nairin_sticker)
    renderer = ASoulImageRenderer(tmp_path, sticker_dir=sticker_root)
    monkeypatch.setattr("bot.services.asoul_render.random.choice", lambda candidates: candidates[0])

    assert renderer.select_schedule_stickers(("心宜", "贝拉", "嘉然", "乃琳")) == (
        xinyi_sticker,
        bella_sticker,
        jiran_sticker,
    )
    assert renderer.select_schedule_stickers(("心宜", "心宜", "不存在")) == (xinyi_sticker,)
    assert renderer.select_schedule_stickers(("心宜",), limit=0) == ()
    assert renderer.select_schedule_sticker(("心宜",)) == xinyi_sticker
    assert renderer.select_schedule_sticker(("不存在",)) is None


def test_remote_image_candidates_upgrade_http_and_preserve_query(tmp_path):
    renderer = ASoulImageRenderer(tmp_path)

    assert renderer._remote_image_candidates("http://i1.hdslb.com/bfs/new_dyn/image.png?x=1") == [
        "https://i1.hdslb.com/bfs/new_dyn/image.png?x=1",
        "https://i0.hdslb.com/bfs/new_dyn/image.png?x=1",
        "https://i2.hdslb.com/bfs/new_dyn/image.png?x=1",
    ]


def test_dynamic_image_layout_keeps_all_images_in_three_column_rows(tmp_path):
    renderer = ASoulImageRenderer(tmp_path)
    images = [Image.new("RGBA", (100, height), "#ef5f8d") for height in (100, 200, 300, 150, 250, 350, 180)]

    layout = renderer._dynamic_image_grid_layout(images, 720)

    assert len(layout) == 7
    assert [left for _, left, top, _, _ in layout if top == 0] == [0, 244, 488]
    assert len({top for _, _, top, _, _ in layout}) == 3
    assert renderer._dynamic_image_grid_height(layout) == layout[-1][2] + layout[-1][4]


def test_full_width_image_scaling_preserves_top_and_bottom_pixels(tmp_path):
    renderer = ASoulImageRenderer(tmp_path)
    source = Image.new("RGBA", (8, 16), "#f5f5f5")
    for y in range(8):
        for x in range(8):
            source.putpixel((x, y), (255, 0, 0, 255))
    for y in range(8, 16):
        for x in range(8):
            source.putpixel((x, y), (0, 0, 255, 255))
    canvas = Image.new("RGBA", (80, 160), "#ffffff")

    assert renderer._scaled_image_height(source, 80, 10) == 160
    assert renderer._paste_full_image(canvas, source, (0, 0, 80, 160))
    assert canvas.getpixel((40, 4))[:3] == (255, 0, 0)
    assert canvas.getpixel((40, 155))[:3] == (0, 0, 255)


def test_asoul_renderer_saves_transparent_rounded_corners(tmp_path):
    path = ASoulImageRenderer(tmp_path)._save(
        Image.new("RGBA", (120, 120), "#ef5f8d"), "corner_check"
    )

    with Image.open(path) as image:
        assert image.mode == "RGBA"
        assert image.getpixel((0, 0))[3] == 0
        assert image.getpixel((34, 34))[3] == 255


def test_bilibili_cards_render_media_for_every_notification_kind(monkeypatch, tmp_path):
    renderer = ASoulImageRenderer(tmp_path)
    source = Image.new("RGBA", (16, 8), "#ef5f8d")
    monkeypatch.setattr(renderer, "_remote_image", lambda _: source.copy())

    dynamic = renderer._render_bilibili_notification(
        "dynamic",
        dynamic={
            "author": "测试UP",
            "text": "正文[表情]",
            "avatar_url": "https://i0.hdslb.com/avatar.png",
            "rich_nodes": '[{"type":"text","text":"正文"},{"type":"emoji","text":"[表情]","url":"https://i0.hdslb.com/emoji.png"}]',
            "image_urls": '["https://i0.hdslb.com/dynamic.png"]',
            "quote_author": "原作者",
            "quote_text": "引用正文",
            "quote_image_urls": '["https://i0.hdslb.com/quote.png"]',
        },
    )
    video = renderer._render_bilibili_notification(
        "video",
        video={"text": "视频标题", "cover_url": "https://i0.hdslb.com/cover.jpg"},
    )
    started = renderer._render_bilibili_notification(
        "start",
        live={"phase": "start", "text": "开播标题", "cover_url": "https://i0.hdslb.com/live.jpg"},
    )
    ended = renderer._render_bilibili_notification(
        "end",
        live={"phase": "end", "text": "下播标题", "cover_url": "https://i0.hdslb.com/live.jpg"},
    )

    for path in (dynamic, video, started, ended):
        with Image.open(path) as image:
            assert image.getbbox() is not None
            assert image.getpixel((540, 500))[:3] == (239, 95, 141)
