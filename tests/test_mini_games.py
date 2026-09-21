from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from PIL import Image

from bot.db import Database
from bot.services.idioms import is_valid_four_character_word, is_valid_idiom
from bot.services.mini_game_reports import MiniGameReportRenderer
from bot.services.mini_games import (
    BOMB,
    DICE,
    GUESS,
    ROULETTE,
    ROULETTE_BARREL_BURST_CHANCE_PERCENT,
    ROULETTE_HIT_TEXTS,
    ROULETTE_SAFE_TEXTS,
    MiniGameService,
)
from bot.services.action_state import action_state_versions


START = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)


def make_service(tmp_path):
    db = Database(tmp_path / "bot.db")
    db.configure_groups((1001, 1002))
    db.set_group_info(1001, "测试群一")
    db.set_group_info(1002, "测试群二")
    return db, MiniGameService(db)


def test_agent_state_fingerprint_changes_with_game_state(tmp_path):
    db, service = make_service(tmp_path)
    initial = action_state_versions(db, 1001)["roulette_load"]
    service.start_roulette(1001, 2001, "甲", START)
    loaded = action_state_versions(db, 1001)["roulette_load"]
    service.fire(1001, 2001, "甲", START + timedelta(seconds=1))
    fired = action_state_versions(db, 1001)["roulette_fire"]
    assert initial != loaded
    assert loaded != fired


def stats_row(db: Database, group_id: int, user_id: int):
    with db.connect() as connection:
        return connection.execute(
            "SELECT * FROM mini_game_stats WHERE group_id=? AND user_id=?", (group_id, user_id)
        ).fetchone()


def update_active_state(db: Database, state: dict[str, object]) -> None:
    with db.connect() as connection:
        session = connection.execute(
            "SELECT session_id FROM mini_game_sessions WHERE status='active'"
        ).fetchone()
        connection.execute(
            "UPDATE mini_game_sessions SET state_json=? WHERE session_id=?",
            (json.dumps(state), int(session["session_id"])),
        )


def active_dice_value(db: Database, user_id: int) -> int:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT dice_value FROM mini_game_participants WHERE user_id=?", (user_id,)
        ).fetchone()
    return int(row["dice_value"])


def active_state(db: Database) -> dict[str, object]:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT state_json FROM mini_game_sessions WHERE status='active'"
        ).fetchone()
    return json.loads(str(row["state_json"]))


def test_roulette_records_death_and_participation_without_sorting_by_games(tmp_path):
    db, service = make_service(tmp_path)
    assert service.start_roulette(1001, 1, "发起人", START).kind == "roulette_started"
    with db.connect() as connection:
        session = connection.execute("SELECT session_id FROM mini_game_sessions").fetchone()
        connection.execute(
            "UPDATE mini_game_sessions SET state_json=? WHERE session_id=?",
            (json.dumps({"bullet_at": 2, "shots": 0}), int(session["session_id"])),
        )

    assert service.fire(1001, 7, "小夏", START + timedelta(seconds=1)).kind == "roulette_safe"
    hit = service.fire(1001, 8, "七喜", START + timedelta(seconds=2))
    assert hit.kind == "roulette_hit"
    assert "七喜" in hit.text

    safe = stats_row(db, 1001, 7)
    victim = stats_row(db, 1001, 8)
    assert safe["roulette_games"] == 1
    assert safe["roulette_deaths"] == 0
    assert victim["roulette_games"] == 1
    assert victim["roulette_deaths"] == 1

    board = service.ranking(ROULETTE, 1001)
    rows = board["sections"][0]["rows"]
    assert [(row["nickname"], row["value"], row["games"]) for row in rows] == [("七喜", 1, 1)]


def test_roulette_interactions_carry_mentions_for_safe_and_hit_players(tmp_path):
    db, service = make_service(tmp_path)
    service.start_roulette(1001, 1, "发起人", START)
    update_active_state(db, {"bullet_at": 2, "shots": 0})

    safe = service.fire(1001, 7, "小夏", START + timedelta(seconds=1))
    hit = service.fire(1001, 8, "七喜", START + timedelta(seconds=2))

    assert safe.mention_replacements == (("小夏", 7),)
    assert hit.mention_replacements == (("七喜", 8),)
    assert hit.mute_user_ids == (8,)


def test_roulette_timeout_keeps_participation_but_does_not_add_deaths(tmp_path):
    db, service = make_service(tmp_path)
    service.start_roulette(1001, 1, "发起人", START)
    with db.connect() as connection:
        session = connection.execute("SELECT session_id FROM mini_game_sessions").fetchone()
        connection.execute(
            "UPDATE mini_game_sessions SET state_json=? WHERE session_id=?",
            (json.dumps({"bullet_at": 6, "shots": 0}), int(session["session_id"])),
        )
    service.fire(1001, 7, "小夏", START + timedelta(seconds=1))

    events = service.expire_due(START + timedelta(seconds=121))
    assert [(event.game_type, event.kind) for event in events] == [(ROULETTE, "roulette_safe")]
    row = stats_row(db, 1001, 7)
    assert row["roulette_games"] == 1
    assert row["roulette_deaths"] == 0


def test_roulette_allows_one_player_to_fire_until_the_bullet_hits(tmp_path):
    db, service = make_service(tmp_path)
    service.start_roulette(1001, 1, "发起人", START)
    with db.connect() as connection:
        session = connection.execute("SELECT session_id FROM mini_game_sessions").fetchone()
        connection.execute(
            "UPDATE mini_game_sessions SET state_json=? WHERE session_id=?",
            (json.dumps({"bullet_at": 2, "shots": 0}), int(session["session_id"])),
        )

    assert service.fire(1001, 7, "小夏", START + timedelta(seconds=1)).kind == "roulette_safe"
    assert service.fire(1001, 7, "小夏", START + timedelta(seconds=2)).kind == "roulette_hit"
    row = stats_row(db, 1001, 7)
    assert (row["roulette_games"], row["roulette_deaths"]) == (1, 1)


def test_roulette_has_a_large_safe_copy_pool_and_varied_hit_copy(tmp_path):
    _db, _service = make_service(tmp_path)
    assert len(ROULETTE_SAFE_TEXTS) >= 30
    assert len(ROULETTE_HIT_TEXTS) >= 12


def test_bomb_requires_holder_and_records_passes_at_expiry(tmp_path):
    db, service = make_service(tmp_path)
    assert service.start_bomb(1001, 1, "小夏", START).kind == "bomb_started"
    assert service.throw_bomb(1001, 2, "七喜", 3, "阿北", START + timedelta(seconds=1)).kind == "not_holder"
    assert service.throw_bomb(1001, 1, "小夏", 2, "七喜", START + timedelta(seconds=2)).kind == "bomb_thrown"
    assert service.throw_bomb(1001, 2, "七喜", 3, "阿北", START + timedelta(seconds=3)).kind == "bomb_thrown"

    events = service.expire_due(START + timedelta(seconds=121))
    assert events[0].kind == "bomb_exploded"
    starter = stats_row(db, 1001, 1)
    middle = stats_row(db, 1001, 2)
    holder = stats_row(db, 1001, 3)
    assert (starter["bomb_games"], starter["bomb_passes"], starter["bomb_deaths"]) == (1, 1, 0)
    assert (middle["bomb_games"], middle["bomb_passes"], middle["bomb_deaths"]) == (1, 1, 0)
    assert (holder["bomb_games"], holder["bomb_passes"], holder["bomb_deaths"]) == (1, 0, 1)


def test_bomb_can_be_passed_back_to_a_previous_holder(tmp_path):
    db, service = make_service(tmp_path)
    service.start_bomb(1001, 1, "小夏", START)
    assert service.throw_bomb(1001, 1, "小夏", 2, "七喜", START + timedelta(seconds=1)).kind == "bomb_thrown"
    assert service.throw_bomb(1001, 2, "七喜", 1, "小夏", START + timedelta(seconds=2)).kind == "bomb_thrown"

    service.expire_due(START + timedelta(seconds=121))
    returned = stats_row(db, 1001, 1)
    passer = stats_row(db, 1001, 2)
    assert (returned["bomb_passes"], returned["bomb_deaths"]) == (1, 1)
    assert (passer["bomb_passes"], passer["bomb_deaths"]) == (1, 0)


