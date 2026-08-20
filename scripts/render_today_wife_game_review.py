"""Render a deterministic review pack for the expanded 今日缘分 game."""

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
LATE = datetime.fromisoformat("2026-08-12T23:51:00+08:00")
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
        bai = draw(3, 2)
        rain = draw(4, 1)
        north = draw(6, 5)
        events = []
        for actor, name, mention, intent in (
            (1, "小明", None, "靠近"),
            (2, "小夏", None, "回应"),
            (3, "小白", 2, "靠近"),
            (4, "小雨", 2, "助攻"),
            (6, "小北", None, "倾听"),
            (1, "小明", None, "倾听"),
            (2, "小夏", None, "回应"),
            (4, "小雨", 1, "助攻"),
        ):
            outcome = game.interaction(GROUP_ID, actor, name, mention, NOW, intent=intent)
            if outcome.kind == "played" and outcome.event is not None:
                events.append(outcome.event)
        response_prompt = game.interaction(GROUP_ID, 2, "小夏", now=NOW, intent="auto")
        assert response_prompt.kind == "prompt" and response_prompt.event is not None
        response_event = next(event for event in events if event["kind"] == "response")
        divorce = wife.divorce(GROUP_ID, 1, NOW)
        assert divorce.record is not None
        archive = game.personal_archive(GROUP_ID, 2, NOW)
        story = game.group_story(GROUP_ID, NOW)
        conclusion = game.lock_and_conclude(GROUP_ID, LATE)["conclusion"]

        relation = game.personal_archive(GROUP_ID, 1, NOW)["own"][0]["relation"]
        reveal = game.draw_reveal(ming, relation)
        personal_history = game.personal_history(GROUP_ID, 1)
        group_archive = game.group_archive(GROUP_ID, NOW)
        third_party_event = next((event for event in events if event["kind"] in {"assist", "interference"}), None)
        if third_party_event is None:
            # Narrative pack expansion changes runtime weights, so a short
            # random review run cannot promise this branch. Keep the card's
            # most important multi-relation layout covered deterministically.
            third_party_event = {
                "title": "今日互动｜关键助攻",
                "actor_nickname": "小北",
                "actor_remaining": 3,
                "day_state": story["day_state"],
                "narrative": "小北没有抢走镜头，只让小明和小夏终于获得一次把话说清楚的同场。围观的人都看见，这份助攻没有替他们决定结局。",
                "effects": (
                    {"left": "小明", "right": "小夏", "delta": 18, "mark": "有人递来台阶"},
                    {"left": "小白", "right": "小夏", "delta": 6, "mark": "同场回声"},
                ),
            }
        cases = {
            "01_抽卡结果": renderer.render_today_wife_game_draw(
                ming,
                wife.story_lines(ming),
                wife.context_lines(ming),
                avatars,
                game.day_state(GROUP_ID, NOW),
                relation,
                draw_reveal=reveal,
                available_actions=reveal.get("action_options") or reveal.get("available_actions"),
            ),
            "02_多人回应": renderer.render_today_wife_interaction(
                response_event,
                avatars,
                available_actions=response_event.get("action_options"),
            ),
            "03_第三方事件": renderer.render_today_wife_interaction(
                third_party_event,
                avatars,
                available_actions=third_party_event.get("action_options"),
            ),
            "04_离婚": renderer.render_divorce(divorce.record, wife.divorce_lines(divorce.record), avatars, relation),
            "05_个人档案": renderer.render_today_wife_archive(
                archive,
                avatars,
                available_actions=archive.get("action_options") or archive.get("available_actions"),
            ),
            "06_群缘分": renderer.render_group_today_wife(story["records"], avatars, NOW.date().isoformat(), {"title": story["day_state"]["script_title"]}, f"{story['day_state']['act_title']}。{story['day_state']['route_key']}的线索正在把不同的关系写进同一页。"),
            "07_今日终章": renderer.render_today_wife_conclusion(conclusion),
            "08_个人永久留档": renderer.render_today_wife_history_archive(personal_history, avatars),
            "09_群往日摘要": renderer.render_today_wife_group_archive(group_archive),
            "12_目标绑定选择": renderer.render_today_wife_action_prompt(response_prompt.event),
        }
        # Deliberately exceeds ordinary nickname and event lengths.  This is a
        # visual regression case for content packs added after initial launch.
        long_event = {
            "title": "今日互动｜围观席忽然安静下来",
            "actor_nickname": "名字很长很长的派对主持人",
            "actor_remaining": 2,
            "day_state": story["day_state"],
            "narrative": "这是一段用于检查长文案的公共剧情。" * 16,
            "effects": (
                {"left": "名字很长很长的派对主持人", "right": "同样名字很长很长的今日同行者", "delta": 30, "mark": "高光时刻"},
                {"left": "第三位名字也很长很长的围观群友", "right": "另一位名字很长很长的当事人", "delta": -10, "mark": "小摩擦"},
            ),
        }
        cases["10_长文案与长昵称"] = renderer.render_today_wife_interaction(long_event, avatars)
        cases["11_五段完整终章"] = renderer.render_today_wife_conclusion(
            {
                "title": "群像没有按计划散场",
                "ending": "今晚没有谁替谁写下结论。每一次被认真接住的回应、每一次没有抢走主角位置的助攻，都把原本可能错过的关系留在了同一张群像里。",
                "sections": (
                    {"kind": "高光关系", "left": "名字很长很长的小雨", "right": "名字很长很长的小明", "minimum": 0, "affection": 56, "marks": ("被看见", "有了后续"), "story": "在最该回头的时候，有人真的看见了这段关系；他们没有急着承诺什么，却不再像之前那样各自走开。"},
                    {"kind": "关键助攻", "actor": "小北", "story": "小北没有替任何人决定结局，只是在所有人都以为话题会被带走的时候，把一段没有说完的话递回了真正需要听见的人。", "effects": ({"left": "小明", "right": "小夏", "delta": 17}, {"left": "小白", "right": "小夏", "delta": 6})},
                    {"kind": "回应分岔", "actor": "小夏", "story": "小夏终于愿意面对所有朝自己靠近的关系。回应没有被平均分配，却让每一段等待都不再是彻底的沉默。", "effects": ({"left": "小明", "right": "小夏", "delta": 30}, {"left": "小白", "right": "小夏", "delta": -3})},
                    {"kind": "后续被接住", "actor": "小明", "story": "小明带着之前没送出的解释回来，没有否认那次错开，而是给了小夏一次不用靠猜测继续走下去的机会。", "effects": ({"left": "小明", "right": "小夏", "delta": 25},)},
                    {"kind": "留在榜上的旧关系", "left": "小明", "right": "小夏", "minimum": 0, "affection": 51, "marks": ("没能赶上", "被打断", "一起找回"), "story": "离婚没有抹掉这段关系。它仍在今天的群像里，作为一页被双方认真经历过的过去，等待以后是否会有新的答案。"},
                ),
                "stats": {"participants": 6, "interactions": 8, "assists": 1, "responses": 3, "misunderstandings": 2},
            }
        )
        for name, path in cases.items():
            target = OUTPUT / f"{name}.png"
            path.replace(target)
            print(target.relative_to(ROOT))


if __name__ == "__main__":
    main()
