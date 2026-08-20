from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from PIL import Image

from bot.services.zhijiang_live_guard import LiveGuardStatus, LiveSchedule
from bot.services.zhijiang_live_reports import ZhijiangLiveReportRenderer


TIMEZONE = ZoneInfo("Asia/Shanghai")


def make_status(*, with_error: bool = False) -> LiveGuardStatus:
    now = datetime(2026, 7, 25, 12, 0, tzinfo=TIMEZONE)
    entry = LiveSchedule(
        "live-1",
        now + timedelta(hours=8),
        "嘉然",
        "夏日特别直播",
        "https://live.bilibili.com/22637261",
        "日常",
    )
    return LiveGuardStatus(
        enabled=True,
        source_url="https://example.test/schedule.json",
        last_refresh=now.isoformat(),
        last_error="网络超时" if with_error else None,
        paused_until=now + timedelta(minutes=60),
        global_game_enabled=False,
        upcoming=(entry,),
    )


def test_zhijiang_live_commands_have_local_image_reports(tmp_path):
    renderer = ZhijiangLiveReportRenderer(tmp_path, timezone_name="Asia/Shanghai")
    now = datetime(2026, 7, 25, 12, 0, tzinfo=TIMEZONE)
    status = make_status()
    paths = (
        renderer.render_schedule(status, now, 7),
        renderer.render_status(status, now),
        renderer.render_refresh(status, now),
        renderer.render_refresh(make_status(with_error=True), now),
        renderer.render_notice("无法刷新枝江直播", "只有超级管理员可以手动刷新直播日程。"),
    )

    for path in paths:
        with Image.open(path) as image:
            assert image.width == renderer.WIDTH
            assert image.height > renderer.HEADER_HEIGHT
            assert image.getbbox() is not None


def test_status_cards_expand_for_multiline_data_notes(tmp_path):
    renderer = ZhijiangLiveReportRenderer(tmp_path, timezone_name="Asia/Shanghai")
    note = "A deliberately long cache note. " * 22

    height = renderer._status_row_height("Data note", note)

    assert height > 150