def test_idiom_bomb_validates_chain_and_uses_configured_duration(tmp_path):
    db, service = make_service(tmp_path)
    started = service.start_bomb(
        1001, 1, "小夏", START, idiom_mode=True, duration_seconds=180
    )
    assert started.kind == "bomb_idiom_started"
    with db.connect() as connection:
        session = connection.execute("SELECT ends_at FROM mini_game_sessions").fetchone()
    assert datetime.fromisoformat(str(session["ends_at"])) == START + timedelta(seconds=180)

    first = service.throw_bomb(1001, 1, "小夏", 2, "七喜", START + timedelta(seconds=1), idiom="画蛇添足")
    assert first.kind == "bomb_thrown"
    assert "画蛇添足" in first.text
    second = service.throw_bomb(1001, 2, "七喜", 1, "小夏", START + timedelta(seconds=2), idiom="足智多谋")
    assert second.kind == "bomb_thrown"
    assert active_state(db)["idiom_last"] == "足智多谋"


def test_idiom_vocabulary_includes_confirmed_site_words():
    assert is_valid_idiom("沧海横流")
    assert is_valid_idiom("妇姑勃谿")
    assert is_valid_idiom("一举成功")
    assert is_valid_idiom("得偿所愿")


def test_idiom_bomb_entertainment_mode_accepts_four_character_words(tmp_path):
    db, service = make_service(tmp_path)
    assert is_valid_four_character_word("社会科学")
    assert is_valid_four_character_word("勇敢牛牛")
    assert not is_valid_idiom("社会科学")

    service.start_bomb(1001, 1, "小夏", START, idiom_mode=True, idiom_ruleset="entertainment")
    accepted = service.throw_bomb(
        1001, 1, "小夏", 2, "七喜", START + timedelta(seconds=1), idiom="社会科学"
    )
    assert accepted.kind == "bomb_thrown"

    service.cancel_group_session(1001, START + timedelta(seconds=2))
    service.start_bomb(1001, 1, "小夏", START, idiom_mode=True)
    rejected = service.throw_bomb(
        1001, 1, "小夏", 2, "七喜", START + timedelta(seconds=3), idiom="社会科学"
    )
    assert rejected.kind == "bomb_invalid_idiom"


def test_idiom_bomb_defaults_to_120_seconds_and_rejects_out_of_range_duration(tmp_path):
    db, service = make_service(tmp_path)
    assert service.start_bomb(1001, 1, "小夏", START, idiom_mode=True, duration_seconds=59).kind == "bomb_invalid_duration"
    assert service.start_bomb(1001, 1, "小夏", START, idiom_mode=True, duration_seconds=601).kind == "bomb_invalid_duration"
    started = service.start_bomb(1001, 1, "小夏", START, idiom_mode=True)
    assert started.kind == "bomb_idiom_started"
    with db.connect() as connection:
        session = connection.execute("SELECT ends_at FROM mini_game_sessions").fetchone()
    assert datetime.fromisoformat(str(session["ends_at"])) == START + timedelta(seconds=120)


def test_idiom_bomb_uses_a_separate_sixty_second_holder_timer(tmp_path):
    _db, service = make_service(tmp_path)
    service.start_bomb(1001, 1, "小夏", START, idiom_mode=True, duration_seconds=180)

    assert service.throw_bomb(
        1001, 1, "小夏", 2, "七喜", START + timedelta(seconds=59), idiom="画蛇添足"
    ).kind == "bomb_thrown"
    assert service.expire_due(START + timedelta(seconds=60)) == []

    expired = service.expire_due(START + timedelta(seconds=119))
    assert len(expired) == 1
    assert expired[0].kind == "bomb_holder_timeout"
    assert "七喜" in expired[0].text



def test_idiom_bomb_rejects_invalid_or_broken_words_without_passing(tmp_path):
    db, service = make_service(tmp_path)
    service.start_bomb(1001, 1, "小夏", START, idiom_mode=True, duration_seconds=60)
    invalid = service.throw_bomb(1001, 1, "小夏", 2, "七喜", START + timedelta(seconds=1), idiom="甲乙丁丙")
    assert invalid.kind == "bomb_invalid_idiom"
    assert active_state(db)["holder_id"] == 1
    assert stats_row(db, 1001, 1) is None

    assert service.throw_bomb(1001, 1, "小夏", 2, "七喜", START + timedelta(seconds=2), idiom="画蛇添足").kind == "bomb_thrown"
    broken = service.throw_bomb(1001, 2, "七喜", 3, "阿北", START + timedelta(seconds=3), idiom="风和日丽")
    assert broken.kind == "bomb_idiom_chain_break"
    assert active_state(db)["holder_id"] == 2
    with db.connect() as connection:
        passer = connection.execute(
            "SELECT pass_count FROM mini_game_participants WHERE user_id=2"
        ).fetchone()
    assert int(passer["pass_count"]) == 0


def test_bomb_time_chaos_obeys_idiom_mode_duration_cap(tmp_path):
    db, service = make_service(tmp_path)
    service.start_bomb(1001, 1, "小夏", START, idiom_mode=True, duration_seconds=60)
    update_active_state(
        db,
        {
            "holder_id": 1,
            "holder_name": "小夏",
            "last_passed_at": START.isoformat(),
            "bomb_idiom_mode": True,
            "bomb_duration_seconds": 60,
            "idiom_last": None,
            "random_event_at": (START + timedelta(seconds=30)).isoformat(),
            "random_event_type": "bomb_time_chaos",
        },
    )
    assert service.trigger_due_random_events(START + timedelta(seconds=30))[0].kind == "bomb_time_chaos"
    with db.connect() as connection:
        session = connection.execute("SELECT ends_at FROM mini_game_sessions").fetchone()
    assert datetime.fromisoformat(str(session["ends_at"])) <= START + timedelta(seconds=60)


def test_idiom_bomb_rejects_repeated_idioms(tmp_path):
    db, service = make_service(tmp_path)
    service.start_bomb(1001, 1, "小夏", START, idiom_mode=True)
    assert service.throw_bomb(1001, 1, "小夏", 2, "七喜", START + timedelta(seconds=1), idiom="画蛇添足").kind == "bomb_thrown"
    repeated = service.throw_bomb(1001, 2, "七喜", 1, "小夏", START + timedelta(seconds=2), idiom="画蛇添足")
    assert repeated.kind == "bomb_idiom_repeat"
    assert active_state(db)["holder_id"] == 2


def test_idiom_bomb_first_char_and_rewrite_events(tmp_path):
    db, service = make_service(tmp_path)
    service.start_bomb(1001, 1, "小夏", START, idiom_mode=True)
    state = active_state(db)
    state["bomb_idiom_first_char_pending"] = True
    update_active_state(db, state)
    first_char = service.throw_bomb(1001, 1, "小夏", 2, "七喜", START + timedelta(seconds=1), idiom="画蛇添足")
    assert first_char.kind == "bomb_idiom_first_char_triggered"
    assert "首字" in first_char.text
    assert active_state(db)["idiom_next_start_char"] == "画"

    state = active_state(db)
    state["bomb_idiom_rewrite_pending"] = True
    state["idiom_next_start_char"] = None
    update_active_state(db, state)
    rewritten = service.throw_bomb(1001, 2, "七喜", 1, "小夏", START + timedelta(seconds=2), idiom="足智多谋")
    assert rewritten.kind == "bomb_idiom_rewrite_triggered"
    assert active_state(db)["holder_id"] == 2
    assert "足智多谋" in active_state(db)["idiom_banned"]


