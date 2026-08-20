from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3

import pytest

from PIL import Image

from bot.db import Database
from bot.services.mini_game_reports import MiniGameReportRenderer
from bot.services.today_wife import TodayWifeService
from bot.services.today_wife_content import THEME_PACKS, mechanic_for, pack_for, script_for
from bot.services.today_wife_delivery import TodayWifeConclusionDelivery
from bot.services.today_wife_game import (
    GROUP_DETAIL_RETENTION_DAYS,
    INITIAL_AFFECTION_RANGE,
    MAX_INTERACTIONS,
    MAX_RESCUE_SUCCESS_RATE,
    NEGATIVE_AFFECTION_RESCUE_BONUS,
    TodayWifeGameService,
)
from bot.services.today_wife_narrative import (
    compose_narrative,
    load_narrative_resources,
    narrative_capacity,
    narrative_resource_report,
)
import bot.services.today_wife_delivery as delivery_module


NOW = datetime.fromisoformat("2026-08-12T21:00:00+08:00")
LATE = datetime.fromisoformat("2026-08-12T23:51:00+08:00")
GROUP_ID = 1001
MEMBERS = (
    {"user_id": 1, "nickname": "小明"},
    {"user_id": 2, "nickname": "小夏"},
    {"user_id": 3, "nickname": "小白"},
    {"user_id": 4, "nickname": "小雨"},
    {"user_id": 5, "nickname": "小冬"},
)


def services(tmp_path: Path) -> tuple[TodayWifeService, TodayWifeGameService]:
    database = Database(tmp_path / "game.db")
    database.configure_groups((GROUP_ID,))
    return TodayWifeService(database), TodayWifeGameService(database)


def draw(wife: TodayWifeService, actor_id: int, name: str, target_id: int):
    result = wife.draw(GROUP_ID, actor_id, name, MEMBERS, NOW, selected_target_id=target_id)
    assert result.kind == "drawn" and result.record is not None
    return result.record


def interaction_snapshot(game: TodayWifeGameService) -> tuple[int, int, int]:
    """Capture the durable counters that an action prompt must leave untouched."""

    day = NOW.date().isoformat()
    with game.database.connect() as connection:
        event_count = connection.execute(
            "SELECT COUNT(*) AS value FROM today_wife_interaction_events WHERE group_id=? AND day=?",
            (GROUP_ID, day),
        ).fetchone()["value"]
        interaction_count = connection.execute(
            "SELECT interaction_count FROM today_wife_day_states WHERE group_id=? AND day=?",
            (GROUP_ID, day),
        ).fetchone()["interaction_count"]
        affection = connection.execute(
            """SELECT affection FROM today_wife_relation_states
               WHERE group_id=? AND day=? AND actor_id=? AND draw_index=?""",
            (GROUP_ID, day, 1, 1),
        ).fetchone()["affection"]
    return int(event_count), int(interaction_count), int(affection)


def test_content_packs_have_nine_themes_and_five_scripts_each() -> None:
    assert len(THEME_PACKS) == 9
    assert {pack.style for pack in THEME_PACKS} == {"现实日常", "轻奇幻", "强幻想"}
    for pack in THEME_PACKS:
        assert len(pack.scripts) >= 5
        assert len(pack.mechanisms) >= 3
        assert len(pack.direct_positive) >= 2
        assert len(pack.assist_positive) >= 2
        assert all(mechanic_for(name).name == name for name in pack.mechanisms)


def test_composable_narrative_library_has_large_non_repeating_event_space() -> None:
    capacity = narrative_capacity()
    resource = narrative_resource_report()
    assert min(capacity.values()) >= 1_000_000
    assert len(resource["layers"]) >= 4
    assert all(size >= 8 for size in resource["layers"])
    assert len(resource["paths"]) >= 2
    samples = {
        compose_narrative(
            "轻奇幻",
            "assist_positive",
            f"review:{index}",
            {
                "actor": "小明", "target": "小夏", "left": "小白", "right": "小雨",
                "prop": "会发光的纸条", "mechanism": "匿名回信",
            },
        )
        for index in range(512)
    }
    # A uniform selection from 3,072 combinations naturally has some birthday
    # collisions at 512 probes; it must still remain well above 90% unique.
    assert len(samples) >= 460
    for pack in THEME_PACKS:
        active_capacity = narrative_capacity(theme_id=pack.id)
        assert min(value for key, value in active_capacity.items() if key.startswith(f"{pack.style}:")) >= 1_000_000


