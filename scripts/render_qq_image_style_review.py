"""Render one deterministic sample for each refreshed QQ image family."""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image

from bot.services.a_coast_archive_render import ACoastArchiveImageRenderer
from bot.services.asoul import ScheduleItem
from bot.services.asoul_render import ASoulImageRenderer
from bot.services.asoul_web_render import ASoulWebRenderer
from bot.services.community_web import CommunityWebRenderer
from bot.services.mini_game_reports import MiniGameReportRenderer
from bot.services.reports import ReportRenderer


OUTPUT_DIR = Path("reports/qq_image_style_review")
ZONE = ZoneInfo("Asia/Shanghai")


def _keep(source: Path, name: str) -> Path:
    target = OUTPUT_DIR / name
    target.unlink(missing_ok=True)
    source.replace(target)
    return target


async def render_web_samples() -> tuple[Path, ...]:
    sticker_renderer = ASoulImageRenderer(OUTPUT_DIR)
    calendar = ASoulWebRenderer(
        OUTPUT_DIR,
        sticker_selector=sticker_renderer.select_schedule_stickers,
    )
    ranking = CommunityWebRenderer(OUTPUT_DIR)
    target_day = date(2026, 9, 1)
    items = (
        ScheduleItem(
            datetime(2026, 9, 1, 20, 0, tzinfo=ZONE),
            ("嘉然",),
            "九月第一场晚间歌回",
            "直播",
        ),
        ScheduleItem(
            datetime(2026, 9, 1, 22, 0, tzinfo=ZONE),
            ("嘉然", "乃琳", "贝拉"),
            "三人深夜电台特别节目",
            "联动",
        ),
    )
    ranking_payload = {
        "mode": "ranking",
        "title": "示例集群今日发言榜",
        "subtitle": "集群汇总 · 2026-09-01",
        "message_total": 329,
        "scope": "day",
        "group": "domain",
        "scope_options": [{"value": "day", "label": "今日"}],
        "group_options": [{"value": "domain", "label": "示例集群"}],
        "rows": [
            {"rank": 1, "nickname": "嘉然今天吃什么", "message_count": 128, "group_name": "示例一群", "avatar": ""},
            {"rank": 2, "nickname": "向晚大魔王", "message_count": 87, "group_name": "示例三群", "avatar": ""},
            {"rank": 3, "nickname": "贝拉的训练搭档", "message_count": 64, "group_name": "示例二群", "avatar": ""},
            {"rank": 4, "nickname": "乃琳的夜谈听众", "message_count": 50, "group_name": "示例五群", "avatar": ""},
        ],
        "chart": {
            "kind": "daily",
            "title": "本群近 7 日发言趋势",
            "subtitle": "固定自然日窗口｜每日发言总数",
            "rows": [
                {"label": "08-26", "message_count": 5127, "avatar": ""},
                {"label": "08-27", "message_count": 3100, "avatar": ""},
                {"label": "08-28", "message_count": 2181, "avatar": ""},
                {"label": "08-29", "message_count": 7884, "avatar": ""},
                {"label": "08-30", "message_count": 3492, "avatar": ""},
                {"label": "08-31", "message_count": 2419, "avatar": ""},
                {"label": "09-01", "message_count": 914, "avatar": ""},
            ],
        },
    }
    try:
        calendar_path = await calendar.render_schedule(
            "today",
            ((target_day, items),),
            generated_at="2026-09-01 18:30",
        )
        ranking_path = await ranking.render_ranking(ranking_payload)
        dynamic_path = await calendar.render_notification(
            "【B站动态】测试 UP\n九月通讯已发布",
            dynamic={
                "author": "测试 UP",
                "profile": "枝江直播与动态推送",
                "title": "九月的第一条枝江通讯",
                "text": "今晚的直播安排已经整理好了，也准备了一点新的幕后内容。",
                "quote_author": "A-SOUL 官方",
                "quote_text": "直播预约已经开启，期待晚上见。",
                "reserve_title": "九月第一场晚间歌回",
                "reserve_subtitle": "今晚 20:00 开播",
                "reserve_action": "预约",
                "likes": "18.2万",
                "following": "24",
                "followers": "73.4万",
            },
        )
        video_path = await calendar.render_notification(
            "【B站视频】测试 UP\n新视频发布",
            video={
                "author": "测试 UP",
                "profile": "枝江直播与动态推送",
                "text": "夏末舞台记录",
                "description": "记录这次舞台准备的片段，也感谢每一位来到直播间的朋友。",
                "likes": "18.2万",
                "following": "24",
                "followers": "73.4万",
            },
        )
        live_start_path = await calendar.render_notification(
            "【开播】测试 UP\n九月第一场晚间歌回",
            live={
                "phase": "start",
                "author": "测试 UP",
                "profile": "枝江直播与动态推送",
                "text": "九月第一场晚间歌回",
                "likes": "18.2万",
                "following": "24",
                "followers": "73.4万",
            },
        )
        live_end_path = await calendar.render_notification(
            "【下播】测试 UP\n九月第一场晚间歌回",
            live={
                "phase": "end",
                "author": "测试 UP",
                "profile": "枝江直播与动态推送",
                "text": "九月第一场晚间歌回",
                "live_duration": "02:15:32",
                "popularity_peak": "12.8万",
                "popularity_average": "8.6万",
            },
        )
    finally:
        await calendar.close()
        await ranking.close()
    return (
        _keep(calendar_path, "calendar.png"),
        _keep(ranking_path, "ranking.png"),
        _keep(dynamic_path, "bilibili_dynamic.png"),
        _keep(video_path, "bilibili_video.png"),
        _keep(live_start_path, "bilibili_live_start.png"),
        _keep(live_end_path, "bilibili_live_end.png"),
    )