def test_idiom_bomb_free_lonely_and_echo_events(tmp_path):
    db, service = make_service(tmp_path)
    service.start_bomb(1001, 1, "小夏", START, idiom_mode=True)
    assert service.throw_bomb(1001, 1, "小夏", 2, "七喜", START + timedelta(seconds=1), idiom="画蛇添足").kind == "bomb_thrown"

    state = active_state(db)
    state["idiom_free_start_pending"] = True
    update_active_state(db, state)
    assert service.throw_bomb(1001, 2, "七喜", 1, "小夏", START + timedelta(seconds=2), idiom="风和日丽").kind == "bomb_thrown"

    state = active_state(db)
    state["idiom_lonely_start_pending"] = True
    update_active_state(db, state)
    blocked = service.throw_bomb(1001, 1, "小夏", 2, "七喜", START + timedelta(seconds=3), idiom="足智多谋")
    assert blocked.kind == "bomb_idiom_lonely_blocked"
    accepted = service.throw_bomb(1001, 1, "小夏", 2, "七喜", START + timedelta(seconds=4), idiom="春风得意")
    assert accepted.kind == "bomb_thrown"

    state = active_state(db)
    state["idiom_used"] = ["画蛇添足", "足智多谋", "谋事在人", "人山人海"]
    state["idiom_last"] = "人山人海"
    state["random_event_at"] = (START + timedelta(seconds=30)).isoformat()
    state["random_event_type"] = "bomb_idiom_echo_anchor"
    update_active_state(db, state)
    echo = service.trigger_due_random_events(START + timedelta(seconds=30))[0]
    assert echo.kind == "bomb_idiom_echo_anchor"
    assert active_state(db)["idiom_chain_anchor"] in {"画蛇添足", "足智多谋", "谋事在人"}


def test_dice_uses_unique_values_and_awards_high_and_low(tmp_path):
    db, service = make_service(tmp_path)
    first = service.roll_dice(1001, 1, "小夏", START)
    second = service.roll_dice(1001, 2, "七喜", START + timedelta(seconds=1))
    assert first.kind == second.kind == "dice_roll"
    assert service.roll_dice(1001, 1, "小夏", START + timedelta(seconds=2)).kind == "already"

    with db.connect() as connection:
        values = [
            int(row["dice_value"])
            for row in connection.execute("SELECT dice_value FROM mini_game_participants ORDER BY user_id")
        ]
    assert len(values) == len(set(values)) == 2
    assert all(1 <= value <= 120 for value in values)

    events = service.expire_due(START + timedelta(seconds=121))
    assert events[0].kind == "dice_result"
    one = stats_row(db, 1001, 1)
    two = stats_row(db, 1001, 2)
    assert one["dice_games"] == two["dice_games"] == 1
    assert one["dice_highs"] + two["dice_highs"] == 1
    assert one["dice_lows"] + two["dice_lows"] == 1


def test_guess_number_gives_directional_feedback_and_records_wins_and_misses(tmp_path):
    db, service = make_service(tmp_path)
    assert service.start_guess(1001, 1, "发起人", START).kind == "guess_started"
    update_active_state(db, {"target": 500, "guess_attempts": 0})

    low = service.guess_number(1001, 7, "小夏", 123, START + timedelta(seconds=1))
    high = service.guess_number(1001, 7, "小夏", 876, START + timedelta(seconds=2))
    hit = service.guess_number(1001, 8, "七喜", 500, START + timedelta(seconds=3))

    assert low.kind == "guess_low"
    assert high.kind == "guess_high"
    assert "500" not in low.text
    assert "500" not in high.text
    assert hit.kind == "guess_hit"
    assert "500" in hit.text
    assert stats_row(db, 1001, 7)["guess_games"] == 1
    assert stats_row(db, 1001, 7)["guess_misses"] == 2
    assert stats_row(db, 1001, 7)["guess_wins"] == 0
    assert stats_row(db, 1001, 8)["guess_games"] == 1
    assert stats_row(db, 1001, 8)["guess_misses"] == 0
    assert stats_row(db, 1001, 8)["guess_wins"] == 1

    board = service.ranking(GUESS, 1001)
    assert board["sections"][0]["title"] == "传奇智力王"
    assert board["sections"][0]["rows"][0]["nickname"] == "七喜"
    assert board["sections"][1]["title"] == "传奇小🐽"
    assert board["sections"][1]["rows"][0]["nickname"] == "小夏"


def test_guess_number_rejects_invalid_values_and_reveals_answer_at_timeout(tmp_path):
    db, service = make_service(tmp_path)
    service.start_guess(1001, 1, "发起人", START)
    update_active_state(db, {"target": 42, "guess_attempts": 0})

    assert service.guess_number(1001, 7, "小夏", None, START + timedelta(seconds=1)).kind == "invalid"
    assert service.guess_number(1001, 7, "小夏", 1000, START + timedelta(seconds=2)).kind == "invalid"
    assert service.guess_number(1001, 7, "小夏", 1, START + timedelta(seconds=3)).kind == "guess_low"
    assert service.guess_number(1001, 8, "七喜", 1, START + timedelta(seconds=4)).kind == "guess_low"
    events = service.expire_due(START + timedelta(seconds=121))

    assert [(event.game_type, event.kind) for event in events] == [(GUESS, "guess_timeout")]
    assert "42" in events[0].text
    assert events[0].mute_user_ids == (7, 8)
    assert events[0].mention_replacements == (("小夏", 7), ("七喜", 8))


def test_guess_feedback_uses_each_high_low_and_invalid_phrase_once_per_session(tmp_path):
    db, service = make_service(tmp_path)

    service.start_guess(1001, 1, "发起人", START)
    update_active_state(db, {"target": 500, "guess_attempts": 0})
    high_texts = {
        service.guess_number(1001, 7, "小夏", 900, START + timedelta(seconds=index)).text
        for index in range(1, 21)
    }
    assert len(high_texts) == 20

    service.cancel_group_session(1001)
    service.start_guess(1001, 1, "发起人", START)
    update_active_state(db, {"target": 500, "guess_attempts": 0})
    low_texts = {
        service.guess_number(1001, 7, "小夏", 100, START + timedelta(seconds=index)).text
        for index in range(1, 21)
    }
    assert len(low_texts) == 20

    service.cancel_group_session(1001)
    service.start_guess(1001, 1, "发起人", START)
    invalid_texts = {
        service.guess_number(1001, 7, "小夏", None, START + timedelta(seconds=index)).text
        for index in range(1, 21)
    }
    assert len(invalid_texts) == 20


def test_dice_rolls_include_a_score_comment_and_special_scores_have_their_own_line(tmp_path):
    _db, service = make_service(tmp_path)
    roll = service.roll_dice(1001, 1, "小夏", START)

    assert roll.kind == "dice_roll"
    assert roll.text.count("\n") == 1
    assert "起势" in service._dice_comment(74)
    comment_66 = service._dice_comment(66)
    assert "双六" in comment_66 or "六六大顺" in comment_66


def test_random_event_is_guaranteed_and_first_roulette_event_scheduled_after_five_seconds(tmp_path):
    db, service = make_service(tmp_path)
    service.start_roulette(1001, 1, "发起人", START)
    state = active_state(db)

    scheduled = datetime.fromisoformat(state["random_event_at"])
    assert 5 <= (scheduled - START).total_seconds() <= 10
    assert 3 <= int(state["random_event_total"]) <= 5
    assert state["random_event_count"] == 0


def test_random_event_counts_follow_the_game_specific_ranges(tmp_path):
    db, service = make_service(tmp_path)
    service.start_bomb(1001, 1, "小夏", START)
    assert 3 <= int(active_state(db)["random_event_total"]) <= 5

    service.cancel_group_session(1001, START + timedelta(seconds=1))
    service.roll_dice(1001, 2, "七喜", START + timedelta(seconds=2))
    assert 5 <= int(active_state(db)["random_event_total"]) <= 10

    service.cancel_group_session(1001, START + timedelta(seconds=3))
    service.start_guess(1001, 3, "小林", START + timedelta(seconds=4))
    assert 2 <= int(active_state(db)["random_event_total"]) <= 3