def test_narrative_packs_load_in_order_and_reject_cross_pack_duplicates(tmp_path: Path) -> None:
    base = {"schema_version": 1, "styles": {}, "events": {}, "layers": {"camera": ["镜头落在{actor}身上。"]}}
    extension = {"schema_version": 1, "styles": {}, "events": {"same_scene": ["{actor}和{target}留下新镜头。"]}, "layers": {"camera": ["镜头也落在{target}身上。"]}}
    (tmp_path / "01_base.json").write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "02_extension.json").write_text(json.dumps(extension, ensure_ascii=False), encoding="utf-8")
    _styles, events, layers, paths = load_narrative_resources(tmp_path)
    assert [path.name for path in paths] == ["01_base.json", "02_extension.json"]
    assert len(events["same_scene"]) >= 7
    assert layers == (("镜头落在{actor}身上。", "镜头也落在{target}身上。"),)

    duplicate = {"schema_version": 1, "styles": {}, "events": {}, "layers": {"camera": ["镜头落在{actor}身上。"]}}
    (tmp_path / "03_duplicate.json").write_text(json.dumps(duplicate, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="03_duplicate.json.*repeats"):
        load_narrative_resources(tmp_path)


def test_narrative_pack_scope_only_applies_to_its_matching_theme(tmp_path: Path) -> None:
    scoped = {
        "schema_version": 1,
        "applies_to": {"theme_ids": ["festival"]},
        "styles": {},
        "events": {},
        "layers": {"festival": ["节日镜头看向{actor}。"]},
    }
    (tmp_path / "festival.json").write_text(json.dumps(scoped, ensure_ascii=False), encoding="utf-8")
    _styles, _events, active_layers, active_paths = load_narrative_resources(tmp_path, theme_id="festival")
    _styles, _events, inactive_layers, inactive_paths = load_narrative_resources(tmp_path, theme_id="ordinary")
    assert active_layers == (("节日镜头看向{actor}。",),)
    assert [path.name for path in active_paths] == ["festival.json"]
    assert inactive_layers == ()
    assert inactive_paths == ()


def test_theme_selection_chooses_style_before_applying_recency_weights(tmp_path: Path) -> None:
    _wife, game = services(tmp_path)
    class ControlledRandom:
        def choice(self, values):
            values = tuple(values)
            if all(isinstance(value, str) for value in values) and set(values) == {"现实日常", "轻奇幻", "强幻想"}:
                return "强幻想"
            return values[0]
    game.rng = ControlledRandom()  # type: ignore[assignment]
    with game.database.connect() as connection:
        theme_id = game._global_theme(connection, NOW.date().isoformat())
    assert next(pack for pack in THEME_PACKS if pack.id == theme_id).style == "强幻想"


def test_only_story_participants_can_interact_and_one_person_can_continue(tmp_path: Path) -> None:
    wife, game = services(tmp_path)
    assert game.interaction(GROUP_ID, 1, "小明", now=NOW).kind == "not_joined"
    draw(wife, 1, "小明", 2)
    first = game.interaction(GROUP_ID, 1, "小明", now=NOW, intent="靠近")
    assert first.kind == "played"
    second = game.interaction(GROUP_ID, 1, "小明", now=NOW, intent="倾听")
    assert second.kind == "played" and second.event is not None
    assert second.event["actor_remaining"] == MAX_INTERACTIONS - 2


def test_bare_interaction_returns_an_actionable_prompt_without_persisting(tmp_path: Path) -> None:
    wife, game = services(tmp_path)
    draw(wife, 1, "小明", 2)
    before = interaction_snapshot(game)

    result = game.interaction(
        GROUP_ID,
        1,
        "小明",
        now=NOW,
        intent="auto",
        source_message_id="auto-prompt",
    )

    assert result.kind == "prompt"
    assert result.event is not None
    assert {"靠近", "倾听"}.issubset(set(result.event["available_actions"]))
    assert interaction_snapshot(game) == before
    assert game.personal_archive(GROUP_ID, 1, NOW)["remaining"] == MAX_INTERACTIONS


@pytest.mark.parametrize("intent", ("修复", "回应", "助攻"))
def test_unavailable_relationship_actions_return_prompts_without_consuming_quota(
    tmp_path: Path,
    intent: str,
) -> None:
    wife, game = services(tmp_path)
    draw(wife, 1, "小明", 2)
    before = interaction_snapshot(game)

    result = game.interaction(
        GROUP_ID,
        1,
        "小明",
        now=NOW,
        intent=intent,
        source_message_id=f"unavailable-{intent}",
    )

    assert result.kind == "prompt"
    assert result.event is not None
    assert result.event["available_actions"]
    assert interaction_snapshot(game) == before
    assert game.personal_archive(GROUP_ID, 1, NOW)["remaining"] == MAX_INTERACTIONS


def test_approach_and_listen_persist_distinct_choice_semantics(tmp_path: Path) -> None:
    approach_wife, approach_game = services(tmp_path / "approach")
    draw(approach_wife, 1, "小明", 2)
    approach = approach_game.interaction(
        GROUP_ID, 1, "小明", now=NOW, intent="靠近", source_message_id="approach-choice"
    )

    listen_wife, listen_game = services(tmp_path / "listen")
    draw(listen_wife, 1, "小明", 2)
    listen = listen_game.interaction(
        GROUP_ID, 1, "小明", now=NOW, intent="倾听", source_message_id="listen-choice"
    )

    assert approach.kind == listen.kind == "played"
    assert approach.event is not None and listen.event is not None
    approach_plan = approach.event["narrative_plan"]
    listen_plan = listen.event["narrative_plan"]
    assert approach_plan["intent"] == "靠近"
    assert listen_plan["intent"] == "倾听"
    assert approach_plan["choice_semantics"] == "approach"
    assert listen_plan["choice_semantics"] == "listen"
    assert approach_plan["choice_semantics"] != listen_plan["choice_semantics"]


def test_named_choices_persist_distinct_branch_transition_route_and_ending(tmp_path: Path) -> None:
    class PositiveRandom:
        def choice(self, values):
            return tuple(values)[0]

        def randrange(self, _upper: int) -> int:
            return 99

        def randint(self, _lower: int, upper: int) -> int:
            return upper

        def random(self) -> float:
            return 1.0

    approach_wife, approach_game = services(tmp_path / "approach-branch")
    draw(approach_wife, 1, "小明", 2)
    approach_game.rng = PositiveRandom()  # type: ignore[assignment]
    approach = approach_game.interaction(
        GROUP_ID, 1, "小明", now=NOW, intent="靠近", source_message_id="approach-branch"
    )

    listen_wife, listen_game = services(tmp_path / "listen-branch")
    draw(listen_wife, 1, "小明", 2)
    listen_game.rng = PositiveRandom()  # type: ignore[assignment]
    listen = listen_game.interaction(
        GROUP_ID, 1, "小明", now=NOW, intent="倾听", source_message_id="listen-branch"
    )

    assert approach.event is not None and listen.event is not None
    approach_plan = approach.event["narrative_plan"]
    listen_plan = listen.event["narrative_plan"]
    assert approach_plan["version"] == listen_plan["version"] == 3
    assert approach_plan["choice_id"] != listen_plan["choice_id"]
    assert approach_plan["outcome_id"] != listen_plan["outcome_id"]
    assert approach_plan["next_beat_id"] != listen_plan["next_beat_id"]
    assert approach_plan["route_delta"] != listen_plan["route_delta"]
    assert approach_plan["ending_id"] != listen_plan["ending_id"]

    approach_arc = approach_game.personal_archive(GROUP_ID, 1, NOW)["own"][0]["relation"]["narrative"]
    listen_arc = listen_game.personal_archive(GROUP_ID, 1, NOW)["own"][0]["relation"]["narrative"]
    assert approach_arc["branch"]["last_choice_id"] == approach_plan["choice_id"]
    assert listen_arc["branch"]["last_choice_id"] == listen_plan["choice_id"]
    assert approach_arc["branch"]["history"][-1]["event_id"] == approach.event["event_id"]
    assert listen_arc["branch"]["history"][-1]["event_id"] == listen.event["event_id"]


def test_directed_draw_keeps_its_origin_after_branch_interaction(tmp_path: Path) -> None:
    wife, game = services(tmp_path)
    forced = wife.force_draw(GROUP_ID, 1, "小明", MEMBERS, NOW, target_id=2)
    assert forced.kind == "drawn" and forced.record is not None

    outcome = game.interaction(
        GROUP_ID, 1, "小明", now=NOW, intent="靠近", source_message_id="directed-branch"
    )

    assert outcome.kind == "played" and outcome.event is not None
    relation = game.personal_archive(GROUP_ID, 1, NOW)["own"][0]["relation"]
    assert relation["narrative"]["relationship_origin"] == "directed"
    assert relation["narrative"]["version"] == 3
    assert relation["narrative"]["branch"]["last_choice_id"] == outcome.event["narrative_plan"]["choice_id"]


@pytest.mark.parametrize("mentioned_id, expected_kind", [(1, "self_target"), (0, "invalid_target"), ("bad-id", "invalid_target")])
def test_invalid_or_self_interaction_targets_do_not_consume_quota(
    tmp_path: Path, mentioned_id: object, expected_kind: str
) -> None:
    wife, game = services(tmp_path)
    draw(wife, 1, "小明", 2)
    before = interaction_snapshot(game)

    outcome = game.interaction(
        GROUP_ID, 1, "小明", mentioned_id=mentioned_id, now=NOW, intent="靠近"
    )

    assert outcome.kind == expected_kind
    assert interaction_snapshot(game) == before


def test_action_prompt_keeps_target_bound_response_and_assist_commands(tmp_path: Path) -> None:
    wife, game = services(tmp_path)
    draw(wife, 1, "小明", 2)
    draw(wife, 3, "小白", 2)

    response_prompt = game.interaction(GROUP_ID, 2, "小夏", now=NOW, intent="auto")
    assert response_prompt.kind == "prompt" and response_prompt.event is not None
    response_options = [
        option for option in response_prompt.event["action_options"] if option["intent"] == "回应"
    ]
    assert len(response_options) == 2
    assert all(option["requires_mention"] for option in response_options)
    assert {option["mentioned_id"] for option in response_options} == {1, 3}
    assert all("+ @" in option["display_command"] for option in response_options)

    draw(wife, 5, "小冬", 1)
    assist_prompt = game.interaction(GROUP_ID, 5, "小冬", now=NOW, intent="auto")
    assert assist_prompt.kind == "prompt" and assist_prompt.event is not None
    assist_options = [
        option for option in assist_prompt.event["action_options"] if option["intent"] == "助攻"
    ]
    assert assist_options
    assert all(option["requires_mention"] and "+ @" in option["display_command"] for option in assist_options)

    before = interaction_snapshot(game)
    unsafe = game.interaction(GROUP_ID, 5, "小冬", now=NOW, intent="助攻")
    assert unsafe.kind == "prompt"
    assert interaction_snapshot(game) == before


def test_script_scoped_story_uses_only_its_own_prop_and_choice_templates(tmp_path: Path) -> None:
    wife, game = services(tmp_path)
    record = draw(wife, 1, "小明", 2)
    state = game.day_state(GROUP_ID, NOW)
    script = script_for(str(state["script_id"]), pack_for(str(state["theme_id"])).id)

    outcome = game.interaction(
        GROUP_ID, 1, "小明", now=NOW, intent="靠近", source_message_id="script-scope"
    )

    assert outcome.kind == "played" and outcome.event is not None
    plan = outcome.event["narrative_plan"]
    assert script.prop in plan["text"]
    assert script.title in game.draw_reveal(record, game.personal_archive(GROUP_ID, 1, NOW)["own"][0]["relation"])["relationship_label"]
    assert all(script.prop in str(fact) or "故事开始的位置" not in str(fact) for fact in game.personal_archive(GROUP_ID, 1, NOW)["own"][0]["relation"]["narrative"]["facts"])


def test_every_secondary_effect_has_a_causal_story_beat(tmp_path: Path) -> None:
    wife, game = services(tmp_path)
    draw(wife, 1, "小明", 2)
    draw(wife, 3, "小白", 2)

    outcome = game.interaction(
        GROUP_ID, 2, "小夏", now=NOW, intent="回应", source_message_id="secondary-beats"
    )

    assert outcome.kind == "played" and outcome.event is not None
    effects = outcome.event["effects"]
    beats = outcome.event["narrative_plan"]["secondary_beats"]
    assert len(beats) == max(0, len(effects) - 1)
    for effect, beat in zip(effects[1:], beats):
        assert effect["left"] in beat["relation"]
        assert effect["right"] in beat["relation"]
        assert f"{int(effect['delta']):+d}" in beat["text"]


def test_second_direct_action_explicitly_carries_forward_the_previous_hook(tmp_path: Path) -> None:
    wife, game = services(tmp_path)
    draw(wife, 1, "小明", 2)
    first = game.interaction(
        GROUP_ID, 1, "小明", now=NOW, intent="靠近", source_message_id="direct-first"
    )
    assert first.kind == "played" and first.event is not None
    first_hook = game.personal_archive(GROUP_ID, 1, NOW)["own"][0]["relation"]["narrative"]["hook"]
    hook_summary = str(first_hook["summary"])

    second = game.interaction(
        GROUP_ID, 1, "小明", now=NOW, intent="靠近", source_message_id="direct-second"
    )

    assert second.kind == "played" and second.event is not None
    plan = second.event["narrative_plan"]
    assert plan["callback_event_id"] == first.event["event_id"]
    assert hook_summary in plan["text"]


def test_group_route_scores_drive_day_state_conclusion_and_group_caption(tmp_path: Path) -> None:
    wife, game = services(tmp_path)
    draw(wife, 1, "小明", 2)

    class PositiveRandom:
        def choice(self, values):
            return tuple(values)[0]

        def randrange(self, _upper: int) -> int:
            return 99

        def randint(self, _lower: int, upper: int) -> int:
            return upper

        def random(self) -> float:
            return 1.0

    game.rng = PositiveRandom()  # type: ignore[assignment]
    result = game.interaction(
        GROUP_ID, 1, "小明", now=NOW, intent="靠近", source_message_id="route-score"
    )
    assert result.kind == "played" and result.event is not None
    state = game.day_state(GROUP_ID, NOW)
    route_scores = state["state"]["route_scores"]
    assert route_scores
    assert all(isinstance(score, (int, float)) for score in route_scores.values())
    assert any(score != 0 for score in route_scores.values())
    dominant_route = max(route_scores, key=route_scores.get)
    assert state["route_key"] == dominant_route

    conclusion = game.lock_and_conclude(GROUP_ID, LATE)["conclusion"]
    story = game.group_story(GROUP_ID, LATE)
    assert conclusion["route_key"] == dominant_route
    assert conclusion["route_label"]
    assert story["day_state"]["route_key"] == dominant_route
    assert conclusion["route_label"] in story["spotlight"]


def test_source_message_retry_replays_original_event_without_mutating_state(tmp_path: Path) -> None:
    wife, game = services(tmp_path)
    draw(wife, 1, "小明", 2)

    first = game.interaction(
        GROUP_ID,
        1,
        "小明",
        now=NOW,
        intent="靠近",
        source_message_id="qq-message-100",
    )
    assert first.kind == "played" and first.event is not None
    with game.database.connect() as connection:
        event_count = connection.execute(
            "SELECT COUNT(*) AS value FROM today_wife_interaction_events WHERE group_id=? AND day=?",
            (GROUP_ID, NOW.date().isoformat()),
        ).fetchone()["value"]
        affection = connection.execute(
            """SELECT affection FROM today_wife_relation_states
               WHERE group_id=? AND day=? AND actor_id=? AND draw_index=?""",
            (GROUP_ID, NOW.date().isoformat(), 1, 1),
        ).fetchone()["affection"]

    # QQ can redeliver a command after the late lock or with a stale event
    # envelope.  The persisted event, not retry inputs, remains authoritative.
    replay = game.interaction(
        GROUP_ID,
        5,
        "小冬",
        now=LATE,
        intent="助攻",
        source_message_id="qq-message-100",
    )
    assert replay.kind == "played" and replay.event is not None
    assert replay.event["event_id"] == first.event["event_id"]
    assert replay.event["actor_id"] == 1
    assert replay.event["actor_remaining"] == first.event["actor_remaining"]
    with game.database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) AS value FROM today_wife_interaction_events WHERE group_id=? AND day=?",
            (GROUP_ID, NOW.date().isoformat()),
        ).fetchone()["value"] == event_count
        assert connection.execute(
            """SELECT affection FROM today_wife_relation_states
               WHERE group_id=? AND day=? AND actor_id=? AND draw_index=?""",
            (GROUP_ID, NOW.date().isoformat(), 1, 1),
        ).fetchone()["affection"] == affection


