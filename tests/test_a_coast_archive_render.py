from pathlib import Path

from PIL import Image

from bot.services.a_coast_archive_render import ACoastArchiveImageRenderer


def test_archive_renderer_uses_ak_bot_branding():
    assert ACoastArchiveImageRenderer.BRAND_HEADER == "AK-BOT FUNCTION"
    assert ACoastArchiveImageRenderer.BRAND_FOOTER == "AK bot"


def test_archive_renderer_renders_search_results_as_a_portrait_image(tmp_path: Path):
    renderer = ACoastArchiveImageRenderer(tmp_path)
    avatar = tmp_path / "903848042.png"
    Image.new("RGB", (40, 40), "#ff0000").save(avatar)

    report = renderer.render(
        903848042,
        [{
            "occurred_at": "2026-07-29 12:34:56",
            "group_id": 910000101,
            "group_name": "A海岸测试群",
            "content": "爱莉希雅" * 50,
        }],
        page=1,
        keyword="爱莉希雅",
        avatar_paths={903848042: avatar},
        total_pages=3,
    )

    assert report.exists()
    assert report.parent == tmp_path
    with Image.open(report) as image:
        assert image.width == ACoastArchiveImageRenderer.WIDTH
        assert image.height > ACoastArchiveImageRenderer.HEADER_HEIGHT
        assert image.height < 520
        assert image.mode == "RGBA"
        assert image.getpixel((0, 0))[3] == 0
        assert image.getpixel((34, 34))[3] == 255
        assert image.getpixel((74, 110))[:3] == (255, 0, 0)
        assert image.getbbox() is not None


def test_archive_renderer_renders_an_empty_result_image(tmp_path: Path):
    report = ACoastArchiveImageRenderer(tmp_path).render(903848042, [], page=3)

    assert report.exists()
    with Image.open(report) as image:
        assert image.width == ACoastArchiveImageRenderer.WIDTH
        assert image.height > ACoastArchiveImageRenderer.HEADER_HEIGHT


def test_profile_renderer_creates_an_adaptive_long_image_with_charts(tmp_path: Path):
    renderer = ACoastArchiveImageRenderer(tmp_path)
    assert renderer.ARCHIVE_STARTED_ON == "2026年7月29日"
    avatar = tmp_path / "903848042.png"
    Image.new("RGB", (80, 80), "#ff0000").save(avatar)
    rows = [
        {"hour": hour, "content": "今天直播吗？哈哈" if hour % 2 else "公告更新 20:00 开播"}
        for hour in range(24)
    ]
    profile = "这是一段动态长度的画像正文。" * 80
    report = renderer.render_profile(
        903848042,
        "测试成员",
        "2026-07-29T02:01:05+00:00",
        profile,
        rows,
        avatar,
    )

    assert report.exists()
    with Image.open(report) as image:
        assert image.width == ACoastArchiveImageRenderer.WIDTH
        assert image.height > 1500
        assert image.getpixel((74, 148))[:3] == (255, 0, 0)
        assert image.getpixel((5, image.height // 4))[:3] != image.getpixel(
            (5, image.height * 3 // 4)
        )[:3]


def test_profile_renderer_omits_ai_narrative_panel_when_disabled(tmp_path: Path):
    renderer = ACoastArchiveImageRenderer(tmp_path)
    rows = [{"hour": hour, "content": "今天直播吗？哈哈"} for hour in range(24)]
    report = renderer.render_profile(
        903848042,
        "测试成员",
        "2026-07-29T02:01:05+00:00",
        "这段 AI 画像不应出现在关闭 AI 时的图片中。" * 40,
        rows,
        include_ai_profile=False,
    )

    with Image.open(report) as image:
        assert image.size == (renderer.WIDTH, 1052)


def test_profile_style_metrics_use_raw_counts_with_a_dynamic_ceiling(tmp_path: Path):
    renderer = ACoastArchiveImageRenderer(tmp_path)
    rows = [{"content": "今晚 20:00 直播吗？哈哈"} for _ in range(240)]

    metrics = dict(renderer._style_metrics(rows))

    assert metrics["复读魂"] == 240
    assert metrics["好奇雷达"] == 240
    assert metrics["情绪电波"] == 240
    assert renderer._metric_ceiling(metrics.values()) == 500


def test_profile_renderer_extracts_a_distinct_overall_summary(tmp_path: Path):
    renderer = ACoastArchiveImageRenderer(tmp_path)
    summary, details = renderer._profile_sections(
        "糖糖总评：聊天气质轻快，接梗很勤。\n\n后续展开观察第一段。\n\n后续展开观察第二段。",
        renderer._font(20),
        renderer._font(22),
        700,
    )

    assert "糖糖总评" not in "".join(summary)
    assert "聊天气质轻快" in "".join(summary)
    assert "后续展开观察第一段" in "".join(details)


def test_profile_renderer_separates_a_closing_tangtang_summary(tmp_path: Path):
    renderer = ACoastArchiveImageRenderer(tmp_path)
    value = (
        "糖糖开篇：开场画像。\n\n"
        "正文观察第一段。\n\n"
        "糖糖总评：最后的总结。"
    )

    summary, details = renderer._profile_sections(
        value, renderer._font(20), renderer._font(22), 700
    )
    closing = renderer._profile_closing_lines(value, renderer._font(20), 700)

    assert "糖糖开篇" not in "".join(summary)
    assert renderer._profile_lead_label(value) == "糖糖开篇"
    assert "正文观察第一段" in "".join(details)
    assert "糖糖总评" not in "".join(details)
    assert "最后的总结" in "".join(closing)