def test_idiom_bomb_events_scale_with_duration_and_cool_down_for_thirty_seconds(tmp_path):
    db, service = make_service(tmp_path)
    service.start_bomb(1001, 1, "小夏", START, idiom_mode=True, duration_seconds=60)
    assert int(active_state(db)["random_event_total"]) == 2

    service.cancel_group_session(1001, START + timedelta(seconds=1))
    service.start_bomb(1001, 1, "小夏", START, idiom_mode=True, duration_seconds=600)
    assert int(active_state(db)["random_event_total"]) == 20

    service.cancel_group_session(1001, START + timedelta(seconds=2))
    service.start_bomb(1001, 1, "小夏", START, idiom_mode=True, duration_seconds=120)
    state = active_state(db)
    state.update(
        {
            "random_event_at": (START + timedelta(seconds=15)).isoformat(),
            "random_event_type": "bomb_hot_hand",
            "random_event_total": 4,
            "random_event_count": 0,
            "random_event_actions_since_last": 0,
            "random_event_history": [],
        }
    )
    update_active_state(db, state)
    assert service.trigger_due_random_events(START + timedelta(seconds=15))[0].kind == "bomb_hot_hand"
    scheduled = datetime.fromisoformat(active_state(db)["random_event_at"])
    assert scheduled == START + timedelta(seconds=45)
    assert service.trigger_due_random_events(START + timedelta(seconds=45)) == []


def test_random_events_require_five_seconds_and_a_player_interaction_between_triggers(
    tmp_path, monkeypatch
):
    db, service = make_service(tmp_path)
    monkeypatch.setattr(
        service,
        "_select_random_event_type",
        lambda _game_type, _state, _participants: "roulette_reflection",
    )
    service.start_roulette(1001, 1, "小夏", START)
    update_active_state(
        db,
        {
            "bullet_at": 6,
            "shots": 0,
            "shot_log": [],
            "random_event_at": (START + timedelta(seconds=15)).isoformat(),
            "random_event_type": "roulette_reflection",
            "random_event_total": 3,
            "random_event_count": 0,
            "random_event_actions_since_last": 0,
            "random_event_history": [],
        },
    )

    assert service.trigger_due_random_events(START + timedelta(seconds=14)) == []
    first_at = START + timedelta(seconds=15)
    assert len(service.trigger_due_random_events(first_at)) == 1
    state = active_state(db)
    second_at = datetime.fromisoformat(str(state["random_event_at"]))
    assert (second_at - first_at).total_seconds() >= 5

    assert service.trigger_due_random_events(second_at) == []
    assert service.fire(1001, 1, "小夏", first_at + timedelta(seconds=1)).kind == "roulette_safe"
    assert len(service.trigger_due_random_events(second_at)) == 1
    state = active_state(db)
    third_at = datetime.fromisoformat(str(state["random_event_at"]))
    assert (third_at - second_at).total_seconds() >= 5

    assert service.trigger_due_random_events(third_at) == []
    assert service.fire(1001, 2, "七喜", second_at + timedelta(seconds=1)).kind in {
        "roulette_safe",
        "roulette_hit",
            "roulette_misfire_triggered",
            "roulette_finger_cramp_safe",
            "roulette_finger_cramp_hit",
            "roulette_burst_revolver_safe",
            "roulette_burst_revolver_hit",
            "roulette_burst_rifle_safe",
            "roulette_burst_rifle_hit",
            "roulette_aim_drift_safe",
            "roulette_barrel_burst_triggered",
            "roulette_area_explosion_triggered",
        }
    assert len(service.trigger_due_random_events(third_at)) == 1
    state = active_state(db)
    assert state["random_event_count"] == 3
    assert state["random_event_at"] is None


def test_roulette_bond_event_adds_a_second_death(tmp_path):
    db, service = make_service(tmp_path)
    service.start_roulette(1001, 1, "小夏", START)
    update_active_state(
        db,
        {
            "bullet_at": 2,
            "shots": 0,
            "shot_log": [],
            "random_event_at": (START + timedelta(seconds=30)).isoformat(),
            "random_event_type": "roulette_bond",
            "random_event_checked": False,
        },
    )
    assert service.fire(1001, 1, "小夏", START + timedelta(seconds=1)).kind == "roulette_safe"
    events = service.trigger_due_random_events(START + timedelta(seconds=30))
    assert [event.kind for event in events] == ["roulette_bond"]

    assert service.fire(1001, 2, "七喜", START + timedelta(seconds=31)).kind == "roulette_hit"
    assert stats_row(db, 1001, 1)["roulette_deaths"] == 1
    assert stats_row(db, 1001, 2)["roulette_deaths"] == 1
    assert service.trigger_due_random_events(START + timedelta(seconds=32)) == []


def test_roulette_reflection_moves_a_lethal_shot_to_the_previous_shooter(tmp_path):
    db, service = make_service(tmp_path)
    service.start_roulette(1001, 1, "小夏", START)
    update_active_state(
        db,
        {
            "bullet_at": 2,
            "shots": 0,
            "shot_log": [],
            "roulette_reflection_active": True,
            "random_event_at": (START + timedelta(seconds=30)).isoformat(),
            "random_event_type": "roulette_reflection",
            "random_event_checked": False,
        },
    )
    assert [event.kind for event in service.trigger_due_random_events(START + timedelta(seconds=30))] == [
        "roulette_reflection"
    ]
    assert service.fire(1001, 1, "小夏", START + timedelta(seconds=31)).kind == "roulette_safe"
    hit = service.fire(1001, 2, "七喜", START + timedelta(seconds=32))

    assert hit.kind == "roulette_hit"
    assert "小夏" in hit.text
    assert stats_row(db, 1001, 1)["roulette_deaths"] == 1
    assert stats_row(db, 1001, 2)["roulette_deaths"] == 0


def test_dice_regret_event_keeps_the_original_value_without_a_second_roll(tmp_path):
    db, service = make_service(tmp_path)
    service.roll_dice(1001, 1, "小夏", START)
    original = active_dice_value(db, 1)
    update_active_state(
        db,
        {
            "random_event_at": (START + timedelta(seconds=30)).isoformat(),
            "random_event_type": "dice_regret",
            "random_event_checked": False,
        },
    )
    assert [event.kind for event in service.trigger_due_random_events(START + timedelta(seconds=30))] == [
        "dice_regret"
    ]
    service.expire_due(START + timedelta(seconds=121))
    assert active_dice_value(db, 1) == original


def test_dice_regret_event_rethrow_overrides_the_one_roll_rule(tmp_path):
    db, service = make_service(tmp_path)
    service.roll_dice(1001, 1, "小夏", START)
    original = active_dice_value(db, 1)
    update_active_state(
        db,
        {
            "random_event_at": (START + timedelta(seconds=30)).isoformat(),
            "random_event_type": "dice_regret",
            "random_event_checked": False,
        },
    )
    service.trigger_due_random_events(START + timedelta(seconds=30))
    assert service.roll_dice(1001, 1, "小夏", START + timedelta(seconds=31)).kind == "dice_reroll"
    assert active_dice_value(db, 1) != original
    assert service.roll_dice(1001, 1, "小夏", START + timedelta(seconds=32)).kind == "already"


def test_dice_force_event_changes_the_selected_players_existing_value(tmp_path):
    db, service = make_service(tmp_path)
    service.roll_dice(1001, 1, "小夏", START)
    original = active_dice_value(db, 1)
    update_active_state(
        db,
        {
            "random_event_at": (START + timedelta(seconds=30)).isoformat(),
            "random_event_type": "dice_force",
            "random_event_checked": False,
        },
    )

    assert [event.kind for event in service.trigger_due_random_events(START + timedelta(seconds=30))] == [
        "dice_force"
    ]
    assert active_dice_value(db, 1) != original
    assert service.trigger_due_random_events(START + timedelta(seconds=31)) == []


def test_dice_random_events_continue_after_a_regret_reroll(tmp_path):
    db, service = make_service(tmp_path)
    service.roll_dice(1001, 1, "小夏", START)
    service.roll_dice(1001, 2, "七喜", START + timedelta(seconds=1))
    update_active_state(
        db,
        {
            "random_event_at": (START + timedelta(seconds=15)).isoformat(),
            "random_event_type": "dice_regret",
            "random_event_total": 2,
            "random_event_count": 0,
            "random_event_actions_since_last": 0,
            "random_event_history": [],
        },
    )

    first_at = START + timedelta(seconds=15)
    assert [event.kind for event in service.trigger_due_random_events(first_at)] == ["dice_regret"]
    state = active_state(db)
    regret_user_id = int(next(iter(state["dice_regrets"])))
    assert service.roll_dice(1001, regret_user_id, "重投玩家", first_at + timedelta(seconds=1)).kind == "dice_reroll"

    state = active_state(db)
    second_at = datetime.fromisoformat(str(state["random_event_at"]))
    state["random_event_type"] = "dice_force"
    update_active_state(db, state)
    assert [event.kind for event in service.trigger_due_random_events(second_at)] == ["dice_force"]
    state = active_state(db)
    assert state["random_event_count"] == 2
    assert state["random_event_at"] is None