def test_explicit_assist_target_without_another_relation_returns_a_nonpersisting_prompt(tmp_path: Path) -> None:
    wife, game = services(tmp_path)
    draw(wife, 1, "小明", 2)
    draw(wife, 3, "小白", 4)
    before = interaction_snapshot(game)

    result = game.interaction(
        GROUP_ID,
        1,
        "小明",
        2,
        NOW,
        intent="助攻",
        source_message_id="unavailable-assist",
    )

    assert result.kind == "prompt" and result.event is not None
    assert result.event["mentioned_id"] == 2
    assert result.event["available_actions"]
    assert interaction_snapshot(game) == before
    assert game.personal_archive(GROUP_ID, 1, NOW)["remaining"] == MAX_INTERACTIONS


def test_interaction_persists_the_actual_day_participant_count(tmp_path: Path) -> None:
    wife, game = services(tmp_path)
    draw(wife, 1, "小明", 2)
    draw(wife, 3, "小白", 4)
    draw(wife, 5, "小冬", 1)

    assert game.interaction(GROUP_ID, 1, "小明", now=NOW, intent="靠近", source_message_id="participants-1").kind == "played"

    assert game.day_state(GROUP_ID, NOW)["state"]["participant_count"] == 5


def test_response_is_publicly_split_across_everyone_who_drew_the_actor(tmp_path: Path) -> None:
    wife, game = services(tmp_path)
    draw(wife, 1, "小明", 2)
    draw(wife, 3, "小白", 2)
    result = game.interaction(GROUP_ID, 2, "小夏", now=NOW, intent="回应")
    assert result.kind == "played" and result.event is not None
    assert result.event["kind"] == "response"
    assert {effect["actor_id"] for effect in result.event["effects"]} == {1, 3}
    # The selected theme may apply a small negative mechanic bias to the
    # otherwise 18-30 main-response range.
    assert any(effect["delta"] >= 16 for effect in result.event["effects"])


