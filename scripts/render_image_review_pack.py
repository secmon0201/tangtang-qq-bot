"""Render one inspectable example for every local image-report entry point."""

from __future__ import annotations

import asyncio
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from bot.services.a_coast_archive_render import ACoastArchiveImageRenderer
from bot.services.asoul import ScheduleItem
from bot.services.asoul_render import ASoulImageRenderer
from bot.services.asoul_web_render import ASoulWebRenderer
from bot.services.mini_game_reports import MiniGameReportRenderer
from bot.services.reports import ReportRenderer
from bot.services.zhijiang_live_guard import LiveGuardStatus, LiveSchedule
from bot.services.zhijiang_live_reports import ZhijiangLiveReportRenderer


OUTPUT_DIR = Path("reports/image_review_v2")
ZONE = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 7, 28, 1, 56, tzinfo=ZONE)


def write_shared_reports() -> list[Path]:
    renderer = ReportRenderer(OUTPUT_DIR, command_prefix="#")
    graphic_cover = OUTPUT_DIR / "graphic_announcement_cover.png"
    graphic_sticker = Path("bot/resources/asoul_stickers/心宜/为你打call-0_sticker_static.png")
    Image.new("RGB", (2200, 280), "#b6d5e9").save(graphic_cover)
    groups = (
        {"group_id": 1001, "group_name": "测试群"},
        {"group_id": 1002, "group_name": "海风群"},
        {"group_id": 1003, "group_name": "星河群"},
    )
    paths = [
        renderer.render_duplicate(
            [
                {"user_id": 1, "nickname": "嘉然今天吃什么", "groups": list(groups[:2])},
                {"user_id": 2, "nickname": "向晚大魔王", "groups": list(groups[1:])},
            ],
            total_count=2,
            page=1,
            total_pages=1,
            group_labels=tuple((row["group_id"], row["group_name"]) for row in groups),
        ),
        renderer.render_ranking(
            [
                {"rank": 1, "user_id": 1, "nickname": "嘉然今天吃什么", "message_count": 128, "group_labels": "测试群、海风群"},
                {"rank": 2, "user_id": 2, "nickname": "向晚大魔王", "message_count": 87, "group_labels": "星河群"},
            ],
            "本周发言排行榜",
            "按发言数降序排列",
            show_group_labels=True,
        ),
        renderer.render_status(
            [{"group_id": 1001, "group_name": "测试群", "stats_enabled": True, "stats_role": "管理员", "detail": "统计功能已开启，日报将在整点后发送。", "tag": "统计开启"}],
            ["数据库：正常", "消息网关：已连接"],
        ),
        renderer.render_admin_panel(
            "管理员帮助",
            "配置和查询均在本地完成",
            [("群设置", "#群设置\n#群设置 小游戏 开", "本群管理员可独立配置。"), ("系统设置", "#系统设置 集群 列表", "仅超级管理员可用。")],
        ),
        renderer.render_group_overview(
            "功能范围已更新",
            "小游戏已在群 1067772451 开启",
            [{"group_id": 1001, "group_name": "测试群", "tag": "独群", "detail": "发言榜：开 | 小游戏：开 | 今日老婆：开 | 准时报点：关"}],
        ),
        renderer.render_whitelist(
            [{"user_id": 1, "nickname": "嘉然今天吃什么", "note": "不参与跨群查重"}, {"user_id": 2, "nickname": "向晚大魔王", "note": "管理员白名单"}],
        ),
        renderer.render_user_help(
            "普通用户帮助",
            "按功能分类；文字版可在 QQ 中展开查看",
            [("查询", "查询当前群信息", [("发言排行", "#发言排行 周", "查看本周群内排行"), ("发言搜索", "#发言搜索 @成员 关键词", "只搜索当前群记录")])],
        ),
        renderer.render_global_announcement("今晚八点 A-SOUL 演唱会开播\n欢迎一起进直播间！"),
        renderer.render_global_graphic_announcement(
            "周末直播提醒",
            "本周六 20:00 开始，欢迎群友一起观看特别直播。\n请提前确认时间，开播后会同步发送直播间地址与注意事项。",
            graphic_cover,
            sticker=graphic_sticker if graphic_sticker.is_file() else None,
        ),
    ]
    graphic_cover.unlink(missing_ok=True)
    return paths