def test_roulette_shift_and_misfire_events_change_the_right_state(tmp_path):
    db, service = make_service(tmp_path)
    service.start_roulette(1001, 1, "小夏", START)
    update_active_state(
        db,
        {
            "bullet_at": 2,
            "shots": 0,
            "shot_log": [],
            "random_event_at": (START + timedelta(seconds=30)).isoformat(),
            "random_event_type": "roulette_shift",
        },
    )
    assert [event.kind for event in service.trigger_due_random_events(START + timedelta(seconds=30))] == [
        "roulette_shift"
    ]
    assert int(active_state(db)["bullet_at"]) != 2

    update_active_state(
        db,
        {
            "bullet_at": 6,
            "shots": 0,
            "shot_log": [],
            "random_event_at": (START + timedelta(seconds=31)).isoformat(),
            "random_event_type": "roulette_misfire",
        },
    )
    hidden = service.trigger_due_random_events(START + timedelta(seconds=31))
    assert hidden[0].kind == "roulette_misfire"
    assert not hidden[0].announce
    triggered = service.fire(1001, 7, "七喜", START + timedelta(seconds=32))
    assert triggered.kind == "roulette_misfire_triggered"
    assert active_state(db)["shots"] == 0
    assert stats_row(db, 1001, 7)["roulette_deaths"] == 1


def test_roulette_finger_cramp_fires_twice_and_the_actor_dies_on_either_hit(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "bot.services.mini_games.secrets.choice", lambda seq: seq[0]
    )
    db, service = make_service(tmp_path)
    service.start_roulette(1001, 1, "小夏", START)
    update_active_state(
        db,
        {
            "bullet_at": 2,
            "shots": 0,
            "shot_log": [],
            "roulette_reflection_active": True,
            "random_event_at": (START + timedelta(seconds=30)).isoformat(),
            "random_event_type": "roulette_finger_cramp",
        },
    )
    hidden = service.trigger_due_random_events(START + timedelta(seconds=30))
    assert hidden[0].kind == "roulette_finger_cramp"
    assert not hidden[0].announce

    outcome = service.fire(1001, 7, "七喜", START + timedelta(seconds=31))
    assert outcome.kind == "roulette_finger_cramp_hit"
    assert outcome.ended
    assert "手指抽筋" in outcome.text
    assert stats_row(db, 1001, 7)["roulette_deaths"] == 1

    reflected_path = tmp_path / "reflected"
    reflected_path.mkdir()
    reflected_db, reflected_service = make_service(reflected_path)
    reflected_service.start_roulette(1001, 1, "小夏", START)
    with reflected_db.connect() as connection:
        session = connection.execute("SELECT session_id FROM mini_game_sessions").fetchone()
        reflected_service._add_participant(
            connection, int(session["session_id"]), 1, "小夏", 1, None, START
        )
    update_active_state(
        reflected_db,
        {
            "bullet_at": 1,
            "shots": 0,
            "shot_log": [{"user_id": 1, "nickname": "小夏"}],
            "roulette_reflection_active": True,
            "random_event_at": (START + timedelta(seconds=30)).isoformat(),
            "random_event_type": "roulette_finger_cramp",
        },
    )
    reflected_service.trigger_due_random_events(START + timedelta(seconds=30))
    reflected = reflected_service.fire(1001, 7, "七喜", START + timedelta(seconds=31))
    assert reflected.kind == "roulette_finger_cramp_hit"
    assert "小夏" in reflected.text
    assert stats_row(reflected_db, 1001, 1)["roulette_deaths"] == 1
    assert stats_row(reflected_db, 1001, 7)["roulette_deaths"] == 0

    safe_path = tmp_path / "safe"
    safe_path.mkdir()
    _second_db, second_service = make_service(safe_path)
    second_service.start_roulette(1001, 1, "小夏", START)
    update_active_state(
        _second_db,
        {
            "bullet_at": 3,
            "shots": 0,
            "shot_log": [],
            "random_event_at": (START + timedelta(seconds=30)).isoformat(),
            "random_event_type": "roulette_finger_cramp",
        },
    )
    second_service.trigger_due_random_events(START + timedelta(seconds=30))
    safe = second_service.fire(1001, 7, "七喜", START + timedelta(seconds=31))
    assert safe.kind == "roulette_finger_cramp_safe"
    state = active_state(_second_db)
    assert state["shots"] == 2
    assert len(state["shot_log"]) == 2


def test_dice_fate_swap_and_reverse_keep_every_value_unique(tmp_path):
    db, service = make_service(tmp_path)
    service.roll_dice(1001, 1, "小夏", START)
    service.roll_dice(1001, 2, "七喜", START + timedelta(seconds=1))
    before = {user_id: active_dice_value(db, user_id) for user_id in (1, 2)}
    update_active_state(
        db,
        {"random_event_at": (START + timedelta(seconds=30)).isoformat(), "random_event_type": "dice_fate_swap"},
    )
    assert service.trigger_due_random_events(START + timedelta(seconds=30))[0].kind == "dice_fate_swap"
    assert active_dice_value(db, 1) == before[2]
    assert active_dice_value(db, 2) == before[1]

    update_active_state(
        db,
        {"random_event_at": (START + timedelta(seconds=31)).isoformat(), "random_event_type": "dice_reverse"},
    )
    reverse = service.trigger_due_random_events(START + timedelta(seconds=31))[0]
    assert reverse.kind == "dice_reverse"
    assert "小夏" in reverse.text and "七喜" in reverse.text and "→" in reverse.text
    assert active_dice_value(db, 1) == 121 - before[2]
    assert active_dice_value(db, 2) == 121 - before[1]


def test_dice_throne_waits_for_the_next_roll_then_swaps_with_the_leader(tmp_path):
    db, service = make_service(tmp_path)
    service.roll_dice(1001, 1, "小夏", START)
    with db.connect() as connection:
        connection.execute("UPDATE mini_game_participants SET dice_value=120 WHERE user_id=1")
    update_active_state(
        db,
        {"random_event_at": (START + timedelta(seconds=30)).isoformat(), "random_event_type": "dice_throne"},
    )
    hidden = service.trigger_due_random_events(START + timedelta(seconds=30))
    assert hidden[0].kind == "dice_throne"
    assert not hidden[0].announce
    outcome = service.roll_dice(1001, 2, "七喜", START + timedelta(seconds=31))
    assert outcome.kind == "dice_throne_triggered"
    assert "七喜" in outcome.text and "小夏" in outcome.text and "→" in outcome.text
    assert active_dice_value(db, 2) == 120


def test_bomb_events_change_holder_timer_and_inertia_is_deferred(tmp_path):
    db, service = make_service(tmp_path)
    service.start_bomb(1001, 1, "小夏", START)
    service.throw_bomb(1001, 1, "小夏", 2, "七喜", START + timedelta(seconds=1))
    with db.connect() as connection:
        original_end = connection.execute("SELECT ends_at FROM mini_game_sessions").fetchone()["ends_at"]
    update_active_state(
        db,
        {"holder_id": 2, "holder_name": "七喜", "random_event_at": (START + timedelta(seconds=30)).isoformat(), "random_event_type": "bomb_hot_hand"},
    )
    assert service.trigger_due_random_events(START + timedelta(seconds=30))[0].kind == "bomb_hot_hand"
    with db.connect() as connection:
        accelerated_end = connection.execute("SELECT ends_at FROM mini_game_sessions").fetchone()["ends_at"]
    assert accelerated_end < original_end

    update_active_state(
        db,
        {"holder_id": 2, "holder_name": "七喜", "random_event_at": (START + timedelta(seconds=31)).isoformat(), "random_event_type": "bomb_tracking"},
    )
    assert service.trigger_due_random_events(START + timedelta(seconds=31))[0].kind == "bomb_tracking"
    assert active_state(db)["holder_id"] == 1

    update_active_state(
        db,
        {"holder_id": 1, "holder_name": "小夏", "random_event_at": (START + timedelta(seconds=32)).isoformat(), "random_event_type": "bomb_inertia"},
    )
    hidden = service.trigger_due_random_events(START + timedelta(seconds=32))
    assert hidden[0].kind == "bomb_inertia"
    assert not hidden[0].announce
    outcome = service.throw_bomb(1001, 1, "小夏", 2, "七喜", START + timedelta(seconds=33))
    assert outcome.kind == "bomb_inertia_triggered"
    assert active_state(db)["holder_id"] == 1