def test_third_party_event_records_exactly_which_relationship_changed(tmp_path: Path) -> None:
    wife, game = services(tmp_path)
    draw(wife, 1, "小明", 2)
    draw(wife, 3, "小白", 4)
    draw(wife, 5, "小冬", 1)
    result = game.interaction(GROUP_ID, 5, "小冬", mentioned_id=2, now=NOW, intent="助攻")
    assert result.kind == "played" and result.event is not None
    assert result.event["kind"] in {"assist", "interference", "direct", "response"}
    assert all({"left", "right", "delta"} <= set(effect) for effect in result.event["effects"])
    assert all(-30 <= int(effect["delta"]) <= 30 for effect in result.event["effects"])


def test_divorce_freezes_memory_value_and_redraw_gets_a_new_initial_affection(tmp_path: Path) -> None:
    wife, game = services(tmp_path)
    first = draw(wife, 1, "小明", 2)
    with wife.database.connect() as connection:
        connection.execute(
            "UPDATE today_wife_relation_states SET affection=47 WHERE group_id=? AND day=? AND actor_id=? AND draw_index=?",
            (GROUP_ID, NOW.date().isoformat(), 1, 1),
        )
    divorced = wife.divorce(GROUP_ID, 1, NOW)
    assert divorced.kind == "divorced"
    archive = game.personal_archive(GROUP_ID, 1, NOW)
    assert archive["own"][0]["relation"]["frozen_affection"] == 47
    redrawn = wife.draw(GROUP_ID, 1, "小明", MEMBERS, NOW, selected_target_id=3)
    assert redrawn.kind == "drawn" and redrawn.record is not None
    archive = game.personal_archive(GROUP_ID, 1, NOW)
    current = next(item for item in archive["own"] if item["draw_index"] == 2)
    assert INITIAL_AFFECTION_RANGE[0] <= current["relation"]["affection"] <= INITIAL_AFFECTION_RANGE[1]
    assert current["relation"]["minimum_affection"] == current["relation"]["affection"]
    assert first["target_id"] == 2