def write_live_reports() -> list[Path]:
    schedule = (
        LiveSchedule("live-1", NOW + timedelta(hours=10), "嘉然", "夏日特别直播：一起唱歌聊天", "https://live.bilibili.com/22637261", "直播"),
        LiveSchedule("live-2", NOW + timedelta(days=1, hours=18), "贝拉", "运动挑战与深夜电台", "https://live.bilibili.com/22637262", "杂谈"),
    )
    status = LiveGuardStatus(True, "https://example.test/schedule.json", NOW.isoformat(), None, NOW + timedelta(hours=2), False, schedule)
    failed = LiveGuardStatus(True, "https://example.test/schedule.json", NOW.isoformat(), "网络超时，已保留上次成功的本地日程缓存。", None, True, schedule)
    renderer = ZhijiangLiveReportRenderer(OUTPUT_DIR, command_prefix="#")
    return [
        renderer.render_schedule(status, NOW, 7),
        renderer.render_status(status, NOW),
        renderer.render_refresh(status, NOW),
        renderer.render_refresh(failed, NOW),
        renderer.render_notice("直播日程需要刷新", "只有管理员可以手动刷新直播日程。\n当前缓存仍可正常使用。", level="warning"),
    ]


def write_game_reports() -> list[Path]:
    renderer = MiniGameReportRenderer(OUTPUT_DIR, command_prefix="#")
    ranking = {
        "title": "小游戏总榜单",
        "global": True,
        "show_group_details": True,
        "groups": [{"group_id": 1001, "group_name": "测试群"}, {"group_id": 1002, "group_name": "星河群"}],
        "sections": [
            {"title": "幸运骰局", "value_label": "胜场", "rows": [{"rank": 1, "user_id": 1, "nickname": "嘉然今天吃什么", "games": 24, "value": 16, "group_ids": (1001, 1002)}, {"rank": 2, "user_id": 2, "nickname": "向晚大魔王", "games": 18, "value": 11, "group_ids": (1002,)}]},
        ],
    }
    return [renderer.render_menu(), *renderer.render_game_details(), renderer.render_ranking(ranking, {}, {})]


