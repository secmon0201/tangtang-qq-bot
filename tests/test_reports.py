import sqlite3
from pathlib import Path

from PIL import Image

from bot.services.reports import ReportRenderer, report_group_marker


def test_duplicate_report_renders_one_image_sized_page(tmp_path: Path):
    renderer = ReportRenderer(tmp_path)
    result = [
        {
            "user_id": index,
            "nickname": f"成员{index}",
            "groups": [
                {"group_id": 1001, "group_name": "群一"},
                {"group_id": 1002, "group_name": "群二"},
            ],
        }
        for index in range(1, 5)
    ]

    report = renderer.render_duplicate(result, total_count=4, page=1, total_pages=1)

    assert report.exists()
    assert report.parent == tmp_path
    with Image.open(report) as image:
        assert image.width == ReportRenderer.WIDTH
        assert image.height <= 1100
        assert image.getbbox() is not None


def test_duplicate_report_uses_cached_avatar_when_available(tmp_path: Path):
    avatar = tmp_path / "7.png"
    Image.new("RGB", (32, 32), "#ff0000").save(avatar)
    renderer = ReportRenderer(tmp_path / "reports")
    result = [
        {
            "user_id": 7,
            "nickname": "测试成员",
            "groups": [{"group_id": 1001, "group_name": "群一"}],
        }
    ]

    report = renderer.render_duplicate(result, 1, 1, 1, {7: avatar})

    assert report.exists()
    with Image.open(report) as image:
        assert image.width == ReportRenderer.WIDTH


def test_duplicate_legend_height_tracks_actual_group_count(tmp_path: Path):
    renderer = ReportRenderer(tmp_path)
    two = tuple((1000 + index, f"群{index}") for index in range(1, 3))
    ten = tuple((1000 + index, f"群{index}") for index in range(1, 11))
    two_height = renderer._legend_height(two, "all")
    ten_height = renderer._legend_height(ten, "all")
    ten_chips = renderer._legend_chip_layout(ten, "all")
    assert ten_height > two_height
    assert ten_height == 48 + (ten_chips[-1][1] + 1) * 34 + 12


def test_duplicate_report_reserves_height_for_wrapped_legend(tmp_path: Path):
    renderer = ReportRenderer(tmp_path)
    labels = tuple((1000 + index, f"这是一个较长的测试群名称{index}") for index in range(1, 11))
    result = [
        {
            "user_id": 7,
            "nickname": "测试成员",
            "groups": [{"group_id": group_id} for group_id, _ in labels[:3]],
        }
    ]
    header_positions: list[int] = []
    original_header = renderer._table_header

    def capture_header(draw, y, columns):
        header_positions.append(y)
        original_header(draw, y, columns)

    renderer._table_header = capture_header
    report = renderer.render_duplicate(result, 1, 1, 1, group_labels=labels, comparison_mode="all")

    assert report.exists()
    assert len(header_positions) == 1
    legend_height = renderer._legend_height(labels, "all")
    assert header_positions[0] >= (
        renderer.HEADER_HEIGHT + 30 + legend_height + renderer.ROW_GAP
    )


def test_duplicate_report_supports_all_groups_mode(tmp_path: Path):
    renderer = ReportRenderer(tmp_path)
    labels = ((1001, "群一"), (1002, "群二"), (1003, "群三"))
    report = renderer.render_duplicate(
        [
            {
                "user_id": 7,
                "nickname": "测试成员",
                "groups": [{"group_id": group_id} for group_id, _ in labels],
            }
        ],
        total_count=1,
        page=1,
        total_pages=1,
        group_labels=labels,
        comparison_mode="all",
    )

    assert report.exists()


def test_report_group_markers_are_font_stable():
    assert [report_group_marker(index) for index in range(1, 5)] == ["①", "②", "③", "④"]


def test_ranking_report_accepts_sqlite_rows(tmp_path: Path):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        "SELECT 1 AS rank, 2120682836 AS user_id, '测试成员' AS nickname, 42 AS message_count"
    ).fetchone()
    renderer = ReportRenderer(tmp_path)

    report = renderer.render_ranking([row], "群 1001 今日发言榜")

    assert report.exists()
    assert report.stat().st_size > 0


def test_a_coast_ranking_report_shows_group_labels_without_qq_number_text(tmp_path: Path):
    renderer = ReportRenderer(tmp_path)
    details: list[str] = []
    original_ellipsize = renderer._ellipsize

    def capture_ellipsize(text, font, width):
        details.append(str(text))
        return original_ellipsize(text, font, width)

    renderer._ellipsize = capture_ellipsize
    report = renderer.render_ranking(
        [
            {
                "rank": 1,
                "user_id": 2120682836,
                "nickname": "测试成员",
                "message_count": 42,
                "group_labels": "海岸一群、海岸二群",
            }
        ],
        "A海岸 总发言榜",
        avatar_paths={},
        show_group_labels=True,
    )

    with Image.open(report) as image:
        assert image.width == ReportRenderer.WIDTH
        assert image.height > ReportRenderer.HEADER_HEIGHT + 120

    assert any(detail.startswith("发言最多群聊：") for detail in details)
    assert not any(detail.startswith("所在群聊：") for detail in details)