def test_new_relationship_starts_with_random_initial_affection(tmp_path: Path) -> None:
    wife, game = services(tmp_path)
    draw(wife, 1, "小明", 2)

    relation = game.personal_archive(GROUP_ID, 1, NOW)["own"][0]["relation"]

    assert INITIAL_AFFECTION_RANGE[0] <= relation["affection"] <= INITIAL_AFFECTION_RANGE[1]
    assert relation["minimum_affection"] == relation["affection"]


def test_positive_and_negative_event_ranges_are_symmetric(tmp_path: Path) -> None:
    _wife, game = services(tmp_path)

    class ControlledRandom:
        def __init__(self, roll: int) -> None:
            self.roll = roll

        def randrange(self, _upper: int) -> int:
            return self.roll

        def randint(self, lower: int, upper: int) -> int:
            return upper

    game.rng = ControlledRandom(0)  # type: ignore[assignment]
    assert game._swing("direct") == -30
    game.rng = ControlledRandom(99)  # type: ignore[assignment]
    assert game._swing("direct") == 30


def test_negative_affection_is_preserved_after_a_negative_event(tmp_path: Path) -> None:
    wife, game = services(tmp_path)
    record = draw(wife, 1, "小明", 2)
    with game.database.connect() as connection:
        game._apply_effect(
            connection,
            GROUP_ID,
            NOW.date().isoformat(),
            game._effect(record, -40, "误会加深", 1),
            1,
            NOW.isoformat(timespec="seconds"),
        )

    relation = game.personal_archive(GROUP_ID, 1, NOW)["own"][0]["relation"]
    assert relation["affection"] < 0
    assert relation["minimum_affection"] == relation["affection"]