def test_bomb_black_hole_and_reverse_delivery_resolve_on_the_next_pass(tmp_path):
    db, service = make_service(tmp_path)
    service.start_bomb(1001, 1, "小夏", START)
    service.throw_bomb(1001, 1, "小夏", 2, "七喜", START + timedelta(seconds=1))
    service.throw_bomb(1001, 2, "七喜", 3, "小林", START + timedelta(seconds=2))
    update_active_state(
        db,
        {
            "holder_id": 3,
            "holder_name": "小林",
            "bomb_previous_holder_id": 2,
            "bomb_previous_holder_name": "七喜",
            "random_event_at": (START + timedelta(seconds=30)).isoformat(),
            "random_event_type": "bomb_black_hole",
        },
    )
    hidden = service.trigger_due_random_events(START + timedelta(seconds=30))
    assert hidden[0].kind == "bomb_black_hole"
    assert not hidden[0].announce
    outcome = service.throw_bomb(1001, 3, "小林", 4, "阿北", START + timedelta(seconds=31))
    assert outcome.kind == "bomb_black_hole_triggered"
    assert active_state(db)["holder_id"] in {1, 2}
    assert active_state(db)["holder_id"] != 4

    update_active_state(
        db,
        {
            "holder_id": 2,
            "holder_name": "七喜",
            "bomb_previous_holder_id": 1,
            "bomb_previous_holder_name": "小夏",
            "random_event_at": (START + timedelta(seconds=40)).isoformat(),
            "random_event_type": "bomb_reverse_delivery",
        },
    )
    hidden = service.trigger_due_random_events(START + timedelta(seconds=40))
    assert hidden[0].kind == "bomb_reverse_delivery"
    assert not hidden[0].announce
    outcome = service.throw_bomb(1001, 2, "七喜", 5, "阿南", START + timedelta(seconds=41))
    assert outcome.kind == "bomb_reverse_delivery_triggered"
    assert active_state(db)["holder_id"] == 1


def test_dice_double_events_and_mirror_keep_scores_unique(tmp_path):
    db, service = make_service(tmp_path)
    service.roll_dice(1001, 1, "小夏", START)
    update_active_state(
        db,
        {"random_event_at": (START + timedelta(seconds=30)).isoformat(), "random_event_type": "dice_double_luck"},
    )
    hidden = service.trigger_due_random_events(START + timedelta(seconds=30))
    assert hidden[0].kind == "dice_double_luck"
    assert not hidden[0].announce
    outcome = service.roll_dice(1001, 2, "七喜", START + timedelta(seconds=31))
    assert outcome.kind == "dice_double_luck_triggered"
    assert "双倍好运" in outcome.text

    update_active_state(
        db,
        {"random_event_at": (START + timedelta(seconds=32)).isoformat(), "random_event_type": "dice_double_misfortune"},
    )
    hidden = service.trigger_due_random_events(START + timedelta(seconds=32))
    assert hidden[0].kind == "dice_double_misfortune"
    outcome = service.roll_dice(1001, 3, "小林", START + timedelta(seconds=33))
    assert outcome.kind == "dice_double_misfortune_triggered"
    assert "双倍倒霉" in outcome.text

    with db.connect() as connection:
        connection.execute("UPDATE mini_game_participants SET dice_value=20 WHERE user_id=1")
        connection.execute("UPDATE mini_game_participants SET dice_value=70 WHERE user_id=2")
        connection.execute("UPDATE mini_game_participants SET dice_value=90 WHERE user_id=3")
    update_active_state(
        db,
        {"random_event_at": (START + timedelta(seconds=34)).isoformat(), "random_event_type": "dice_mirror"},
    )
    mirror = service.trigger_due_random_events(START + timedelta(seconds=34))[0]
    assert mirror.kind == "dice_mirror"
    assert "→" in mirror.text
    with db.connect() as connection:
        values = [row["dice_value"] for row in connection.execute("SELECT dice_value FROM mini_game_participants")]
    assert len(values) == len(set(values))
    assert set(values) != {20, 70, 90}


def test_new_dice_value_events_keep_scores_unique_and_explain_changes(tmp_path):
    db, service = make_service(tmp_path)
    for user_id, name, offset in ((1, "小夏", 0), (2, "七喜", 1), (3, "小林", 2)):
        service.roll_dice(1001, user_id, name, START + timedelta(seconds=offset))

    def set_values() -> None:
        with db.connect() as connection:
            connection.execute("UPDATE mini_game_participants SET dice_value=20 WHERE user_id=1")
            connection.execute("UPDATE mini_game_participants SET dice_value=60 WHERE user_id=2")
            connection.execute("UPDATE mini_game_participants SET dice_value=90 WHERE user_id=3")

    def values() -> list[int]:
        with db.connect() as connection:
            return [row["dice_value"] for row in connection.execute("SELECT dice_value FROM mini_game_participants")]

    for event_type in ("dice_adjacent", "dice_half", "dice_comeback"):
        set_values()
        update_active_state(
            db,
            {"random_event_at": (START + timedelta(seconds=30)).isoformat(), "random_event_type": event_type},
        )
        event = service.trigger_due_random_events(START + timedelta(seconds=30))[0]
        assert event.kind == event_type
        assert "→" in event.text
        current_values = values()
        assert len(current_values) == len(set(current_values))

    set_values()
    update_active_state(
        db,
        {"random_event_at": (START + timedelta(seconds=31)).isoformat(), "random_event_type": "dice_relief"},
    )
    relief = service.trigger_due_random_events(START + timedelta(seconds=31))[0]
    assert relief.kind == "dice_relief"
    assert active_dice_value(db, 1) > 20
    assert "20 →" in relief.text

    set_values()
    update_active_state(
        db,
        {"random_event_at": (START + timedelta(seconds=32)).isoformat(), "random_event_type": "dice_tax"},
    )
    tax = service.trigger_due_random_events(START + timedelta(seconds=32))[0]
    assert tax.kind == "dice_tax"
    assert active_dice_value(db, 3) < 90
    assert "90 →" in tax.text


def test_new_dice_deferred_events_apply_to_the_next_roll(tmp_path):
    db, service = make_service(tmp_path)
    service.roll_dice(1001, 1, "小夏", START)
    with db.connect() as connection:
        connection.execute("UPDATE mini_game_participants SET dice_value=60 WHERE user_id=1")

    update_active_state(
        db,
        {"random_event_at": (START + timedelta(seconds=30)).isoformat(), "random_event_type": "dice_high_platform"},
    )
    high = service.trigger_due_random_events(START + timedelta(seconds=30))[0]
    assert not high.announce
    high_roll = service.roll_dice(1001, 2, "七喜", START + timedelta(seconds=31))
    assert high_roll.kind == "dice_high_platform_triggered"
    assert 91 <= active_dice_value(db, 2) <= 120
    assert "高台跃迁" in high_roll.text

    update_active_state(
        db,
        {"random_event_at": (START + timedelta(seconds=32)).isoformat(), "random_event_type": "dice_abyss"},
    )
    abyss = service.trigger_due_random_events(START + timedelta(seconds=32))[0]
    assert not abyss.announce
    abyss_roll = service.roll_dice(1001, 3, "小林", START + timedelta(seconds=33))
    assert abyss_roll.kind == "dice_abyss_triggered"
    assert 1 <= active_dice_value(db, 3) <= 30
    assert "深渊试炼" in abyss_roll.text

    update_active_state(
        db,
        {"random_event_at": (START + timedelta(seconds=34)).isoformat(), "random_event_type": "dice_twin"},
    )
    twin = service.trigger_due_random_events(START + timedelta(seconds=34))[0]
    assert not twin.announce
    twin_roll = service.roll_dice(1001, 4, "阿北", START + timedelta(seconds=35))
    assert twin_roll.kind == "dice_twin_triggered"
    assert "双生骰面" in twin_roll.text and "→" in twin_roll.text
    current_values = []
    with db.connect() as connection:
        current_values = [row["dice_value"] for row in connection.execute("SELECT dice_value FROM mini_game_participants")]
    assert len(current_values) == len(set(current_values))


