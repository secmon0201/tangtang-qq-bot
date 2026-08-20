from datetime import datetime
from zoneinfo import ZoneInfo

from bot.services.zhijiang_live_guard import LiveSchedule
from bot.services.zhijiang_live_reminders import (
    MULTI_LIVE_GAME_REMINDERS,
    SINGLE_LIVE_GAME_REMINDERS,
    render_live_game_reminder,
)


TIMEZONE = ZoneInfo("Asia/Shanghai")


def make_entry(event_id: str, category: str) -> LiveSchedule:
    return LiveSchedule(
        event_id,
        datetime(2026, 7, 25, 20, 0, tzinfo=TIMEZONE),
        category,
        "测试直播",
        "https://live.bilibili.com/22637261",
        "日常",
    )


def test_every_single_live_reminder_names_the_live_member():
    entry = make_entry("diana", "嘉然")

    assert len(SINGLE_LIVE_GAME_REMINDERS) == 10
    for template in SINGLE_LIVE_GAME_REMINDERS:
        message = render_live_game_reminder((entry,), choose=lambda _templates, value=template: value)
        assert message is not None
        assert message.count("嘉然") == 1
        assert "直播" in message
        assert "小游戏" in message


def test_every_multi_live_reminder_names_every_live_member():
    entries = (make_entry("diana", "嘉然"), make_entry("bella", "贝拉"))

    assert len(MULTI_LIVE_GAME_REMINDERS) == 10
    for template in MULTI_LIVE_GAME_REMINDERS:
        message = render_live_game_reminder(entries, choose=lambda _templates, value=template: value)
        assert message is not None
        assert "嘉然和贝拉" in message
        assert message.count("嘉然") == 1
        assert message.count("贝拉") == 1
        assert "直播" in message
        assert "小游戏" in message