def test_negative_affection_only_boosts_the_owner_rescue_success_rate(tmp_path: Path) -> None:
    _wife, game = services(tmp_path)

    class ControlledRandom:
        def randrange(self, upper: int) -> int:
            return min(80, upper - 1)

        def randint(self, _lower: int, upper: int) -> int:
            return upper

    game.rng = ControlledRandom()  # type: ignore[assignment]
    pack = next(
        pack
        for pack in THEME_PACKS
        if mechanic_for(pack.mechanisms[0]).chain_success_rate < 80
    )
    script = script_for(pack.scripts[0].id, pack.id)
    relation = {"actor_id": 1, "target_id": 2, "draw_index": 1, "actor_nickname": "小明", "target_nickname": "小夏"}
    base_rate = mechanic_for(pack.mechanisms[0]).chain_success_rate

    ordinary = game._chain_event(pack, script, pack.mechanisms[0], relation, 1, "小明", None, "", {}, {"affection": 0})
    rescue = game._chain_event(pack, script, pack.mechanisms[0], relation, 1, "小明", None, "", {}, {"affection": -1})

    assert game._rescue_success_rate(0, base_rate) == base_rate
    assert game._rescue_success_rate(-1, base_rate) == min(MAX_RESCUE_SUCCESS_RATE, base_rate + NEGATIVE_AFFECTION_RESCUE_BONUS)
    assert ordinary["effects"][0]["delta"] < 0
    assert rescue["effects"][0]["delta"] > 0


def test_repair_keeps_the_negative_event_as_its_callback_source(tmp_path: Path) -> None:
    wife, game = services(tmp_path)
    draw(wife, 1, "小明", 2)

    class FixedRandom:
        def choice(self, values):
            return tuple(values)[0]

        def randrange(self, _upper: int) -> int:
            return 0

        def randint(self, _lower: int, upper: int) -> int:
            return upper

        def random(self) -> float:
            return 1.0

    game.rng = FixedRandom()  # type: ignore[assignment]
    negative = game.interaction(
        GROUP_ID, 1, "小明", now=NOW, intent="靠近", source_message_id="negative-1"
    )
    assert negative.kind == "played" and negative.event is not None
    assert negative.event["effects"][0]["delta"] < 0

    repaired = game.interaction(
        GROUP_ID, 1, "小明", now=NOW, intent="修复", source_message_id="repair-1"
    )
    assert repaired.kind == "played" and repaired.event is not None
    assert repaired.event["kind"] == "chain"
    assert repaired.event["narrative_plan"]["callback_event_id"] == negative.event["event_id"]
    relation = game.personal_archive(GROUP_ID, 1, NOW)["own"][0]["relation"]
    assert relation["narrative"]["hook"]["source_event_id"] == negative.event["event_id"]