def test_guess_divergent_and_temperature_events_recalculate_without_revealing_the_answer(tmp_path):
    db, service = make_service(tmp_path)
    service.start_guess(1001, 1, "小夏", START)
    update_active_state(
        db,
        {"target": 500, "guess_attempts": 9, "random_event_at": (START + timedelta(seconds=30)).isoformat(), "random_event_type": "guess_divergent"},
    )
    hidden = service.trigger_due_random_events(START + timedelta(seconds=30))
    assert hidden[0].kind == "guess_divergent"
    assert not hidden[0].announce
    outcome = service.guess_number(1001, 7, "七喜", 500, START + timedelta(seconds=31))
    assert outcome.kind in {"guess_high", "guess_low"}
    assert "发散思维" in outcome.text
    assert not outcome.ended
    state = active_state(db)
    assert int(state["target"]) // 100 == 5
    assert int(state["target"]) != 500
    assert state["guess_attempts"] == 9

    update_active_state(
        db,
        {
            "target": 500,
            "last_wrong_guess": {"name": "七喜", "value": 490},
            "random_event_at": (START + timedelta(seconds=32)).isoformat(),
            "random_event_type": "guess_temperature",
        },
    )
    temperature = service.trigger_due_random_events(START + timedelta(seconds=32))[0]
    assert temperature.kind == "guess_temperature"
    assert "距离答案不足 50" in temperature.text


def test_guess_echo_and_digit_vision_reveal_only_partial_clues(tmp_path):
    db, service = make_service(tmp_path)
    service.start_guess(1001, 1, "小夏", START)
    update_active_state(
        db,
        {"target": 507, "random_event_at": (START + timedelta(seconds=30)).isoformat(), "random_event_type": "guess_echo"},
    )
    echo = service.trigger_due_random_events(START + timedelta(seconds=30))[0]
    assert echo.kind == "guess_echo"
    assert "数字回声" in echo.text
    assert "507" not in echo.text

    update_active_state(
        db,
        {"target": 507, "random_event_at": (START + timedelta(seconds=31)).isoformat(), "random_event_type": "guess_digit_vision"},
    )
    vision = service.trigger_due_random_events(START + timedelta(seconds=31))[0]
    assert vision.kind == "guess_digit_vision"
    assert "数位透视" in vision.text
    assert "507" not in vision.text


def test_guess_cursed_values_are_never_answers_and_trigger_a_mute(tmp_path):
    db = Database(tmp_path / "bot.db")
    db.configure_groups((1001,))
    service = MiniGameService(db, cursed_guess_numbers=(13,))
    service.start_guess(1001, 1, "starter", START)

    assert int(active_state(db)["target"]) != 13
    cursed = service.guess_number(1001, 7, "player", 13, START + timedelta(seconds=1))

    assert cursed.kind == "guess_cursed"
    assert cursed.mute_user_ids == (7,)
    assert cursed.mention_user_ids == (7,)
    assert active_state(db)["guess_attempts"] == 0
    with db.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM mini_game_participants").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM mini_game_stats WHERE user_id=7").fetchone()[0] == 0


def test_default_guess_cursed_values_include_33(tmp_path):
    _db, service = make_service(tmp_path)

    assert 33 in service.cursed_guess_numbers


def test_guess_feedback_adds_close_or_matching_position_clues(tmp_path):
    db, service = make_service(tmp_path)
    service.start_guess(1001, 1, "starter", START)
    update_active_state(db, {"target": 507, "guess_attempts": 0})

    distant = service.guess_number(1001, 7, "player", 307, START + timedelta(seconds=1))
    close = service.guess_number(1001, 8, "other", 450, START + timedelta(seconds=2))

    assert distant.kind == "guess_low"
    assert "十位" in distant.text and "个位" in distant.text
    assert close.kind == "guess_low"
    assert "\n" in close.text


def test_roulette_burst_revolver_uses_an_independent_chamber(tmp_path):
    db, service = make_service(tmp_path)
    service.start_roulette(1001, 1, "first", START)
    update_active_state(db, {"shots": 0, "bullet_at": 6, "shot_log": []})
    service.fire(1001, 1, "first", START + timedelta(seconds=1))
    update_active_state(
        db,
        {
            "shots": 0,
            "bullet_at": 6,
            "roulette_burst_revolver_pending": True,
            "roulette_burst_bullet_at": 2,
        },
    )

    outcome = service.fire(1001, 2, "second", START + timedelta(seconds=2))

    assert outcome.kind == "roulette_burst_revolver_hit"
    assert not outcome.ended
    assert outcome.mute_user_ids == (1,)
    assert active_state(db)["shots"] == 0
    assert stats_row(db, 1001, 1)["roulette_deaths"] == 1


def test_roulette_burst_rifle_can_hit_multiple_players_without_advancing_main_chamber(tmp_path):
    db, service = make_service(tmp_path)
    service.start_roulette(1001, 1, "first", START)
    update_active_state(db, {"shots": 1, "bullet_at": 6, "shot_log": []})
    service.fire(1001, 1, "first", START + timedelta(seconds=1))
    update_active_state(
        db,
        {
            "shots": 1,
            "bullet_at": 6,
            "roulette_burst_rifle_pending": True,
            "roulette_burst_rifle_bullet_count": 3,
            "roulette_burst_rifle_bullet_slots": [1, 2, 5],
        },
    )

    outcome = service.fire(1001, 2, "second", START + timedelta(seconds=2))

    assert outcome.kind == "roulette_burst_rifle_hit"
    assert not outcome.ended
    assert outcome.mute_user_ids == (2, 1)
    assert active_state(db)["shots"] == 1
    assert stats_row(db, 1001, 1)["roulette_deaths"] == 1
    assert stats_row(db, 1001, 2)["roulette_deaths"] == 1


def test_roulette_burst_rifle_can_miss_all_current_participants(tmp_path):
    db, service = make_service(tmp_path)
    service.start_roulette(1001, 1, "first", START)
    update_active_state(
        db,
        {
            "shots": 0,
            "bullet_at": 6,
            "roulette_burst_rifle_pending": True,
            "roulette_burst_rifle_bullet_count": 1,
            "roulette_burst_rifle_bullet_slots": [4],
        },
    )

    outcome = service.fire(1001, 1, "first", START + timedelta(seconds=1))

    assert outcome.kind == "roulette_burst_rifle_safe"
    assert outcome.mute_user_ids == ()
    assert stats_row(db, 1001, 1) is None


def test_roulette_area_explosion_eliminates_and_mutes_every_participant(tmp_path):
    db, service = make_service(tmp_path)
    service.start_roulette(1001, 1, "first", START)
    update_active_state(db, {"shots": 0, "bullet_at": 2, "shot_log": []})
    assert service.fire(1001, 1, "first", START + timedelta(seconds=1)).kind == "roulette_safe"
    state = active_state(db)
    state["roulette_area_explosion_pending"] = True
    update_active_state(db, state)

    outcome = service.fire(1001, 2, "second", START + timedelta(seconds=2))

    assert outcome.kind == "roulette_area_explosion_triggered"
    assert outcome.ended
    assert outcome.mute_user_ids == (1, 2)
    assert outcome.half_mute_user_ids == (1,)
    assert stats_row(db, 1001, 1)["roulette_deaths"] == 1
    assert stats_row(db, 1001, 2)["roulette_deaths"] == 1