async def write_asoul_reports() -> list[Path]:
    renderer = ASoulImageRenderer(OUTPUT_DIR)
    web_renderer = ASoulWebRenderer(OUTPUT_DIR, sticker_selector=renderer.select_schedule_sticker)
    today = date(2026, 7, 28)
    items = [
        ScheduleItem(datetime(2026, 7, 28, 20, 0, tzinfo=ZONE), ("嘉然",), "夏日晚间歌回", "直播"),
        ScheduleItem(datetime(2026, 7, 28, 22, 0, tzinfo=ZONE), ("向晚", "贝拉"), "深夜电台特别节目", "杂谈"),
    ]
    pillow_paths = [
        await renderer.render_schedule(today, "今日直播", items),
        await renderer.render_week_schedule(((today, items), (today + timedelta(days=1), items[:1]))),
        await renderer.render_bilibili_notification("【B站新动态】测试UP\n今晚八点开播，欢迎提前预约。\nhttps://example.test/d"),
        await renderer.render_bilibili_notification(
            "【B站评论区回复】乃琳Queen\n在思诺snow的动态底下的回复\n这是一条评论推送审查样例。\nhttps://example.test/comment"
        ),
        await renderer.render_bilibili_notification(
            "【B站动态】心宜\n这是带引用与预约信息的动态卡片。",
            dynamic={
                "author": "测试 UP",
                "uid": "595861835",
                "profile": "元气满满的 A-SOUL 舞担",
                "text": "周五中午 12 点，先来看看最新的枝江通讯，然后过一下战双的夏日剧情。",
                "quote_author": "A-SOUL 官方",
                "quote_text": "直播预约已开启，期待与你相见。",
                "reserve_title": "【突击】夏日回忆特别直播",
                "reserve_subtitle": "明天 20:00 开播",
                "reserve_action": "预约",
            },
        ),
        await renderer.render_bilibili_notification(
            "【B站视频】心宜\n新视频发布",
            video={
                "author": "测试 UP",
                "uid": "595861835",
                "profile": "A-SOUL 成员",
                "text": "夏日特别企划：和大家一起完成舞台挑战",
                "description": "记录这次舞台筹备的片段，也感谢每一位来到直播间的朋友。",
                "url": "https://example.test/video",
                "likes": "18.2万",
                "following": "24",
                "followers": "73.4万",
            },
        ),
        await renderer.render_bilibili_notification(
            "【直播】测试 UP\n夏日晚间歌回\nhttps://example.test/live",
            live={
                "phase": "start",
                "author": "测试 UP",
                "uid": "595861835",
                "profile": "A-SOUL 成员",
                "text": "夏日晚间歌回",
                "url": "https://example.test/live",
                "likes": "18.2万",
                "following": "24",
                "followers": "73.4万",
            },
        ),
        await renderer.render_bilibili_notification(
            "【直播】测试 UP\n夏日晚间歌回\nhttps://example.test/live",
            live={
                "phase": "end",
                "author": "测试 UP",
                "uid": "595861835",
                "profile": "A-SOUL 成员",
                "text": "夏日晚间歌回",
                "live_duration": "02:15:32",
                "popularity_peak": "12.8万",
                "popularity_average": "8.6万",
                "likes_delta": "+2.4万",
                "following_delta": "+1,942",
                "followers_delta": "+8,516",
            },
        ),
    ]
    try:
        html_paths = [
            await web_renderer.render_schedule("today", ((today, items),), generated_at="2026-07-28 01:56"),
            await web_renderer.render_schedule(
                "tomorrow",
                ((today + timedelta(days=1), ()),),
                generated_at="2026-07-28 01:56",
            ),
            await web_renderer.render_schedule(
                "week",
                ((today, items), (today + timedelta(days=1), items[:1]), (today + timedelta(days=2), ())),
                generated_at="2026-07-28 01:56",
            ),
            await web_renderer.render_notification(
                "【B站动态】测试 UP\n动态卡片 HTML 预览",
                dynamic={
                    "author": "测试 UP",
                    "profile": "元气满满的 A-SOUL 舞担",
                    "text": "周五中午 12 点，先来看看最新的枝江通讯，然后一起聊聊夏日舞台。",
                    "quote_author": "A-SOUL 官方",
                    "quote_text": "直播预约已开启，期待与你相见。",
                    "reserve_title": "【突击】夏日回忆特别直播",
                    "reserve_subtitle": "明天 20:00 开播",
                    "reserve_action": "预约",
                    "likes": "18.2万",
                    "following": "24",
                    "followers": "73.4万",
                },
            ),
            await web_renderer.render_notification(
                "【B站视频】测试 UP\n新视频发布",
                video={
                    "author": "测试 UP",
                    "profile": "A-SOUL 成员",
                    "text": "夏日特别企划：和大家一起完成舞台挑战",
                    "description": "记录这次舞台筹备的片段，也感谢每一位来到直播间的朋友。",
                    "likes": "18.2万",
                    "following": "24",
                    "followers": "73.4万",
                },
            ),
            await web_renderer.render_notification(
                "【B站评论区回复】乃琳Queen\n在思诺snow的动态底下的回复\n这是一条评论推送审查样例。\nhttps://example.test/comment"
            ),
            await web_renderer.render_notification(
                "【开播】测试 UP\n夏日晚间歌回",
                live={
                    "phase": "start",
                    "author": "测试 UP",
                    "profile": "A-SOUL 成员",
                    "text": "夏日晚间歌回",
                    "likes": "18.2万",
                    "following": "24",
                    "followers": "73.4万",
                },
            ),
            await web_renderer.render_notification(
                "【下播】测试 UP\n夏日晚间歌回",
                live={
                    "phase": "end",
                    "author": "测试 UP",
                    "profile": "A-SOUL 成员",
                    "text": "夏日晚间歌回",
                    "live_duration": "02:15:32",
                    "popularity_peak": "12.8万",
                    "popularity_average": "8.6万",
                },
            ),
        ]
    finally:
        await web_renderer.close()
    return [*pillow_paths, *html_paths]