def test_a_coast_ranking_report_adds_independent_group_totals_footer(tmp_path: Path):
    renderer = ReportRenderer(tmp_path)
    plain = renderer.render_ranking([], "A Coast daily", show_group_labels=True)
    with_totals = renderer.render_ranking(
        [],
        "A Coast daily",
        show_group_labels=True,
        group_totals=[
            {"group_id": 1001, "group_name": "A Coast group one", "message_count": 11},
            {"group_id": 1002, "group_name": "A Coast group two", "message_count": 0},
        ],
    )

    with Image.open(plain) as plain_image, Image.open(with_totals) as totals_image:
        assert totals_image.height > plain_image.height


def test_group_ranking_report_adds_a_fixed_daily_trend_chart(tmp_path: Path):
    renderer = ReportRenderer(tmp_path)
    plain = renderer.render_ranking([], "Local ranking")
    trend = renderer.render_ranking(
        [],
        "Local ranking",
        daily_totals=[
            {"day": f"2026-07-{day:02d}", "message_count": day}
            for day in range(25, 32)
        ],
    )

    with Image.open(plain) as plain_image, Image.open(trend) as trend_image:
        assert trend_image.height > plain_image.height


def test_admin_panel_renders_group_scope_and_command_sections(tmp_path: Path):
    renderer = ReportRenderer(tmp_path)
    report = renderer.render_admin_panel(
        "管理员帮助",
        "本地图片反馈",
        [
            ("功能范围", "#功能范围 列表", "范围修改后立即生效。"),
            ("被动互动", "#被动互动状态", "参数与名单会持久化。"),
        ],
    )

    assert report.exists()
    with Image.open(report) as image:
        assert image.width == ReportRenderer.WIDTH
        assert image.getbbox() is not None


def test_admin_panel_long_note_uses_stacked_layout(tmp_path: Path):
    renderer = ReportRenderer(tmp_path)
    commands = "#发言排行 日|周|月|总"
    long_note = "群内榜只计算当前群；A海岸榜合并五群，每位用户只显示一个完整群名。" * 4
    assert renderer._help_card_stacked(commands, long_note)
    assert not renderer._help_card_stacked(commands, "群内榜只计算当前群。")


def test_group_overview_renders_group_identity_and_multiline_settings(tmp_path: Path):
    renderer = ReportRenderer(tmp_path)
    report = renderer.render_group_overview(
        "被动互动状态",
        "按群独立配置",
        [
            {
                "group_id": 1001,
                "group_name": "测试群一",
                "tag": "被动互动",
                "detail": "随机表情：30% 命中，每群冷却 10 秒。\n随机复读：10% 命中，冷却 15 分钟，消息间隔 50 条。\n三连复读：已开启。",
            }
        ],
    )

    assert report.exists()
    with Image.open(report) as image:
        assert image.width == ReportRenderer.WIDTH
        assert image.height >= ReportRenderer.HEADER_HEIGHT + ReportRenderer.GROUP_OVERVIEW_ROW_HEIGHT


def test_group_overview_grows_for_long_settings_without_fixed_row_clipping(tmp_path: Path):
    renderer = ReportRenderer(tmp_path)
    base_row = {"group_id": 1001, "group_name": "Group one", "tag": "Hourly"}
    short = renderer.render_group_overview(
        "Hourly status",
        "Short detail",
        [{**base_row, "detail": "Enabled; 00:00-23:59"}],
    )
    with Image.open(short) as image:
        short_height = image.height

    long = renderer.render_group_overview(
        "Hourly status",
        "Long detail",
        [{
            **base_row,
            "detail": (
                "Enabled; 00:00-23:59; pool: morning 112/112/112, daytime 112/110/110; "
                "evening 109/107/104, night 109/108/103; combinations about 876 million"
            ),
        }],
    )
    with Image.open(long) as image:
        assert image.height > short_height


def test_old_report_files_are_removed_before_new_report_is_saved(tmp_path: Path):
    old_report = tmp_path / "old.png"
    old_report.write_bytes(b"old")
    renderer = ReportRenderer(tmp_path, retention_hours=1)
    old_report.touch()
    import os
    import time

    old_time = time.time() - 7200
    os.utime(old_report, (old_time, old_time))

    renderer.render_whitelist([])

    assert not old_report.exists()