def test_roulette_aim_drift_redirects_only_a_real_bullet(tmp_path):
    db, service = make_service(tmp_path)
    service.start_roulette(1001, 1, "first", START)
    update_active_state(db, {"shots": 0, "bullet_at": 6, "shot_log": []})
    service.fire(1001, 1, "first", START + timedelta(seconds=1))
    update_active_state(
        db,
        {"shots": 1, "bullet_at": 2, "roulette_aim_drift_pending": True},
    )

    outcome = service.fire(1001, 2, "second", START + timedelta(seconds=2))

    assert outcome.kind == "roulette_hit"
    assert outcome.ended
    assert outcome.mute_user_ids == (1,)
    assert stats_row(db, 1001, 1)["roulette_deaths"] == 1
    assert stats_row(db, 1001, 2)["roulette_deaths"] == 0


def test_roulette_barrel_burst_cancels_the_final_hit_without_a_mute(tmp_path):
    db, service = make_service(tmp_path)
    service.start_roulette(1001, 1, "first", START)
    update_active_state(
        db,
        {"shots": 0, "bullet_at": 1, "roulette_barrel_burst_pending": True},
    )

    outcome = service.fire(1001, 1, "first", START + timedelta(seconds=1))

    assert outcome.kind == "roulette_barrel_burst_triggered"
    assert outcome.ended
    assert outcome.mute_user_ids == ()
    assert stats_row(db, 1001, 1)["roulette_deaths"] == 0


def test_roulette_burst_weapons_are_unavailable_until_the_fourth_shot(tmp_path):
    _db, service = make_service(tmp_path)

    early_state = {"shots": 2, "bullet_at": 6, "random_event_type": "roulette_burst_revolver"}
    assert service._select_random_event_type(ROULETTE, early_state, []) != "roulette_burst_revolver"
    early_state["random_event_type"] = "roulette_burst_rifle"
    assert service._select_random_event_type(ROULETTE, early_state, []) != "roulette_burst_rifle"

    eligible_state = {"shots": 3, "bullet_at": 6, "random_event_type": "roulette_burst_revolver"}
    assert service._select_random_event_type(ROULETTE, eligible_state, []) == "roulette_burst_revolver"
    eligible_state["random_event_type"] = "roulette_burst_rifle"
    assert service._select_random_event_type(ROULETTE, eligible_state, []) == "roulette_burst_rifle"


def test_roulette_barrel_burst_has_a_five_percent_selection_chance(tmp_path, monkeypatch):
    _db, service = make_service(tmp_path)
    state = {"shots": 3, "bullet_at": 6}

    monkeypatch.setattr("bot.services.mini_games.secrets.randbelow", lambda _upper: 4)
    assert service._select_random_event_type(ROULETTE, state, []) == "roulette_barrel_burst"

    monkeypatch.setattr(
        "bot.services.mini_games.secrets.randbelow",
        lambda _upper: ROULETTE_BARREL_BURST_CHANCE_PERCENT,
    )
    assert service._select_random_event_type(ROULETTE, state, []) != "roulette_barrel_burst"



def test_global_ranking_aggregates_internal_user_id_and_exposes_only_group_markers(tmp_path):
    db, service = make_service(tmp_path)
    with db.connect() as connection:
        connection.execute(
            """INSERT INTO mini_game_stats
               (group_id,user_id,nickname,roulette_deaths,roulette_games,updated_at)
               VALUES (1001,7,'旧昵称',2,5,?)""",
            (START.isoformat(),),
        )
        connection.execute(
            """INSERT INTO mini_game_stats
               (group_id,user_id,nickname,roulette_deaths,roulette_games,updated_at)
               VALUES (1002,7,'新昵称',3,4,?)""",
            ((START + timedelta(seconds=1)).isoformat(),),
        )
    board = service.ranking(ROULETTE)
    row = board["sections"][0]["rows"][0]
    assert row["nickname"] == "新昵称"
    assert row["value"] == 5
    assert row["games"] == 9
    assert row["group_ids"] == (1001, 1002)
    assert [item["group_name"] for item in board["groups"]] == ["测试群一", "测试群二"]


def test_global_ranking_can_hide_group_details_without_changing_cumulative_scores(tmp_path):
    db, service = make_service(tmp_path)
    with db.connect() as connection:
        connection.execute(
            """INSERT INTO mini_game_stats
               (group_id,user_id,nickname,roulette_deaths,roulette_games,updated_at)
               VALUES (1001,7,'群一玩家',2,3,?)""",
            (START.isoformat(),),
        )
        connection.execute(
            """INSERT INTO mini_game_stats
               (group_id,user_id,nickname,roulette_deaths,roulette_games,updated_at)
               VALUES (1002,7,'群二玩家',3,4,?)""",
            ((START + timedelta(seconds=1)).isoformat(),),
        )

    board = service.ranking(ROULETTE, include_group_details=False)
    row = board["sections"][0]["rows"][0]

    assert board["show_group_details"] is False
    assert board["groups"] == []
    assert row["nickname"] == "群二玩家"
    assert (row["value"], row["games"], row["group_ids"]) == (5, 7, ())


def test_global_ranking_hides_disabled_group_records_without_deleting_them(tmp_path):
    db, service = make_service(tmp_path)
    with db.connect() as connection:
        connection.execute(
            """INSERT INTO mini_game_stats
               (group_id,user_id,nickname,roulette_deaths,roulette_games,updated_at)
               VALUES (1001,7,'群一玩家',2,3,?)""",
            (START.isoformat(),),
        )
        connection.execute(
            """INSERT INTO mini_game_stats
               (group_id,user_id,nickname,roulette_deaths,roulette_games,updated_at)
               VALUES (1002,8,'群二玩家',4,5,?)""",
            (START.isoformat(),),
        )

    visible = service.ranking(ROULETTE, visible_group_ids=(1001,))
    rows = visible["sections"][0]["rows"]
    assert [(row["user_id"], row["value"]) for row in rows] == [(7, 2)]
    assert [row["group_id"] for row in visible["groups"]] == [1001]

    hidden = service.ranking(ROULETTE, visible_group_ids=())
    assert hidden["sections"][0]["rows"] == []
    assert hidden["groups"] == []

    restored = service.ranking(ROULETTE, visible_group_ids=(1001, 1002))
    assert [(row["user_id"], row["value"]) for row in restored["sections"][0]["rows"]] == [
        (8, 4),
        (7, 2),
    ]


def test_clearing_one_groups_game_records_preserves_other_groups(tmp_path):
    db, service = make_service(tmp_path)
    service.start_roulette(1001, 1, "群一发起人", START)
    service.fire(1001, 2, "群一玩家", START + timedelta(seconds=1))
    service.expire_due(START + timedelta(seconds=121))
    with db.connect() as connection:
        connection.execute(
            """INSERT INTO mini_game_stats
               (group_id,user_id,nickname,roulette_deaths,roulette_games,updated_at)
               VALUES (1002,9,'群二玩家',1,2,?)""",
            (START.isoformat(),),
        )

    deleted = service.clear_group_records(1001)

    assert deleted["sessions"] == 1
    assert deleted["participants"] == 1
    assert deleted["stats"] == 1
    with db.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM mini_game_sessions WHERE group_id=1001"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM mini_game_participants"
        ).fetchone()[0] == 0
        retained = connection.execute(
            "SELECT roulette_deaths,roulette_games FROM mini_game_stats WHERE group_id=1002 AND user_id=9"
        ).fetchone()
    assert tuple(retained) == (1, 2)


def test_menu_and_global_ranking_render_as_local_png_without_ids(tmp_path):
    _db, service = make_service(tmp_path)
    renderer = MiniGameReportRenderer(tmp_path)
    menu = renderer.render_menu()
    details = renderer.render_game_details()
    board = renderer.render_ranking(service.ranking(DICE), {}, {})
    assert len(details) == 5
    for path in (menu, *details, board):
        assert path.exists()
        with Image.open(path) as image:
            assert image.width == renderer.WIDTH
            assert image.getbbox() is not None
    with Image.open(board) as image:
        assert image.mode == "RGBA"
        assert image.getpixel((5, image.height // 4))[:3] != image.getpixel(
            (5, image.height * 3 // 4)
        )[:3]