def write_archive_reports() -> list[Path]:
    renderer = ACoastArchiveImageRenderer(OUTPUT_DIR)
    rows = [
        {
            "occurred_at": "2026-07-29 12:34:56",
            "group_id": 1128870029,
            "group_name": "A海岸测试群",
            "content": "今天的直播也辛苦啦，晚上八点见！",
        },
        {
            "occurred_at": "2026-07-29 20:08:18",
            "group_id": 1077416717,
            "group_name": "星河群",
            "content": "已经预约直播，期待一起看直播。",
        },
    ]
    profile_rows = [
        {"hour": hour, "content": "今晚 20:00 直播吗？哈哈" if hour % 2 else "公告更新，直播预约已开启"}
        for hour in range(24)
    ]
    return [
        renderer.render(595861835, rows, page=1, keyword="直播", total_pages=1),
        renderer.render_profile(
            595861835,
            "心宜的应援者",
            "2026-07-29T02:01:05+08:00",
            "糖糖开篇：这是一位积极参与群内话题的成员。\n\n正文观察：常在直播话题出现，也会热心回应其他群友。\n\n糖糖总评：保持这份真诚的应援热情。",
            profile_rows,
        ),
    ]


def write_preview_sheets(paths: list[Path]) -> list[Path]:
    """Create compact visual indexes so the full pack can be reviewed at a glance."""
    preview_paths: list[Path] = []
    thumb_size = (330, 420)
    columns, rows = 3, 3
    cell_width, cell_height = 370, 480
    title_font = ImageFont.truetype(r"C:\Windows\Fonts\msyhbd.ttc", 22)
    label_font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 16)
    for page_start in range(0, len(paths), columns * rows):
        page_paths = paths[page_start:page_start + columns * rows]
        sheet = Image.new("RGB", (columns * cell_width + 64, rows * cell_height + 92), "#fff8fc")
        draw = ImageDraw.Draw(sheet)
        draw.text((32, 24), f"AK-BOT FUNCTION  图片审阅  {page_start // (columns * rows) + 1}", font=title_font, fill="#d65791")
        for index, path in enumerate(page_paths):
            column, row = index % columns, index // columns
            left, top = 32 + column * cell_width, 78 + row * cell_height
            draw.rounded_rectangle((left, top, left + 346, top + 444), radius=16, fill="#ffffff", outline="#ead4e0", width=2)
            with Image.open(path) as source:
                thumb = source.convert("RGB")
                thumb.thumbnail(thumb_size)
                sheet.paste(thumb, (left + (346 - thumb.width) // 2, top + 14))
            label = path.stem.split("_", 1)[0]
            draw.text((left + 16, top + 426), label, font=label_font, fill="#6f5b69")
        target = OUTPUT_DIR / f"preview_{page_start // (columns * rows) + 1:02d}.png"
        sheet.save(target, format="PNG", optimize=True)
        preview_paths.append(target)
    return preview_paths


def write_official_probe() -> list[Path]:
    """The OpenAPI probe is a plugin-private renderer, so initialize NoneBot first."""
    import nonebot

    nonebot.init()
    from bot.plugins.official_qq import _probe_image

    source = Path(_probe_image())
    target = OUTPUT_DIR / "official_qq_probe.png"
    shutil.copy2(source, target)
    return [target]


def main() -> None:
    # This is a generated review-only directory. Rebuild it so every renderer
    # contributes exactly one current sample instead of leaving stale variants.
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    paths = [
        *write_shared_reports(),
        *write_live_reports(),
        *write_game_reports(),
        *asyncio.run(write_asoul_reports()),
        *write_archive_reports(),
        *write_official_probe(),
    ]
    previews = write_preview_sheets(paths)
    print(f"review directory: {OUTPUT_DIR.resolve()}")
    for path in paths:
        print(path.resolve())
    for path in previews:
        print(path.resolve())


if __name__ == "__main__":
    main()
