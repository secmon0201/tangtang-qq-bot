"""Render a review pack for the automatic three-act 今日缘分 game."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import tempfile

from PIL import Image

from bot.db import Database
from bot.services.mini_game_reports import MiniGameReportRenderer
from bot.services.today_wife import TodayWifeService
from bot.services.today_wife_game import TodayWifeGameService


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "data" / "reports" / "today_wife_game_review"
NOW = datetime.fromisoformat("2026-08-12T21:00:00+08:00")
ROUND_TIMES = (
    datetime.fromisoformat("2026-08-12T11:45:00+08:00"),
    datetime.fromisoformat("2026-08-12T17:45:00+08:00"),
    datetime.fromisoformat("2026-08-12T23:05:00+08:00"),
)
GROUP_ID = 1001
MEMBERS = (
    {"user_id": 1, "nickname": "小明"}, {"user_id": 2, "nickname": "小夏"},
    {"user_id": 3, "nickname": "小白"}, {"user_id": 4, "nickname": "小雨"},
    {"user_id": 5, "nickname": "小冬"}, {"user_id": 6, "nickname": "小北"},
)


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as raw:
        database = Database(Path(raw) / "review.db")
        database.configure_groups((GROUP_ID,))
        wife = TodayWifeService(database)
        game = TodayWifeGameService(database)
        renderer = MiniGameReportRenderer(OUTPUT, retention_hours=720)
        colors = ("#f3a2bd", "#b7d9f2", "#c4e7c8", "#f5d0a9", "#d9c3ee", "#f4e39b")
        avatars = {}
        for member, color in zip(MEMBERS, colors):
            path = OUTPUT / f"avatar_{member['user_id']}.png"
            Image.new("RGB", (360, 360), color).save(path)
            avatars[int(member["user_id"])] = path

        def draw(actor: int, target: int):
            record = wife.draw(GROUP_ID, actor, next(item["nickname"] for item in MEMBERS if item["user_id"] == actor), MEMBERS, NOW, selected_target_id=target).record
            assert record is not None
            return record

        forced = wife.force_draw(GROUP_ID, 1, "小明", MEMBERS, NOW, target_id=2)
        assert forced.record is not None
        ming = forced.record
        draw(3, 2)
        draw(4, 1)
        draw(6, 5)
        relation = game.personal_archive(GROUP_ID, 1, NOW)["own"][0]["relation"]
        reveal = game.draw_reveal(ming, relation)
        draw_card = renderer.render_today_wife_game_draw(
            ming,
            wife.story_lines(ming),
            wife.context_lines(ming),
            avatars,
            game.day_state(GROUP_ID, NOW),
            relation,
            draw_reveal=reveal,
        )

        passive = game.passive_interaction_after_command(
            GROUP_ID,
            3,
            "review-passive",
            ROUND_TIMES[0].replace(hour=10),
            probability=1.0,
        )
        assert passive.kind == "played"
        rounds = tuple(
            game.prepare_collective_round(GROUP_ID, round_no, round_time)
            for round_no, round_time in enumerate(ROUND_TIMES, start=1)
        )
        assert all(payload is not None for payload in rounds)

        archive = game.personal_archive(GROUP_ID, 1, ROUND_TIMES[-1])
        story = game.group_story(GROUP_ID, ROUND_TIMES[-1])
        personal_history = game.personal_history(GROUP_ID, 1)
        group_archive = game.group_archive(GROUP_ID, ROUND_TIMES[-1])
        cases = {
            "01_领取结果": draw_card,
            "02_午间集体互动": renderer.render_today_wife_collective_round(rounds[0]),
            "03_傍晚集体互动": renderer.render_today_wife_collective_round(rounds[1]),
            "04_夜间收官": renderer.render_today_wife_collective_round(rounds[2]),
            "05_个人缘分": renderer.render_today_wife_archive(archive, avatars),
            "06_群缘分": renderer.render_group_today_wife(
                story["records"],
                avatars,
                NOW.date().isoformat(),
                {"title": story["day_state"]["script_title"]},
                f"{story['day_state']['act_title']}。{story['day_state']['route_key']}的线索正在把不同的关系写进同一页。",
            ),
            "07_个人永久留档": renderer.render_today_wife_history_archive(personal_history, avatars),
            "08_群往日摘要": renderer.render_today_wife_group_archive(group_archive),
        }
        for name, path in cases.items():
            target = OUTPUT / f"{name}.png"
            path.replace(target)
            print(target.relative_to(ROOT))


if __name__ == "__main__":
    main()