def test_draw_group_interaction_and_conclusion_share_the_daily_scene(tmp_path: Path) -> None:
    wife, game = services(tmp_path)
    record = draw(wife, 1, "小明", 2)
    state = game.day_state(GROUP_ID, NOW)
    relation = game.personal_archive(GROUP_ID, 1, NOW)["own"][0]["relation"]
    reveal = game.draw_reveal(record, relation, NOW)
    interaction = game.interaction(
        GROUP_ID, 1, "小明", now=NOW, intent="倾听", source_message_id="scene-1"
    )
    assert interaction.kind == "played" and interaction.event is not None
    group_story = game.group_story(GROUP_ID, NOW)
    conclusion = game.lock_and_conclude(GROUP_ID, LATE)["conclusion"]

    assert record["story_id"] == state["script_id"] == reveal["scene_id"]
    assert interaction.event["narrative_plan"]["scene_id"] == state["script_id"]
    assert group_story["day_state"]["script_id"] == state["script_id"]
    assert conclusion["title"] == state["script_title"]


def test_historical_draw_reveal_uses_its_original_day_scene(tmp_path: Path) -> None:
    wife, game = services(tmp_path)
    historical_now = NOW - timedelta(days=1)
    historical = wife.draw(GROUP_ID, 1, "小明", MEMBERS, historical_now, selected_target_id=2)
    assert historical.kind == "drawn" and historical.record is not None
    historical_state = game.day_state(GROUP_ID, historical_now)
    game.day_state(GROUP_ID, NOW)

    # Make a different current scene explicit so this regression cannot pass by
    # chance when two dates select the same theme and script.
    old_pack = THEME_PACKS[0]
    current_pack = THEME_PACKS[-1]
    with game.database.connect() as connection:
        connection.execute(
            "UPDATE today_wife_day_states SET theme_id=?,script_id=? WHERE group_id=? AND day=?",
            (old_pack.id, old_pack.scripts[0].id, GROUP_ID, historical_now.date().isoformat()),
        )
        connection.execute(
            "UPDATE today_wife_day_states SET theme_id=?,script_id=? WHERE group_id=? AND day=?",
            (current_pack.id, current_pack.scripts[0].id, GROUP_ID, NOW.date().isoformat()),
        )
    reveal = game.draw_reveal(historical.record, {}, NOW)

    assert historical_state["day"] == historical_now.date().isoformat()
    assert reveal["scene_id"] == old_pack.scripts[0].id
    assert reveal["scene_id"] != current_pack.scripts[0].id


def test_game_table_migration_adds_story_fields_and_resolves_legacy_retry_duplicates(tmp_path: Path) -> None:
    path = tmp_path / "legacy-game.db"
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE today_wife_relation_states (
                group_id INTEGER NOT NULL,
                day TEXT NOT NULL,
                actor_id INTEGER NOT NULL,
                draw_index INTEGER NOT NULL
            );
            CREATE TABLE today_wife_interaction_events (
                event_id INTEGER PRIMARY KEY,
                group_id INTEGER NOT NULL,
                day TEXT NOT NULL,
                actor_id INTEGER NOT NULL,
                source_message_id TEXT NOT NULL DEFAULT ''
            );
            INSERT INTO today_wife_interaction_events(event_id,group_id,day,actor_id,source_message_id)
            VALUES (10,1001,'2026-08-12',1,'legacy-message'),
                   (11,1001,'2026-08-12',1,'legacy-message');
            """
        )
        Database._migrate_today_wife_game_tables(connection)
        relation_columns = {row["name"] for row in connection.execute("PRAGMA table_info(today_wife_relation_states)")}
        event_columns = {row["name"] for row in connection.execute("PRAGMA table_info(today_wife_interaction_events)")}
        sources = [
            tuple(row)
            for row in connection.execute(
                "SELECT event_id,source_message_id FROM today_wife_interaction_events ORDER BY event_id"
            )
        ]

        assert "narrative_json" in relation_columns
        assert {"narrative_json", "intent", "source_message_id"}.issubset(event_columns)
        assert sources == [(10, "legacy-message"), (11, "")]
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO today_wife_interaction_events
                   (event_id,group_id,day,actor_id,source_message_id) VALUES (12,1001,'2026-08-12',1,'legacy-message')"""
            )


def test_divorced_relationship_remains_in_the_group_story_with_frozen_affection(tmp_path: Path) -> None:
    wife, game = services(tmp_path)
    draw(wife, 1, "小明", 2)
    with wife.database.connect() as connection:
        connection.execute(
            "UPDATE today_wife_relation_states SET affection=31 WHERE group_id=? AND day=? AND actor_id=? AND draw_index=?",
            (GROUP_ID, NOW.date().isoformat(), 1, 1),
        )
    assert wife.divorce(GROUP_ID, 1, NOW).kind == "divorced"
    story = game.group_story(GROUP_ID, NOW)
    record = story["records"][0]
    assert record["status"] == "divorced"
    assert record["relation"]["frozen_affection"] == 31


