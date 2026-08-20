"""Render a fixed visual review pack for the 今日老婆 feature."""

from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image

from bot.db import Database
from bot.services.avatars import AvatarService
from bot.services.mini_game_reports import MiniGameReportRenderer
from bot.services.today_wife import TodayWifeService
from bot.services.today_wife_game import TodayWifeGameService


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "data" / "reports" / "today_wife_cases"
GROUP_ID = 1001
ACTOR_ID = 595861835
BOT_ID = 2120682836
NOW = datetime.fromisoformat("2026-08-11T12:00:00+08:00")
MEMBERS = [
    {"user_id": ACTOR_ID, "nickname": "测试用户"},
    {"user_id": BOT_ID, "nickname": "机器人账号"},
    {"user_id": 201, "nickname": "小青"},
    {"user_id": 202, "nickname": "小白"},
    {"user_id": 301, "nickname": "小红"},
    {"user_id": 302, "nickname": "小蓝"},
    {"user_id": 303, "nickname": "小绿"},
    {"user_id": 400, "nickname": "小明"},
    {"user_id": 500, "nickname": "小夏"},
    {"user_id": 600, "nickname": "小秋"},
    {"user_id": 700, "nickname": "小雨"},
    {"user_id": 701, "nickname": "小禾"},
]


def draw(service: TodayWifeService, actor_id: int, actor_name: str, target_id: int, now: datetime = NOW):
    outcome = service.draw(GROUP_ID, actor_id, actor_name, MEMBERS, now, selected_target_id=target_id)
    assert outcome.record is not None
    return outcome.record


async def avatars(output_dir: Path) -> dict[int, Path]:
    cache_dir = output_dir / "avatar_cache"
    service = AvatarService(cache_dir, "https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640")
    paths = await service.prefetch([{"user_id": BOT_ID}])
    colors = ("#f2a1bc", "#b7d9f2", "#c4e7c8", "#f5d0a9", "#d9c3ee", "#f4e39b")
    for index, member in enumerate(MEMBERS):
        user_id = int(member["user_id"])
        if user_id in paths:
            continue
        path = cache_dir / f"{user_id}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (300, 300), colors[index % len(colors)]).save(path)
        paths[user_id] = path
    return paths


async def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # SQLite WAL cleanup can briefly retain a file handle on Windows after the
    # final connection closes. These disposable review databases need not make
    # a successful image render fail because of that delayed handle release.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
        database = Database(Path(temporary) / "today_wife_cases.db")
        database.configure_groups((GROUP_ID,))
        database.set_group_info(GROUP_ID, "缘分测试群")
        service = TodayWifeService(database)
        game_service = TodayWifeGameService(database)
        renderer = MiniGameReportRenderer(OUTPUT_DIR, retention_hours=720)
        avatar_paths = await avatars(OUTPUT_DIR)

        def relation_for(record: dict[str, object], now: datetime = NOW) -> dict[str, object]:
            archive = game_service.personal_archive(GROUP_ID, int(record["actor_id"]), now)
            return next(
                (
                    dict(item.get("relation") or {})
                    for item in archive["own"]
                    if int(item.get("draw_index") or 0) == int(record.get("draw_index") or 0)
                ),
                {},
            )

        def draw_card(record: dict[str, object], now: datetime = NOW) -> Path:
            relation = relation_for(record, now)
            return renderer.render_today_wife_game_draw(
                record,
                service.story_lines(record),
                service.context_lines(record),
                avatar_paths,
                game_service.day_state(GROUP_ID, now),
                relation,
                draw_reveal=game_service.draw_reveal(record, relation, now),
            )

        yesterday = draw(service, ACTOR_ID, "测试用户", 201, NOW - timedelta(days=1))
        service.divorce(GROUP_ID, ACTOR_ID, NOW - timedelta(days=1))
        draw(service, 302, "小蓝", 600, NOW - timedelta(days=1))
        draw(service, 303, "小绿", 600, NOW - timedelta(days=1))
        service.divorce(GROUP_ID, 303, NOW - timedelta(days=1))

        ordinary = draw(service, ACTOR_ID, "测试用户", BOT_ID)
        divorced = service.divorce(GROUP_ID, ACTOR_ID, NOW)
        assert divorced.record is not None
        redraw = draw(service, ACTOR_ID, "测试用户", 701)

        first = draw(service, 201, "小青", 202)
        taken = draw(service, 202, "小白", 301)
        cycle = draw(service, 301, "小红", 201)
        draw(service, 400, "小明", 500)
        mutual = draw(service, 500, "小夏", 400)
        echo = draw(service, 302, "小蓝", 600)
        reunion = draw(service, 303, "小绿", 600)
        popular = draw(service, 700, "小雨", 600)

        large_rows = [
            {
                "actor_id": 10_000 + index * 2,
                "actor_nickname": f"成员{index * 2 + 1}",
                "target_id": 10_001 + index * 2,
                "target_nickname": f"成员{index * 2 + 2}",
            }
            for index in range(50)
        ]

        cases = {
            "01_普通抽中_595861835到机器人": draw_card(ordinary),
            "02_被抽中后另选": draw_card(taken),
            "03_双向奔赴": draw_card(mutual),
            "04_两人撞车与旧缘重逢": draw_card(reunion),
            "05_多人撞车": draw_card(popular),
            "06_三人关系闭环": draw_card(cycle),
            "07_前缘回声": draw_card(echo),
            "08_离婚": renderer.render_divorce(divorced.record, service.divorce_lines(divorced.record), avatar_paths),
            "09_离婚后第二次抽取": draw_card(redraw),
            "10_个人缘分": renderer.render_today_wife_history(
                service.history(GROUP_ID, ACTOR_ID), avatar_paths,
                service.state_message("empty_history", GROUP_ID, ACTOR_ID, NOW),
            ),
            "11_群缘分": renderer.render_group_today_wife(
                service.group_records(GROUP_ID, NOW), avatar_paths, NOW.date().isoformat(),
                service.episode(GROUP_ID, NOW.date()),
                service.group_spotlight(service.group_records(GROUP_ID, NOW), GROUP_ID, NOW.date()),
            ),
            "12_百人群缘分": renderer.render_group_today_wife(
                large_rows, avatar_paths, NOW.date().isoformat(), service.episode(GROUP_ID, NOW.date()),
                service.group_spotlight(large_rows, GROUP_ID, NOW.date()),
            ),
            "13_空个人缘分": renderer.render_today_wife_history(
                [], {}, service.state_message("empty_history", GROUP_ID, 999, NOW)
            ),
            "14_空群缘分": renderer.render_group_today_wife(
                [], {}, NOW.date().isoformat(), service.episode(GROUP_ID, NOW.date()),
                service.group_spotlight([], GROUP_ID, NOW.date()),
            ),
        }
        for stem, source in cases.items():
            destination = OUTPUT_DIR / f"{stem}.png"
            source.replace(destination)
            print(destination.relative_to(ROOT))
        assert yesterday and first


if __name__ == "__main__":
    asyncio.run(main())
