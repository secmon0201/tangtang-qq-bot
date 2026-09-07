"""Render one deterministic sample for each refreshed QQ image family."""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.services.asoul import ScheduleItem
from bot.services.asoul_render import ASoulImageRenderer
from bot.services.asoul_web_render import ASoulWebRenderer
from bot.services.community_web import CommunityWebRenderer
from bot.services.mini_game_reports import MiniGameReportRenderer


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


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    paths = [*asyncio.run(render_web_samples()), render_today_wife_sample()]
    for path in paths:
        print(path.resolve())


if __name__ == "__main__":
    main()