def test_daily_limit_lock_and_conclusion_are_idempotent(tmp_path: Path) -> None:
    wife, game = services(tmp_path)
    draw(wife, 1, "小明", 2)
    draw(wife, 3, "小白", 1)
    for index in range(MAX_INTERACTIONS * 2):
        actor, name = (1, "小明") if index % 2 == 0 else (3, "小白")
        assert game.interaction(GROUP_ID, actor, name, now=NOW, intent="靠近").kind == "played"
    assert game.interaction(GROUP_ID, 1, "小明", now=NOW, intent="靠近").kind == "exhausted"
    first = game.lock_and_conclude(GROUP_ID, LATE)
    second = game.lock_and_conclude(GROUP_ID, LATE)
    assert first["status"] == second["status"] == "locked"
    assert first["conclusion"] == second["conclusion"]
    assert game.interaction(GROUP_ID, 1, "小明", now=LATE).kind == "locked"


def test_game_cards_render_for_draw_event_archive_and_conclusion(tmp_path: Path) -> None:
    wife, game = services(tmp_path)
    record = draw(wife, 1, "小明", 2)
    draw(wife, 3, "小白", 2)
    event = game.interaction(GROUP_ID, 2, "小夏", now=NOW, intent="回应")
    assert event.event is not None
    archive = game.personal_archive(GROUP_ID, 2, NOW)
    conclusion = game.lock_and_conclude(GROUP_ID, LATE)["conclusion"]
    avatar = tmp_path / "avatar.png"
    Image.new("RGB", (180, 180), "#f3a2bd").save(avatar)
    avatars = {item["user_id"]: avatar for item in MEMBERS}
    renderer = MiniGameReportRenderer(tmp_path / "reports")
    draw_relation = game.personal_archive(GROUP_ID, 1, NOW)["own"][0]["relation"]
    paths = (
        renderer.render_today_wife_game_draw(record, wife.story_lines(record), wife.context_lines(record), avatars, game.day_state(GROUP_ID, NOW), draw_relation),
        renderer.render_today_wife_interaction(event.event, avatars),
        renderer.render_today_wife_archive(archive, avatars),
        renderer.render_today_wife_conclusion(conclusion),
        renderer.render_today_wife_history_archive(game.personal_history(GROUP_ID, 1), avatars),
        renderer.render_today_wife_group_archive(game.group_archive(GROUP_ID, NOW)),
    )
    for path in paths:
        with Image.open(path) as image:
            assert image.width >= 800
            assert image.height >= 260
            assert image.getbbox() is not None


def test_personal_history_is_permanent_while_group_detail_expires_to_summaries(tmp_path: Path) -> None:
    wife, game = services(tmp_path)
    old = NOW - timedelta(days=GROUP_DETAIL_RETENTION_DAYS + 2)
    outcome = wife.draw(GROUP_ID, 1, "小明", MEMBERS, old, selected_target_id=2)
    assert outcome.record is not None
    history = game.personal_history(GROUP_ID, 1)
    assert history["total"] == 1
    assert history["rows"][0]["day"] == old.date().isoformat()
    old_story = game.group_story(GROUP_ID, NOW, old.date())
    assert old_story["full_detail"] is False
    summaries = game.group_archive(GROUP_ID, NOW)["summaries"]
    assert summaries[0]["day"] == old.date().isoformat()
    assert summaries[0]["full_detail"] is False


def test_conclusion_delivery_retries_a_failed_send_within_the_late_window(monkeypatch, tmp_path: Path) -> None:
    wife, game = services(tmp_path)
    draw(wife, 1, "小明", 2)

    class Renderer:
        def render_today_wife_conclusion(self, conclusion):
            assert conclusion["title"]
            return tmp_path / "conclusion.png"

    attempts: list[int] = []

    async def send(_bot, _action, **params):
        attempts.append(int(params["group_id"]))
        if len(attempts) == 1:
            raise RuntimeError("temporary disconnect")

    monkeypatch.setattr(delivery_module, "local_image_segment", lambda path: f"image:{path}")
    monkeypatch.setattr(delivery_module, "call_qq_action", send)
    delivery = TodayWifeConclusionDelivery(game, Renderer(), lambda: (GROUP_ID,))

    first = asyncio.run(delivery.deliver_once(object(), LATE))
    second = asyncio.run(delivery.deliver_once(object(), LATE))

    assert first == {"status": "processed", "sent": 0, "failed": 1}
    assert second == {"status": "processed", "sent": 1, "failed": 0}
    assert attempts == [GROUP_ID, GROUP_ID]
    assert game.day_state(GROUP_ID, LATE)["status"] == "published"