def render_today_wife_sample() -> Path:
    renderer = MiniGameReportRenderer(OUTPUT_DIR, command_prefix="#")
    source = renderer.render_today_wife(
        {
            "target_id": 20260901,
            "target_nickname": "今晚与你同路的人",
            "context_nickname": "测试用户",
            "episode_title": "夏末便利店",
            "relationship_key": "意外默契",
            "story_tags": ("初次相遇", "共同线索"),
            "intro_line": "门铃响起时，你们同时伸手去拿最后一瓶汽水。",
        },
        (
            "一句玩笑把原本普通的夜晚变成了今天故事的开场。",
            "离开之前，对方把写着下一站地址的小票递到了你手里。",
        ),
        ("这段关系会怎样继续，要等群里的下一幕来回答。",),
        {},
    )
    return _keep(source, "today_wife.png")


def render_refreshed_local_samples() -> tuple[Path, ...]:
    report_renderer = ReportRenderer(OUTPUT_DIR, command_prefix="#")
    game_renderer = MiniGameReportRenderer(OUTPUT_DIR, command_prefix="#")
    profile_renderer = ACoastArchiveImageRenderer(OUTPUT_DIR)
    cover = OUTPUT_DIR / "announcement_review_cover.png"
    Image.new("RGB", (1800, 360), "#dceef1").save(cover)
    text_announcement = report_renderer.render_global_announcement(
        "今晚八点直播见\n愿每一份期待都有回声"
    )
    graphic_announcement = report_renderer.render_global_graphic_announcement(
        "周末直播提醒",
        "本周六 20:00 开始，开播后会同步发送直播间地址与注意事项。",
        cover,
    )
    cover.unlink(missing_ok=True)
    profile = profile_renderer.render_profile(
        900000001,
        "示例群友",
        "2026-09-01T20:01:00+08:00",
        "糖糖开篇：常在晚间参与聊天，也愿意接住群友抛来的话题。\n\n"
        "正文观察：表达直接，提问与回应都很及时。\n\n"
        "糖糖总评：是让聊天自然延续下去的那类成员。",
        [
            {"hour": hour, "content": "今晚直播吗？一起看呀" if hour % 3 else "收到，稍后见"}
            for hour in range(24)
        ],
    )
    mini_game_ranking = game_renderer.render_ranking(
        {
            "title": "小游戏总榜单",
            "global": True,
            "show_group_details": True,
            "groups": [
                {"group_id": 1001, "group_name": "示例一群"},
                {"group_id": 1002, "group_name": "示例二群"},
            ],
            "sections": [
                {
                    "title": "幸运骰局",
                    "value_label": "胜场",
                    "rows": [
                        {"rank": 1, "user_id": 1, "nickname": "今天吃什么", "games": 24, "value": 16, "group_ids": (1001, 1002)},
                        {"rank": 2, "user_id": 2, "nickname": "晚风来信", "games": 18, "value": 11, "group_ids": (1002,)},
                        {"rank": 3, "user_id": 3, "nickname": "海边散步", "games": 12, "value": 8, "group_ids": (1001,)},
                    ],
                }
            ],
        },
        {},
        {},
    )
    return (
        _keep(text_announcement, "announcement_text.png"),
        _keep(graphic_announcement, "announcement_graphic.png"),
        _keep(profile, "speech_profile.png"),
        _keep(mini_game_ranking, "mini_game_ranking.png"),
    )


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    paths = [
        *asyncio.run(render_web_samples()),
        render_today_wife_sample(),
        *render_refreshed_local_samples(),
    ]
    for path in paths:
        print(path.resolve())


if __name__ == "__main__":
    main()
