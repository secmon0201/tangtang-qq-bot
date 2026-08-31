"""Persistent interaction engine for the expandable 今日缘分 game."""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from bot.db import Database, utc_now
from bot.services.today_wife_content import THEME_PACKS, ThemePack, mechanic_for, pack_for, script_for
from bot.services.today_wife_story import INTENT_LABELS, StoryDirector


LOCK_TIME = time(23, 30)
MAX_INTERACTIONS = 5
GROUP_DETAIL_RETENTION_DAYS = 7
PERSONAL_HISTORY_PAGE_SIZE = 20
INITIAL_AFFECTION_RANGE = (15, 25)
NEGATIVE_AFFECTION_RESCUE_BONUS = 25
MAX_RESCUE_SUCCESS_RATE = 95
VALID_INTENTS = frozenset(INTENT_LABELS)


@dataclass(frozen=True, slots=True)
class InteractionOutcome:
    kind: str
    event: dict[str, Any] | None = None
    message: str = ""


class TodayWifeGameService:
    """Owns stateful interactions while keeping theme content declarative."""

    def __init__(self, database: Database, timezone_name: str = "Asia/Shanghai") -> None:
        self.database = database
        self.zone = ZoneInfo(timezone_name)
        self.rng = random.SystemRandom()

    def local_now(self, value: datetime | None = None) -> datetime:
        current = value or datetime.now(self.zone)
        return current.replace(tzinfo=self.zone) if current.tzinfo is None else current.astimezone(self.zone)

    def is_locked(self, group_id: int, now: datetime | None = None) -> bool:
        current = self.local_now(now)
        if current.timetz().replace(tzinfo=None) >= LOCK_TIME:
            return True
        state = self.day_state(group_id, current)
        return str(state["status"]) != "open"

    def day_state(self, group_id: int, now: datetime | None = None) -> dict[str, Any]:
        current = self.local_now(now)
        day = current.date().isoformat()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM today_wife_day_states WHERE group_id=? AND day=?", (int(group_id), day)
            ).fetchone()
            if row is None:
                theme_id = self._global_theme(connection, day)
                pack = pack_for(theme_id)
                script = self._select_script(pack, int(group_id), day)
                stamp = current.isoformat(timespec="seconds")
                connection.execute(
                    """INSERT INTO today_wife_day_states
                       (group_id,day,theme_id,script_id,state_json,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (
                        int(group_id),
                        day,
                        pack.id,
                        script.id,
                        json.dumps(self._initial_day_payload(script), ensure_ascii=False),
                        stamp,
                        stamp,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM today_wife_day_states WHERE group_id=? AND day=?", (int(group_id), day)
                ).fetchone()
        return self._present_day_state(row)

    def ensure_relation(self, connection: Any, record: Mapping[str, Any], timestamp: str | None = None) -> dict[str, Any]:
        normalized_record = {key: record[key] for key in record.keys()} if hasattr(record, "keys") else dict(record)
        draw_index = int(normalized_record["draw_index"] or 1)
        initial_affection = self.rng.randint(*INITIAL_AFFECTION_RANGE)
        connection.execute(
            """INSERT OR IGNORE INTO today_wife_relation_states
               (group_id,day,actor_id,draw_index,affection,minimum_affection,updated_at)
               VALUES (?,?,?,?,?,?,?)""",
            (
                int(normalized_record["group_id"]), str(normalized_record["day"]), int(normalized_record["actor_id"]),
                draw_index, initial_affection, initial_affection, timestamp or utc_now(),
            ),
        )
        row = connection.execute(
            """SELECT * FROM today_wife_relation_states
               WHERE group_id=? AND day=? AND actor_id=? AND draw_index=?""",
            (int(normalized_record["group_id"]), str(normalized_record["day"]), int(normalized_record["actor_id"]), draw_index),
        ).fetchone()
        relation = self._present_relation_state(row)
        if not relation["narrative"]:
            state_row = connection.execute(
                "SELECT * FROM today_wife_day_states WHERE group_id=? AND day=?",
                (int(normalized_record["group_id"]), str(normalized_record["day"])),
            ).fetchone()
            if state_row is not None:
                context = StoryDirector.context_from_day_state(self._present_day_state(state_row))
                arc = StoryDirector.initial_arc(
                    context,
                    normalized_record,
                    str(normalized_record.get("relationship_key") or StoryDirector.relationship_label(context, normalized_record)),
                )
                connection.execute(
                    """UPDATE today_wife_relation_states SET narrative_json=?,updated_at=?
                       WHERE group_id=? AND day=? AND actor_id=? AND draw_index=?""",
                    (
                        json.dumps(arc, ensure_ascii=False),
                        timestamp or utc_now(),
                        int(normalized_record["group_id"]),
                        str(normalized_record["day"]),
                        int(normalized_record["actor_id"]),
                        draw_index,
                    ),
                )
                relation["narrative"] = arc
        return relation

    def ensure_relation_for_draw(self, record: Mapping[str, Any], now: datetime | None = None) -> None:
        current = self.local_now(now)
        with self.database.connect() as connection:
            self.ensure_relation(connection, record, current.isoformat(timespec="seconds"))

    def freeze_relation(self, record: Mapping[str, Any], now: datetime | None = None) -> dict[str, Any]:
        current = self.local_now(now)
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            relation = self.ensure_relation(connection, record, current.isoformat(timespec="seconds"))
            connection.execute(
                """UPDATE today_wife_relation_states
                   SET frozen_affection=affection,mood='separated',updated_at=?
                   WHERE group_id=? AND day=? AND actor_id=? AND draw_index=?""",
                (current.isoformat(timespec="seconds"), int(record["group_id"]), str(record["day"]), int(record["actor_id"]), int(record["draw_index"] or 1)),
            )
            return {**relation, "frozen_affection": relation["affection"], "mood": "separated"}

    def interaction(
        self,
        group_id: int,
        actor_id: int,
        actor_nickname: str,
        mentioned_id: int | None = None,
        now: datetime | None = None,
        *,
        intent: str = "auto",
        source_message_id: str | int | None = None,
        automatic: bool = False,
        bypass_limit: bool = False,
    ) -> InteractionOutcome:
        current = self.local_now(now)
        day = current.date().isoformat()
        stamp = current.isoformat(timespec="seconds")
        source_id = str(source_message_id).strip() if source_message_id is not None else ""
        # A redelivered QQ event must replay the persisted result even after
        # the day's lock.  Do the cheap read before validation/lock gates, then
        # repeat it inside the write transaction below to close the race.
        if source_id:
            with self.database.connect() as connection:
                existing = connection.execute(
                    """SELECT * FROM today_wife_interaction_events
                       WHERE group_id=? AND source_message_id=?""",
                    (int(group_id), source_id),
                ).fetchone()
                if existing is not None:
                    return self._replayed_interaction_outcome(connection, existing)
        if current.timetz().replace(tzinfo=None) >= LOCK_TIME:
            return InteractionOutcome("locked", message="今晚的篇章已在 23:30 封场，明天再继续写新的故事。")
        normalized_intent = str(intent or "auto").strip() or "auto"
        if normalized_intent not in VALID_INTENTS:
            return InteractionOutcome("invalid_intent", message="互动方式请使用：靠近、倾听、回应、修复或助攻。")
        try:
            mentioned = int(mentioned_id) if mentioned_id is not None else None
        except (TypeError, ValueError):
            return InteractionOutcome("invalid_target", message="互动目标必须是一位有效的群友，不能用无效 @ 代替。")
        if mentioned is not None and mentioned <= 0:
            return InteractionOutcome("invalid_target", message="互动目标必须是一位有效的群友，不能用 @全体 代替。")
        if mentioned == int(actor_id):
            return InteractionOutcome("self_target", message="这次互动不能 @ 自己，也不会消耗次数。")
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if source_id:
                existing = connection.execute(
                    """SELECT * FROM today_wife_interaction_events
                       WHERE group_id=? AND source_message_id=?""",
                    (int(group_id), source_id),
                ).fetchone()
                if existing is not None:
                    return self._replayed_interaction_outcome(connection, existing)
            state_row = self._ensure_day_state(connection, int(group_id), current)
            if str(state_row["status"]) != "open":
                return InteractionOutcome("locked", message="今天的篇章已经结算，只能查看缘分档案和群缘分。")
            records = self._records(connection, int(group_id), day)
            participants = self._participants(records)
            if int(actor_id) not in participants:
                return InteractionOutcome("not_joined", message="你还没走进今天的篇章。先发送 #今日老婆，让一段缘分替你开场。")
            used = connection.execute(
                """SELECT COUNT(*) AS value FROM today_wife_interaction_events
                   WHERE group_id=? AND day=? AND actor_id=?""", (int(group_id), day, int(actor_id))
            ).fetchone()["value"]
            if not bypass_limit and int(used) >= MAX_INTERACTIONS:
                return InteractionOutcome("exhausted", message="你今天已经完成了 5 次互动，留一点故事给明天吧。")
            if mentioned is not None and mentioned not in participants:
                return InteractionOutcome("target_not_joined", message="TA 还没走进今天的篇章，这次指定不会消耗互动次数。")

            pack = pack_for(str(state_row["theme_id"]))
            script = script_for(str(state_row["script_id"]), pack.id)
            action_state = self._available_actions(connection, records, int(actor_id), mentioned)
            if normalized_intent == "auto":
                if not automatic:
                    return self._interaction_prompt(
                        state_row,
                        script,
                        action_state,
                        MAX_INTERACTIONS - int(used),
                    )
                options = tuple(
                    dict(option)
                    for option in action_state.get("action_options", ())
                    if isinstance(option, Mapping) and str(option.get("intent") or "") in VALID_INTENTS
                )
                if not options:
                    return InteractionOutcome("no_valid_action", message="当前没有可自动执行的有效互动。")
                selected_action = self.rng.choice(options)
                normalized_intent = str(selected_action["intent"])
                try:
                    mentioned = int(selected_action.get("mentioned_id")) if selected_action.get("mentioned_id") else None
                except (TypeError, ValueError):
                    mentioned = None
                action_state = self._available_actions(connection, records, int(actor_id), mentioned)
            else:
                selected_action = None
            selected_action = self._matching_action_option(
                action_state,
                normalized_intent,
                mentioned,
            ) or selected_action
            if selected_action is None and not self._legacy_action_is_compatible(
                action_state,
                normalized_intent,
                mentioned,
            ):
                return self._interaction_prompt(
                    state_row,
                    script,
                    action_state,
                    MAX_INTERACTIONS - int(used),
                    requested_intent=normalized_intent,
                )
            mechanism = self.rng.choice(pack.mechanisms)
            prepared = self._prepare_event(
                connection, state_row, records, pack, script, mechanism, int(actor_id), str(actor_nickname), mentioned, stamp, normalized_intent
            )
            prepared["intent"] = normalized_intent
            prepared["choice_id"] = str((selected_action or {}).get("choice_id") or "")
            prepared["actor_nickname"] = self._name(actor_nickname)
            relation_states = self._relation_states_for_effects(connection, int(group_id), day, prepared["effects"])
            self._attach_effect_totals(prepared["effects"], relation_states)
            context = StoryDirector.context(pack.id, script.id)
            narrative_plan = StoryDirector.compose_interaction(
                context,
                prepared,
                relation_states,
                f"{group_id}:{day}:{actor_id}:{source_id or self.rng.randrange(2**31)}",
            )
            prepared["narrative_plan"] = narrative_plan
            prepared["narrative"] = str(narrative_plan["text"])
            event_id = connection.execute(
                """INSERT INTO today_wife_interaction_events
                   (group_id,day,actor_id,actor_nickname,mentioned_id,mentioned_nickname,kind,role,mechanism,act,title,narrative,effects_json,marks_json,narrative_json,intent,source_message_id,key_event,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    int(group_id), day, int(actor_id), self._name(actor_nickname), prepared["mentioned_id"],
                    prepared["mentioned_name"], prepared["kind"], prepared["role"], prepared["mechanism"],
                    int(state_row["act"]), prepared["title"], prepared["narrative"],
                    json.dumps(prepared["effects"], ensure_ascii=False), json.dumps(prepared["marks"], ensure_ascii=False),
                    json.dumps(narrative_plan, ensure_ascii=False), normalized_intent, source_id,
                    int(prepared["key_event"]), stamp,
                ),
            ).lastrowid
            for effect in prepared["effects"]:
                self._apply_effect(connection, int(group_id), day, effect, int(event_id), stamp)
            self._apply_narrative_updates(connection, int(group_id), day, narrative_plan, int(event_id), stamp)
            count = int(state_row["interaction_count"]) + 1
            act, route_key, state_json = self._advance_act(
                state_row,
                pack,
                prepared,
                count,
                int(event_id),
                current,
                participant_count=len(participants),
            )
            connection.execute(
                """UPDATE today_wife_day_states
                   SET interaction_count=?,last_interactor_id=?,last_event_id=?,act=?,route_key=?,state_json=?,updated_at=?
                   WHERE group_id=? AND day=?""",
                (count, int(actor_id), int(event_id), act, route_key, json.dumps(state_json, ensure_ascii=False), stamp, int(group_id), day),
            )
            row = connection.execute(
                "SELECT * FROM today_wife_interaction_events WHERE event_id=?", (int(event_id),)
            ).fetchone()
            event = self._present_event(row)
            event["day_state"] = self._present_day_state(connection.execute(
                "SELECT * FROM today_wife_day_states WHERE group_id=? AND day=?", (int(group_id), day)
            ).fetchone())
            event["actor_remaining"] = MAX_INTERACTIONS - int(used) - 1
            refreshed_records = self._records(connection, int(group_id), day)
            post_actions = self._available_actions(connection, refreshed_records, int(actor_id), mentioned)
            event["available_actions"] = post_actions["available_actions"]
            event["action_options"] = post_actions["action_options"]
            event["current_hook"] = post_actions["current_hook"]
        return InteractionOutcome("played", event)

    def passive_interaction_after_command(
        self,
        group_id: int,
        trigger_user_id: int,
        source_message_id: str | int,
        now: datetime | None = None,
        *,
        probability: float = 0.35,
    ) -> InteractionOutcome:
        """Silently run one valid event after a non-first participant's command."""

        current = self.local_now(now)
        day = current.date().isoformat()
        with self.database.connect() as connection:
            records = self._records(connection, int(group_id), day)
        actors = self._active_actor_rows(records)
        participants = {
            int(record["actor_id"]): record
            for record in actors
        }
        if len(participants) < 2 or int(trigger_user_id) not in participants:
            return InteractionOutcome("not_eligible")
        first_actor_record = min(
            (record for record in records if int(record["actor_id"]) in participants),
            key=lambda row: (str(row.get("drawn_at") or ""), int(row["actor_id"])),
        )
        if int(first_actor_record["actor_id"]) == int(trigger_user_id):
            return InteractionOutcome("first_participant")
        if self.rng.random() >= max(0.0, min(float(probability), 1.0)):
            return InteractionOutcome("not_triggered")
        candidates = [row for row in actors if int(row["actor_id"]) != int(trigger_user_id)]
        if not candidates:
            return InteractionOutcome("no_other_active_participant")
        actor = self.rng.choice(candidates)
        return self.interaction(
            int(group_id),
            int(actor["actor_id"]),
            str(actor.get("actor_nickname") or "这位群友"),
            now=current,
            intent="auto",
            source_message_id=f"passive:{source_message_id}",
            automatic=True,
        )

    def prepare_collective_round(
        self,
        group_id: int,
        round_no: int,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Persist one all-participant round and return its render payload."""

        if int(round_no) not in (1, 2, 3):
            raise ValueError("collective round must be 1, 2, or 3")
        current = self.local_now(now)
        day = current.date().isoformat()
        with self.database.connect() as connection:
            existing = connection.execute(
                """SELECT payload_json FROM today_wife_collective_rounds
                   WHERE group_id=? AND day=? AND round_no=?""",
                (int(group_id), day, int(round_no)),
            ).fetchone()
            if existing is not None:
                payload = self._json_object(existing["payload_json"])
                return payload or None
            records = self._records(connection, int(group_id), day)
        actors = self._active_actor_rows(records)
        if not actors:
            return None

        events: list[dict[str, Any]] = []
        for actor in actors:
            result = self.interaction(
                int(group_id),
                int(actor["actor_id"]),
                str(actor.get("actor_nickname") or "这位群友"),
                now=current,
                intent="auto",
                source_message_id=f"collective:{day}:{int(round_no)}:{int(actor['actor_id'])}",
                automatic=True,
                bypass_limit=True,
            )
            if result.kind == "played" and result.event is not None:
                events.append(result.event)
        if not events:
            return None

        core = self.rng.choice(events)
        ordered = [
            core,
            *sorted(
                (event for event in events if event is not core),
                key=self._collective_event_rank,
                reverse=True,
            ),
        ]
        act_after = 2 if int(round_no) == 1 else 3
        stamp = current.isoformat(timespec="seconds")
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = self._ensure_day_state(connection, int(group_id), current)
            state_payload = self._json_object(state["state_json"])
            state_payload["last_turn"] = {
                "event_id": int(core.get("event_id") or 0),
                "route": str(state["route_key"]),
                "at": stamp,
                "round": int(round_no),
            }
            state_payload["turning_point"] = str(core.get("narrative") or core.get("title") or "")
            state_payload["collective_round"] = int(round_no)
            connection.execute(
                """UPDATE today_wife_day_states
                   SET act=?,last_event_id=?,state_json=?,updated_at=?
                   WHERE group_id=? AND day=?""",
                (
                    act_after,
                    int(core.get("event_id") or 0),
                    json.dumps(state_payload, ensure_ascii=False),
                    stamp,
                    int(group_id),
                    day,
                ),
            )
            refreshed = connection.execute(
                "SELECT * FROM today_wife_day_states WHERE group_id=? AND day=?",
                (int(group_id), day),
            ).fetchone()
            payload = {
                "group_id": int(group_id),
                "day": day,
                "round_no": int(round_no),
                "title": ("午间集体互动", "傍晚集体互动", "今日故事收官")[int(round_no) - 1],
                "core_event_id": int(core.get("event_id") or 0),
                "events": ordered,
                "participant_count": len(actors),
                "day_state": self._present_day_state(refreshed),
            }
            connection.execute(
                """INSERT INTO today_wife_collective_rounds
                   (group_id,day,round_no,payload_json,prepared_at)
                   VALUES (?,?,?,?,?)""",
                (int(group_id), day, int(round_no), json.dumps(payload, ensure_ascii=False), stamp),
            )
        if int(round_no) == 3:
            conclusion_state = self.lock_and_conclude(int(group_id), current)
            payload["conclusion"] = conclusion_state.get("conclusion") or {}
            with self.database.connect() as connection:
                connection.execute(
                    """UPDATE today_wife_collective_rounds SET payload_json=?
                       WHERE group_id=? AND day=? AND round_no=3""",
                    (json.dumps(payload, ensure_ascii=False), int(group_id), day),
                )
        return payload

    def collective_round_delivered(self, group_id: int, round_no: int, now: datetime | None = None) -> bool:
        current = self.local_now(now)
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT delivered_at FROM today_wife_collective_rounds
                   WHERE group_id=? AND day=? AND round_no=?""",
                (int(group_id), current.date().isoformat(), int(round_no)),
            ).fetchone()
        return bool(row is not None and row["delivered_at"])

    def collective_participant_count(self, group_id: int, now: datetime | None = None) -> int:
        current = self.local_now(now)
        with self.database.connect() as connection:
            records = self._records(connection, int(group_id), current.date().isoformat())
        return len(self._active_actor_rows(records))

    def mark_collective_round_delivery(
        self,
        group_id: int,
        round_no: int,
        now: datetime | None = None,
        *,
        error: str = "",
    ) -> None:
        current = self.local_now(now)
        stamp = current.isoformat(timespec="seconds")
        with self.database.connect() as connection:
            if error:
                connection.execute(
                    """UPDATE today_wife_collective_rounds
                       SET delivery_attempts=delivery_attempts+1,delivery_error=?
                       WHERE group_id=? AND day=? AND round_no=?""",
                    (str(error)[:300], int(group_id), current.date().isoformat(), int(round_no)),
                )
            else:
                connection.execute(
                    """UPDATE today_wife_collective_rounds
                       SET delivered_at=?,delivery_attempts=delivery_attempts+1,delivery_error=''
                       WHERE group_id=? AND day=? AND round_no=?""",
                    (stamp, int(group_id), current.date().isoformat(), int(round_no)),
                )
                if int(round_no) == 3:
                    connection.execute(
                        """UPDATE today_wife_day_states
                           SET status='published',delivered_at=?,delivery_error='',updated_at=?
                           WHERE group_id=? AND day=?""",
                        (stamp, stamp, int(group_id), current.date().isoformat()),
                    )

    def personal_archive(self, group_id: int, user_id: int, now: datetime | None = None) -> dict[str, Any]:
        current = self.local_now(now)
        day = current.date().isoformat()
        with self.database.connect() as connection:
            state = self._ensure_day_state(connection, int(group_id), current)
            records = self._records(connection, int(group_id), day)
            own = [row for row in records if int(row["actor_id"]) == int(user_id)]
            incoming = [row for row in records if int(row["target_id"]) == int(user_id)]
            for record in own + incoming:
                self.ensure_relation(connection, record)
            relation_rows = connection.execute(
                "SELECT * FROM today_wife_relation_states WHERE group_id=? AND day=?", (int(group_id), day)
            ).fetchall()
            relation_map = {(int(row["actor_id"]), int(row["draw_index"])): self._present_relation_state(row) for row in relation_rows}
            all_events = [self._present_event(row) for row in connection.execute(
                """SELECT * FROM today_wife_interaction_events WHERE group_id=? AND day=?
                   ORDER BY event_id DESC""", (int(group_id), day),
            ).fetchall()]
            events = [
                event for event in all_events
                if int(event["actor_id"]) == int(user_id)
                or any(
                    int(effect.get("actor_id") or 0) == int(user_id)
                    or int(effect.get("target_id") or 0) == int(user_id)
                    for effect in event["effects"]
                )
            ][:12]
            history = self._personal_history_rows(connection, int(group_id), int(user_id), limit=3, offset=0)
            action_state = self._available_actions(connection, records, int(user_id), None)
        return {
            "day_state": self._present_day_state(state),
            "own": [{**record, "relation": relation_map.get((int(record["actor_id"]), int(record["draw_index"])), {})} for record in own],
            "incoming": [{**record, "relation": relation_map.get((int(record["actor_id"]), int(record["draw_index"])), {})} for record in incoming],
            "events": events,
            "history": history,
            "remaining": max(0, MAX_INTERACTIONS - sum(1 for event in all_events if int(event["actor_id"]) == int(user_id))),
            "available_actions": action_state["available_actions"],
            "action_options": action_state["action_options"],
        }

    def personal_history(self, group_id: int, user_id: int, page: int = 1) -> dict[str, Any]:
        """Return a permanent, paginated personal archive without storing chat text."""
        page = max(1, int(page))
        with self.database.connect() as connection:
            total = int(connection.execute(
                "SELECT COUNT(*) AS value FROM today_wife_records WHERE group_id=? AND actor_id=?",
                (int(group_id), int(user_id)),
            ).fetchone()["value"])
            rows = self._personal_history_rows(
                connection,
                int(group_id),
                int(user_id),
                limit=PERSONAL_HISTORY_PAGE_SIZE,
                offset=(page - 1) * PERSONAL_HISTORY_PAGE_SIZE,
            )
        return {
            "rows": rows,
            "page": page,
            "page_size": PERSONAL_HISTORY_PAGE_SIZE,
            "total": total,
            "pages": max(1, (total + PERSONAL_HISTORY_PAGE_SIZE - 1) // PERSONAL_HISTORY_PAGE_SIZE),
        }

    def group_story(
        self,
        group_id: int,
        now: datetime | None = None,
        requested_day: date | None = None,
    ) -> dict[str, Any]:
        current = self.local_now(now)
        target_day = requested_day or current.date()
        day = target_day.isoformat()
        with self.database.connect() as connection:
            if target_day == current.date():
                state = self._ensure_day_state(connection, int(group_id), current)
            else:
                state = connection.execute(
                    "SELECT * FROM today_wife_day_states WHERE group_id=? AND day=?", (int(group_id), day)
                ).fetchone()
            records = self._records(connection, int(group_id), day)
            for record in records:
                self.ensure_relation(connection, record)
            relation_rows = connection.execute(
                "SELECT * FROM today_wife_relation_states WHERE group_id=? AND day=?", (int(group_id), day)
            ).fetchall()
            relation_map = {(int(row["actor_id"]), int(row["draw_index"])): self._present_relation_state(row) for row in relation_rows}
            events = [self._present_event(row) for row in connection.execute(
                "SELECT * FROM today_wife_interaction_events WHERE group_id=? AND day=? ORDER BY event_id", (int(group_id), day)
            ).fetchall()]
        full_detail = target_day >= current.date() - timedelta(days=GROUP_DETAIL_RETENTION_DAYS - 1)
        presented_state = self._present_day_state(state) if state is not None else self._archived_day_state(day)
        enriched_records = [{**record, "relation": relation_map.get((int(record["actor_id"]), int(record["draw_index"])), {})} for record in records]
        if state is not None:
            context = StoryDirector.context_from_day_state(presented_state)
            route_key = self._dominant_route(self._route_scores(presented_state.get("state", {})))
            spotlight = StoryDirector.group_caption(
                context,
                enriched_records,
                events,
                str(presented_state["act_title"]),
                route_label=self._route_label(route_key),
                turning_point=str(presented_state.get("state", {}).get("turning_point") or ""),
            )
        else:
            spotlight = "这一页已经归入留档，关系仍保留它们各自的来处。"
        return {
            "day_state": presented_state,
            "records": enriched_records,
            "events": events,
            "spotlight": spotlight,
            "day": day,
            "full_detail": full_detail,
        }

    def group_archive(self, group_id: int, now: datetime | None = None, limit: int = 30) -> dict[str, Any]:
        """List daily group summaries; only the newest seven days expose full graphs."""
        current = self.local_now(now)
        with self.database.connect() as connection:
            days = connection.execute(
                """SELECT day FROM today_wife_records WHERE group_id=?
                   GROUP BY day ORDER BY day DESC LIMIT ?""",
                (int(group_id), max(1, min(int(limit), 90))),
            ).fetchall()
            summaries = [
                self._group_day_summary(connection, int(group_id), str(row["day"]), current.date())
                for row in days
            ]
        return {"summaries": summaries, "detail_retention_days": GROUP_DETAIL_RETENTION_DAYS}

    def draw_reveal(self, record: Mapping[str, Any], relation: Mapping[str, Any], now: datetime | None = None) -> dict[str, Any]:
        """Return the canonical draw card story for a relationship record."""

        record_day = str(record.get("day") or "").strip()
        state = None
        if record_day:
            with self.database.connect() as connection:
                state = connection.execute(
                    "SELECT * FROM today_wife_day_states WHERE group_id=? AND day=?",
                    (int(record["group_id"]), record_day),
                ).fetchone()
        if state is not None:
            context = StoryDirector.context_from_day_state(self._present_day_state(state))
        else:
            context = StoryDirector.context_for_story_id(str(record.get("story_id") or ""))
        if context is None:
            # Legacy rows predate daily story state.  Preserve a neutral archive
            # scene instead of accidentally borrowing the current day's theme.
            context = {
                "scene_id": str(record.get("story_id") or "archived_draw"),
                "title": "留档篇章",
                "prop": "这一天留下的线索",
                "opening": "这段缘分已经归入留档。",
            }
        return StoryDirector.compose_draw_reveal(context, record, relation)

    def lock_and_conclude(self, group_id: int, now: datetime | None = None) -> dict[str, Any]:
        current = self.local_now(now)
        day = current.date().isoformat()
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = self._ensure_day_state(connection, int(group_id), current)
            if state["conclusion_built_at"]:
                return self._present_day_state(state)
            records = self._records(connection, int(group_id), day)
            for record in records:
                self.ensure_relation(connection, record)
            relation_rows = connection.execute(
                "SELECT * FROM today_wife_relation_states WHERE group_id=? AND day=?", (int(group_id), day)
            ).fetchall()
            relation_map = {(int(row["actor_id"]), int(row["draw_index"])): self._present_relation_state(row) for row in relation_rows}
            events = [self._present_event(row) for row in connection.execute(
                "SELECT * FROM today_wife_interaction_events WHERE group_id=? AND day=? ORDER BY event_id", (int(group_id), day)
            ).fetchall()]
            conclusion = self._build_conclusion(self._present_day_state(state), records, relation_map, events)
            stamp = current.isoformat(timespec="seconds")
            connection.execute(
                """UPDATE today_wife_day_states SET status='locked',conclusion_json=?,conclusion_built_at=?,updated_at=?
                   WHERE group_id=? AND day=?""",
                (json.dumps(conclusion, ensure_ascii=False), stamp, stamp, int(group_id), day),
            )
            state = connection.execute("SELECT * FROM today_wife_day_states WHERE group_id=? AND day=?", (int(group_id), day)).fetchone()
        return self._present_day_state(state)

    def pending_conclusions(self, now: datetime | None = None) -> list[dict[str, Any]]:
        current = self.local_now(now)
        day = current.date().isoformat()
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM today_wife_day_states
                   WHERE day=? AND status IN ('locked','published') AND delivered_at IS NULL
                   ORDER BY group_id""", (day,)
            ).fetchall()
        return [self._present_day_state(row) for row in rows]

    def mark_conclusion_delivered(self, group_id: int, now: datetime | None = None, error: str = "") -> None:
        current = self.local_now(now)
        with self.database.connect() as connection:
            if error:
                connection.execute(
                    "UPDATE today_wife_day_states SET delivery_error=?,updated_at=? WHERE group_id=? AND day=?",
                    (error[:300], current.isoformat(timespec="seconds"), int(group_id), current.date().isoformat()),
                )
            else:
                connection.execute(
                    """UPDATE today_wife_day_states SET status='published',delivered_at=?,delivery_error='',updated_at=?
                       WHERE group_id=? AND day=?""",
                    (current.isoformat(timespec="seconds"), current.isoformat(timespec="seconds"), int(group_id), current.date().isoformat()),
                )

    def _prepare_event(
        self,
        connection: Any,
        state: Any,
        records: list[dict[str, Any]],
        pack: ThemePack,
        script: Any,
        mechanism: str,
        actor_id: int,
        actor_name: str,
        mentioned: int | None,
        stamp: str,
        intent: str = "auto",
    ) -> dict[str, Any]:
        active_own = self._latest_active(records, actor_id)
        incoming = [record for record in records if int(record["target_id"]) == actor_id and str(record["status"]) == "active"]
        pending = self._pending_relation(connection, active_own) if active_own else None
        target_record = self._target_record(records, mentioned) if mentioned else None
        mentioned_name = self._person_name(records, mentioned) if mentioned else ""
        active_relation = self.ensure_relation(connection, active_own) if active_own else None
        rescue_target = active_relation is not None and int(active_relation["affection"]) < 0
        if intent == "修复":
            if active_own is None or not (pending or rescue_target):
                return self._same_scene_event(mechanism, actor_name, mentioned, mentioned_name, "修复还不需要发生")
            if mentioned is not None and mentioned != int(active_own["target_id"]):
                return self._same_scene_event(mechanism, actor_name, mentioned, mentioned_name, "这段旧事不在这里")
            return self._chain_event(
                pack, script, mechanism, active_own, actor_id, actor_name, mentioned,
                mentioned_name, pending or {}, active_relation or {}, intent=intent,
            )
        if intent == "回应":
            matching = [record for record in incoming if mentioned is None or int(record["actor_id"]) == mentioned]
            if not matching:
                return self._same_scene_event(mechanism, actor_name, mentioned, mentioned_name, "暂时没有等候的关系")
            return self._response_event(
                connection, pack, script, mechanism, records, actor_id, actor_name, mentioned, mentioned_name, intent=intent
            )
        if intent == "助攻":
            if mentioned is None:
                return self._same_scene_event(mechanism, actor_name, mentioned, mentioned_name, "助攻需要先 @ 一位已入场群友")
            return self._third_event(
                connection, pack, script, mechanism, records, actor_id, actor_name, mentioned, mentioned_name,
                force_positive=True, intent=intent,
            )
        if intent in {"靠近", "倾听"}:
            if active_own is None:
                return self._same_scene_event(mechanism, actor_name, mentioned, mentioned_name, "你还没有一段可以靠近的主缘分")
            if mentioned is not None and mentioned != int(active_own["target_id"]):
                return self._same_scene_event(mechanism, actor_name, mentioned, mentioned_name, "这次只能先回应自己的今日缘分")
            return self._direct_event(
                pack, script, mechanism, active_own, actor_id, actor_name, mentioned,
                mentioned_name, intent=intent,
            )
        if (pending or rescue_target) and (
            mentioned is None
            or mentioned in {int(active_own["target_id"]), int((pending or {}).get("third_id") or 0)}
        ):
            return self._chain_event(
                pack, script, mechanism, active_own, actor_id, actor_name, mentioned,
                mentioned_name, pending or {}, active_relation or {}, intent=intent,
            )
        if incoming and (mentioned is None or (target_record is not None and int(target_record["target_id"]) == actor_id)):
            return self._response_event(connection, pack, script, mechanism, records, actor_id, actor_name, mentioned, mentioned_name, intent=intent)
        if mentioned is not None:
            if active_own and int(active_own["target_id"]) == mentioned:
                target_has_others = any(int(record["target_id"]) == mentioned and int(record["actor_id"]) != actor_id for record in records)
                if not target_has_others or self.rng.randrange(100) < 40:
                    return self._direct_event(pack, script, mechanism, active_own, actor_id, actor_name, mentioned, mentioned_name, intent=intent)
                return self._third_event(connection, pack, script, mechanism, records, actor_id, actor_name, mentioned, mentioned_name, intent=intent)
            # An @ outside the actor's own relationship must drive the exact
            # relation that changes. Never keep A in metadata while changing B.
            return self._third_event(connection, pack, script, mechanism, records, actor_id, actor_name, mentioned, mentioned_name, intent=intent)
        weights = pack.director.act_weights[max(0, min(2, int(state["act"]) - 1))]
        rule = mechanic_for(mechanism)
        options: list[str] = []
        if active_own:
            options.extend(["direct"] * max(1, int(weights["direct"]) + int(rule.event_bias.get("direct", 0))))
        if incoming:
            options.extend(["response"] * max(1, int(weights["response"]) + int(rule.event_bias.get("response", 0))))
        if len(records) > 1:
            options.extend(["third"] * max(1, int(weights["third"]) + int(rule.event_bias.get("third", 0))))
        if not options:
            return self._direct_event(pack, script, mechanism, active_own, actor_id, actor_name, None, "", intent=intent)
        choice = self.rng.choice(options)
        if choice == "response":
            return self._response_event(connection, pack, script, mechanism, records, actor_id, actor_name, None, "", intent=intent)
        if choice == "third":
            return self._third_event(connection, pack, script, mechanism, records, actor_id, actor_name, None, "", intent=intent)
        return self._direct_event(pack, script, mechanism, active_own, actor_id, actor_name, None, "", intent=intent)

    def _available_actions(
        self,
        connection: Any,
        records: list[dict[str, Any]],
        actor_id: int,
        mentioned: int | None,
    ) -> dict[str, Any]:
        """Describe valid choices without producing an event or changing state."""

        active_own = self._latest_active(records, actor_id)
        incoming = [
            record for record in records
            if int(record["target_id"]) == int(actor_id) and str(record["status"]) == "active"
        ]
        relation = self._existing_relation_state(connection, active_own) if active_own else None
        pending = dict(relation.get("pending") or {}) if relation else {}
        arc = relation.get("narrative") if relation and isinstance(relation.get("narrative"), Mapping) else {}
        hook = arc.get("hook") if isinstance(arc.get("hook"), Mapping) else {}
        options: list[dict[str, Any]] = []

        def relation_options(
            record: Mapping[str, Any],
            relation_state: Mapping[str, Any] | None,
            intents: tuple[str, ...],
            *,
            target_id: int | None,
            target_name: str,
            requires_mention: bool,
            allow_compatibility: bool = False,
        ) -> tuple[dict[str, Any], ...]:
            context = StoryDirector.context_for_story_id(str(record.get("story_id") or ""))
            if context is None:
                return ()
            relation_arc = (
                relation_state.get("narrative")
                if isinstance(relation_state, Mapping)
                and isinstance(relation_state.get("narrative"), Mapping)
                else {}
            )
            return StoryDirector.action_options_for_arc(
                context,
                relation_arc,
                intents,
                relation_key=f"{int(record['actor_id'])}:{int(record['draw_index'])}",
                target_id=target_id,
                target_name=target_name,
                requires_mention=requires_mention,
                allow_compatibility=allow_compatibility,
            )

        legacy_available: list[str] = []
        if active_own is not None and (mentioned is None or mentioned == int(active_own["target_id"])):
            options.extend(
                relation_options(
                    active_own,
                    relation,
                    ("靠近", "倾听"),
                    target_id=int(active_own["target_id"]),
                    target_name=str(active_own.get("target_nickname") or "这位群友"),
                    requires_mention=False,
                )
            )
            legacy_available.extend(("靠近", "倾听"))
        if active_own is not None and relation is not None and (pending or int(relation.get("affection") or 0) < 0) and (
            mentioned is None or mentioned == int(active_own["target_id"])
        ):
            options.extend(
                relation_options(
                    active_own,
                    relation,
                    ("修复",),
                    target_id=int(active_own["target_id"]),
                    target_name=str(active_own.get("target_nickname") or "这位群友"),
                    requires_mention=False,
                    allow_compatibility=True,
                )
            )
            legacy_available.append("修复")

        matching_incoming = [
            record for record in incoming
            if mentioned is None or int(record["actor_id"]) == int(mentioned)
        ]
        response_requires_mention = mentioned is None and len(matching_incoming) > 1
        for record in matching_incoming:
            incoming_relation = self._existing_relation_state(connection, record)
            options.extend(
                relation_options(
                    record,
                    incoming_relation,
                    ("回应",),
                    target_id=int(record["actor_id"]),
                    target_name=str(record.get("actor_nickname") or "这位群友"),
                    requires_mention=response_requires_mention,
                    allow_compatibility=True,
                )
            )
        if matching_incoming:
            legacy_available.append("回应")

        other_relations = [
            record for record in records
            if str(record["status"]) == "active"
            and int(record["actor_id"]) != int(actor_id)
            and (mentioned is None or mentioned in {int(record["actor_id"]), int(record["target_id"])})
        ]
        assist_by_target: dict[int, Mapping[str, Any]] = {}
        for record in other_relations:
            for person_id in (int(record["actor_id"]), int(record["target_id"])):
                if person_id != int(actor_id):
                    assist_by_target.setdefault(person_id, record)
        assist_targets = sorted(assist_by_target)
        if mentioned is None:
            assist_targets = assist_targets[:3]
        for person_id in assist_targets:
            record = assist_by_target[person_id]
            other_relation = self._existing_relation_state(connection, record)
            options.extend(
                relation_options(
                    record,
                    other_relation,
                    ("助攻",),
                    target_id=person_id,
                    target_name=self._person_name(records, person_id),
                    requires_mention=True,
                    allow_compatibility=True,
                )
            )
        if mentioned is not None and assist_targets:
            legacy_available.append("助攻")

        unique_options: list[dict[str, Any]] = []
        seen_options: set[tuple[str, int | None, str]] = set()
        for option in options:
            try:
                target = int(option.get("mentioned_id")) if option.get("mentioned_id") else None
            except (TypeError, ValueError):
                target = None
            identity = (
                str(option.get("choice_id") or ""),
                target,
                str(option.get("display_command") or option.get("command") or ""),
            )
            if identity not in seen_options:
                seen_options.add(identity)
                unique_options.append(option)
        prompt_hook = hook
        if not prompt_hook and matching_incoming:
            incoming_relation = self._existing_relation_state(connection, matching_incoming[0])
            incoming_arc = (
                incoming_relation.get("narrative")
                if incoming_relation and isinstance(incoming_relation.get("narrative"), Mapping)
                else {}
            )
            prompt_hook = (
                incoming_arc.get("hook")
                if isinstance(incoming_arc.get("hook"), Mapping)
                else {}
            )
        return {
            "available_actions": tuple(
                dict.fromkeys(str(option.get("intent") or "") for option in unique_options if str(option.get("intent") or ""))
            ),
            "action_options": tuple(unique_options),
            "legacy_available": tuple(dict.fromkeys(legacy_available)),
            "current_hook": str(prompt_hook.get("summary") or "先选择一件想认真做的事。"),
            "relationship_target": str(active_own.get("target_nickname") or "") if active_own else "",
            "mentioned_id": mentioned,
        }

    @staticmethod
    def _matching_action_option(
        action_state: Mapping[str, Any],
        intent: str,
        mentioned: int | None,
    ) -> dict[str, Any] | None:
        """Return the one visible option that authorizes this exact command."""

        raw_options = action_state.get("action_options")
        if not isinstance(raw_options, (tuple, list)):
            return None
        for raw_option in raw_options:
            if not isinstance(raw_option, Mapping) or str(raw_option.get("intent") or "") != intent:
                continue
            try:
                option_target = int(raw_option.get("mentioned_id")) if raw_option.get("mentioned_id") else None
            except (TypeError, ValueError):
                option_target = None
            if mentioned is None:
                if bool(raw_option.get("requires_mention")):
                    continue
                return dict(raw_option)
            if option_target not in (None, int(mentioned)):
                continue
            return dict(raw_option)
        return None

    @staticmethod
    def _legacy_action_is_compatible(
        action_state: Mapping[str, Any], intent: str, mentioned: int | None
    ) -> bool:
        """Keep old bare commands working without advertising stale shortcuts."""

        if intent == "助攻" and mentioned is None:
            return False
        values = action_state.get("legacy_available")
        return intent in values if isinstance(values, (tuple, list, set, frozenset)) else False

    def _interaction_prompt(
        self,
        state: Any,
        script: Any,
        action_state: Mapping[str, Any],
        remaining: int,
        *,
        requested_intent: str = "",
    ) -> InteractionOutcome:
        available = tuple(str(item) for item in action_state.get("available_actions", ()) if str(item))
        action_options = tuple(
            dict(item)
            for item in action_state.get("action_options", ())
            if isinstance(item, Mapping)
        )
        hook = str(action_state.get("current_hook") or f"{script.prop}还在等一个回应")
        choices = "、".join(
            str(item.get("display_command") or item.get("command") or "")
            for item in action_options
            if str(item.get("display_command") or item.get("command") or "")
        ) or "先等待新的关系线索"
        if requested_intent:
            message = f"“{requested_intent}”暂时不适合这一幕，不会消耗次数。当前线索：{hook}。可选：{choices}。"
        else:
            message = f"当前线索：{hook}。请选择：{choices}。"
        return InteractionOutcome(
            "prompt",
            {
                "title": "今日互动｜选择下一步",
                "available_actions": available,
                "action_options": action_options,
                "current_hook": hook,
                "actor_remaining": max(0, int(remaining)),
                "day_state": self._present_day_state(state),
                "script_title": str(script.title),
                "mentioned_id": action_state.get("mentioned_id"),
            },
            message,
        )

    def _same_scene_event(
        self,
        mechanism: str,
        actor_name: str,
        mentioned: int | None,
        mentioned_name: str,
        reason: str,
    ) -> dict[str, Any]:
        return {
            "kind": "same_scene",
            "role": "同场停留",
            "mechanism": mechanism,
            "mentioned_id": mentioned,
            "mentioned_name": mentioned_name,
            "title": "今日互动｜先把这一幕看清楚",
            "narrative": reason,
            "effects": [],
            "marks": ["同场"],
            "key_event": False,
        }

    def _direct_event(
        self,
        pack: ThemePack,
        script: Any,
        mechanism: str,
        relation: dict[str, Any] | None,
        actor_id: int,
        actor_name: str,
        mentioned: int | None,
        mentioned_name: str,
        *,
        intent: str = "auto",
    ) -> dict[str, Any]:
        if relation is None:
            return self._same_scene_event(mechanism, actor_name, mentioned, mentioned_name, "这次只有一次同场，没有改写任何关系。")
        target = str(relation["target_nickname"])
        rule = mechanic_for(mechanism)
        if intent == "倾听":
            # Listening advances a fact rather than gambling the whole scene:
            # it is deliberately narrower and cannot trigger a severe miss.
            delta = self._bounded_delta(self.rng.randint(3, 12) + rule.delta_bias)
        else:
            # Approaching keeps the high-variance social-risk profile.
            intent_bias = 2 if intent == "靠近" else 0
            delta = self._swing("direct", rule.delta_bias + intent_bias)
        mark = self._mark_for(delta, "direct")
        return {
            "kind": "direct", "role": INTENT_LABELS.get(intent, "主动互动"), "mechanism": mechanism, "mentioned_id": mentioned,
            "mentioned_name": mentioned_name or target, "title": self._event_title(delta, INTENT_LABELS.get(intent, "主动互动")), "narrative": "",
            "effects": [self._effect(relation, delta, mark, actor_id)], "marks": [mark], "key_event": abs(delta) >= 21,
        }

    def _response_event(
        self,
        connection: Any,
        pack: ThemePack,
        script: Any,
        mechanism: str,
        records: list[dict[str, Any]],
        actor_id: int,
        actor_name: str,
        mentioned: int | None,
        mentioned_name: str,
        *,
        intent: str = "auto",
    ) -> dict[str, Any]:
        relationships = [record for record in records if int(record["target_id"]) == actor_id and str(record["status"]) == "active"]
        if mentioned is not None:
            relationships = [record for record in relationships if int(record["actor_id"]) == mentioned]
        weighted: list[tuple[dict[str, Any], int]] = []
        for record in relationships:
            state = self.ensure_relation(connection, record)
            weight = max(1, int(state["affection"]) + 6 * int(state["interaction_count"]) + 1)
            weighted.append((record, weight))
        main = self._weighted_record(weighted)
        effects: list[dict[str, Any]] = []
        rule = mechanic_for(mechanism)
        for record, _weight in weighted:
            if record is main:
                delta = self._bounded_delta(self.rng.randint(18, 30) + rule.delta_bias)
                mark = "被看见"
            else:
                if rule.response_mode == "shared":
                    delta = self._bounded_delta(self.rng.randint(7, 16) + rule.delta_bias)
                elif rule.response_mode == "volatile":
                    delta = self._bounded_delta(self.rng.choice([self.rng.randint(1, 13), self.rng.randint(-13, 0)]) + rule.delta_bias)
                else:
                    delta = self._bounded_delta(self.rng.choice([self.rng.randint(3, 12), self.rng.randint(-12, 0)]) + rule.delta_bias)
                mark = "收到回应" if delta > 0 else "没能赶上"
            effects.append(self._effect(record, delta, mark, actor_id, response=True))
        return {
            "kind": "response", "role": INTENT_LABELS.get(intent, "回应缘分"), "mechanism": mechanism, "mentioned_id": mentioned,
            "mentioned_name": mentioned_name, "title": "今日互动｜有人等到了回应",
            "narrative": "",
            "effects": effects, "marks": ["回应"], "key_event": any(effect["delta"] >= 21 for effect in effects),
        }

    def _third_event(
        self,
        connection: Any,
        pack: ThemePack,
        script: Any,
        mechanism: str,
        records: list[dict[str, Any]],
        actor_id: int,
        actor_name: str,
        mentioned: int | None,
        mentioned_name: str,
        *,
        force_positive: bool = False,
        intent: str = "auto",
    ) -> dict[str, Any]:
        candidates = [record for record in records if str(record["status"]) == "active" and int(record["actor_id"]) != actor_id]
        if mentioned is not None:
            related = [record for record in candidates if mentioned in {int(record["actor_id"]), int(record["target_id"])}]
            if not related:
                # An explicit @ is a targeting contract.  Do not silently
                # mutate another couple merely because the mentioned member has
                # no eligible third-party relationship to affect.
                return self._same_scene_event(
                    mechanism,
                    actor_name,
                    mentioned,
                    mentioned_name,
                    "这次没有可由你助攻的关系",
                )
            candidates = related
        if not candidates:
            own = self._latest_active(records, actor_id)
            return self._direct_event(pack, script, mechanism, own, actor_id, actor_name, mentioned, mentioned_name, intent=intent)
        record = self.rng.choice(candidates)
        left, right = str(record["actor_nickname"]), str(record["target_nickname"])
        rule = mechanic_for(mechanism)
        positive = force_positive or self.rng.randrange(100) < rule.third_positive_rate
        delta = self._bounded_delta((self.rng.randint(8, 20) if positive else -self.rng.randint(8, 20)) + rule.delta_bias)
        mark = "被助攻" if positive else "被打断"
        effects = [self._effect(record, delta, mark, actor_id, third_party=True)]
        if rule.third_spread:
            connected = [
                item for item in candidates
                if item is not record and int(item["target_id"]) in {int(record["actor_id"]), int(record["target_id"])}
            ]
            if connected:
                side = self.rng.choice(connected)
                side_delta = self._bounded_delta(self.rng.randint(2, 8) if positive else -self.rng.randint(2, 8))
                effects.append(self._effect(side, side_delta, "余波传来", actor_id, third_party=True))
        return {
            "kind": "assist" if positive else "interference", "role": INTENT_LABELS.get(intent, "关键助攻" if positive else "意外介入"), "mechanism": mechanism,
            "mentioned_id": mentioned, "mentioned_name": mentioned_name, "title": "今日互动｜" + ("替别人接住了线索" if positive else "把话截在了半路"),
            "narrative": "",
            "effects": effects, "marks": ["助攻者" if positive else "好心办坏事"],
            "key_event": positive and delta >= 16,
        }

    def _chain_event(
        self,
        pack: ThemePack,
        script: Any,
        mechanism: str,
        relation: dict[str, Any],
        actor_id: int,
        actor_name: str,
        mentioned: int | None,
        mentioned_name: str,
        pending: Mapping[str, Any],
        relation_state: Mapping[str, Any],
        *,
        intent: str = "auto",
    ) -> dict[str, Any]:
        rule = mechanic_for(mechanism)
        success_rate = self._rescue_success_rate(int(relation_state["affection"]), rule.chain_success_rate)
        delta = self._bounded_delta((self.rng.randint(13, 30) if self.rng.randrange(100) < success_rate else -self.rng.randint(13, 30)) + rule.delta_bias)
        mark = "一起找回" if delta > 0 else "误会加深"
        return {
            "kind": "chain", "role": INTENT_LABELS.get(intent, "连锁承接"), "mechanism": mechanism, "mentioned_id": mentioned,
            "mentioned_name": mentioned_name or str(relation["target_nickname"]), "title": "今日互动｜上次的事有了后续",
            "narrative": "",
            "effects": [self._effect(relation, delta, mark, actor_id, clear_pending=True)], "marks": [mark], "key_event": delta >= 21,
        }

    def _apply_effect(self, connection: Any, group_id: int, day: str, effect: Mapping[str, Any], event_id: int, stamp: str) -> None:
        record = connection.execute(
            """SELECT * FROM today_wife_records WHERE group_id=? AND day=? AND actor_id=? AND draw_index=?""",
            (group_id, day, int(effect["actor_id"]), int(effect["draw_index"])),
        ).fetchone()
        if record is None:
            return
        state = self.ensure_relation(connection, record, stamp)
        if state.get("frozen_affection") is not None:
            return
        delta = int(effect["delta"])
        next_affection = int(state["affection"]) + delta
        marks = list(state["marks"])
        mark = str(effect.get("mark") or "")
        if mark and mark not in marks:
            marks.append(mark)
        pending: dict[str, Any] = dict(state["pending"])
        if bool(effect.get("clear_pending")):
            pending = {}
        elif delta < 0:
            pending = {"type": "repair", "source_event_id": event_id, "third_id": int(effect.get("source_id") or 0)}
        mood = "highlight" if delta >= 21 else "repair" if delta < 0 else "warm" if delta >= 10 else "calm"
        connection.execute(
            """UPDATE today_wife_relation_states
               SET affection=?,minimum_affection=MIN(minimum_affection,?),mood=?,marks_json=?,pending_json=?,
                   interaction_count=interaction_count+?,response_count=response_count+?,third_party_impacts=third_party_impacts+?,updated_at=?
               WHERE group_id=? AND day=? AND actor_id=? AND draw_index=?""",
            (
                next_affection, next_affection, mood, json.dumps(marks, ensure_ascii=False), json.dumps(pending, ensure_ascii=False),
                1 if not effect.get("response") and not effect.get("third_party") else 0, 1 if effect.get("response") else 0,
                1 if effect.get("third_party") else 0, stamp, group_id, day, int(effect["actor_id"]), int(effect["draw_index"]),
            ),
        )

    def _relation_states_for_effects(
        self,
        connection: Any,
        group_id: int,
        day: str,
        effects: list[Mapping[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        states: dict[str, dict[str, Any]] = {}
        for effect in effects:
            actor_id, draw_index = int(effect["actor_id"]), int(effect["draw_index"])
            row = connection.execute(
                """SELECT * FROM today_wife_records WHERE group_id=? AND day=? AND actor_id=? AND draw_index=?""",
                (group_id, day, actor_id, draw_index),
            ).fetchone()
            if row is None:
                continue
            relation = self.ensure_relation(connection, row)
            states[f"{actor_id}:{draw_index}"] = relation
        return states

    @staticmethod
    def _attach_effect_totals(
        effects: list[dict[str, Any]],
        states: Mapping[str, Mapping[str, Any]],
    ) -> None:
        for effect in effects:
            key = f"{int(effect['actor_id'])}:{int(effect['draw_index'])}"
            relation = states.get(key, {})
            before = int(relation.get("affection") or 0)
            effect["before_affection"] = before
            effect["after_affection"] = before + int(effect.get("delta") or 0)

    def _apply_narrative_updates(
        self,
        connection: Any,
        group_id: int,
        day: str,
        narrative_plan: Mapping[str, Any],
        event_id: int,
        stamp: str,
    ) -> None:
        updates = narrative_plan.get("relation_updates")
        if not isinstance(updates, Mapping):
            return
        for key, arc in updates.items():
            if not isinstance(arc, Mapping):
                continue
            try:
                actor_text, draw_text = str(key).split(":", 1)
                actor_id, draw_index = int(actor_text), int(draw_text)
            except (TypeError, ValueError):
                continue
            payload = dict(arc)
            hook = payload.get("hook")
            if isinstance(hook, Mapping):
                persisted_hook = dict(hook)
                # A successful repair resolves the hook created by the earlier
                # negative event.  Keep that causal source instead of replacing
                # it with the repair event itself.
                if persisted_hook.get("source_event_id") in (None, ""):
                    persisted_hook["source_event_id"] = event_id
                payload["hook"] = persisted_hook
            branch = payload.get("branch")
            if isinstance(branch, Mapping):
                persisted_branch = dict(branch)
                history = [
                    dict(item)
                    for item in persisted_branch.get("history", ())
                    if isinstance(item, Mapping)
                ]
                if history and history[-1].get("event_id") in (None, ""):
                    history[-1]["event_id"] = event_id
                persisted_branch["history"] = history[-12:]
                payload["branch"] = persisted_branch
            payload["previous_event_id"] = event_id
            connection.execute(
                """UPDATE today_wife_relation_states SET narrative_json=?,updated_at=?
                   WHERE group_id=? AND day=? AND actor_id=? AND draw_index=?""",
                (json.dumps(payload, ensure_ascii=False), stamp, group_id, day, actor_id, draw_index),
            )

    @staticmethod
    def _actor_event_count(connection: Any, group_id: int, day: str, actor_id: int) -> int:
        row = connection.execute(
            """SELECT COUNT(*) AS value FROM today_wife_interaction_events
               WHERE group_id=? AND day=? AND actor_id=?""",
            (group_id, day, actor_id),
        ).fetchone()
        return int(row["value"] if row is not None else 0)

    def _replayed_interaction_outcome(self, connection: Any, row: Any) -> InteractionOutcome:
        """Return the original event without treating a retry as a new action."""

        event = self._present_event(row)
        group_id = int(event["group_id"])
        event_day = str(event["day"])
        event_actor_id = int(event["actor_id"])
        state = connection.execute(
            "SELECT * FROM today_wife_day_states WHERE group_id=? AND day=?",
            (group_id, event_day),
        ).fetchone()
        event["day_state"] = self._present_day_state(state) if state is not None else {}
        event["actor_remaining"] = max(
            0,
            MAX_INTERACTIONS - self._actor_event_count(connection, group_id, event_day, event_actor_id),
        )
        return InteractionOutcome("played", event)

    def _advance_act(
        self,
        state: Any,
        pack: ThemePack,
        prepared: Mapping[str, Any],
        count: int,
        event_id: int,
        current: datetime,
        *,
        participant_count: int | None = None,
    ) -> tuple[int, str, dict[str, Any]]:
        payload = self._json_object(state["state_json"])
        act = int(state["act"])
        route_scores = self._route_scores(payload)
        narrative_plan = prepared.get("narrative_plan")
        planned_delta = (
            narrative_plan.get("route_delta")
            if isinstance(narrative_plan, Mapping)
            and isinstance(narrative_plan.get("route_delta"), Mapping)
            else None
        )
        route_delta = (
            {str(key): int(value) for key, value in planned_delta.items()}
            if planned_delta is not None
            else self._route_score_delta(prepared)
        )
        for route_key, score in route_delta.items():
            route_scores[route_key] = int(route_scores.get(route_key, 0)) + int(score)
        route = self._dominant_route(route_scores)
        payload["route_scores"] = route_scores
        payload["turning_point"] = self._turning_point(prepared, route)
        participants = int(participant_count or payload.get("participant_count") or 0)
        if participants <= 0:
            participants = 2
        # Ordinary automatic events update relationship and route state. Only
        # a completed collective round is allowed to turn the shared act.
        payload["participant_count"] = participants
        return act, route, payload

    def _build_conclusion(self, state: Mapping[str, Any], records: list[dict[str, Any]], relation_map: Mapping[tuple[int, int], dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, Any]:
        pack = pack_for(str(state["theme_id"]))
        script = script_for(str(state["script_id"]), pack.id)
        scored = [(record, relation_map.get((int(record["actor_id"]), int(record["draw_index"])), {})) for record in records]
        active = [(record, relation) for record, relation in scored if relation and relation.get("frozen_affection") is None]
        high = max(active, key=lambda item: int(item[1].get("affection", 0)), default=None)
        reversal = max(active, key=lambda item: int(item[1].get("affection", 0)) - int(item[1].get("minimum_affection", 0)), default=None)
        assist_events = [event for event in events if event["kind"] in {"assist", "interference"}]
        response_events = [event for event in events if event["kind"] == "response"]
        repair_events = [event for event in events if event["kind"] == "chain"]
        former = [(record, relation) for record, relation in scored if relation and relation.get("frozen_affection") is not None]
        stats = {
            "participants": len(self._participants(records)), "interactions": len(events),
            "assists": sum(event["kind"] == "assist" for event in events),
            "responses": sum(event["kind"] == "response" for event in events),
            "misunderstandings": sum(event["kind"] == "interference" for event in events),
            "repairs": sum(event["kind"] == "chain" for event in events),
        }
        sections: list[dict[str, Any]] = []
        if high:
            record, relation = high
            sections.append({"kind": "高光关系", "left": record["actor_nickname"], "right": record["target_nickname"], "affection": relation["affection"], "minimum": relation["minimum_affection"], "marks": relation["marks"], "story": self._personal_ending(record, relation)})
        if reversal and reversal != high and int(reversal[1].get("affection", 0)) > int(reversal[1].get("minimum_affection", 0)):
            record, relation = reversal
            sections.append({"kind": "最大反转", "left": record["actor_nickname"], "right": record["target_nickname"], "affection": relation["affection"], "minimum": relation["minimum_affection"], "marks": relation["marks"], "story": self._personal_ending(record, relation)})
        if assist_events:
            event = max(assist_events, key=lambda item: abs(sum(int(effect.get("delta", 0)) for effect in item["effects"])))
            sections.append({"kind": "关键助攻" if event["kind"] == "assist" else "意外介入", "actor": event["actor_nickname"], "story": event["narrative"], "effects": event["effects"]})
        if response_events:
            event = max(response_events, key=lambda item: max((int(effect.get("delta", 0)) for effect in item["effects"]), default=0))
            sections.append({"kind": "回应分岔", "actor": event["actor_nickname"], "story": event["narrative"], "effects": event["effects"]})
        if repair_events:
            event = max(repair_events, key=lambda item: sum(int(effect.get("delta", 0)) for effect in item["effects"]))
            sections.append({"kind": "后续被接住", "actor": event["actor_nickname"], "story": event["narrative"], "effects": event["effects"]})
        if former:
            record, relation = max(former, key=lambda item: int(item[1].get("frozen_affection") or 0))
            sections.append({
                "kind": "留在榜上的旧关系",
                "left": record["actor_nickname"],
                "right": record["target_nickname"],
                "affection": relation["frozen_affection"],
                "minimum": relation["minimum_affection"],
                "marks": relation["marks"],
                "story": "离婚没有抹掉这段关系，它仍是今天群像里被看见的一页。",
            })
        context = StoryDirector.context(pack.id, script.id)
        conclusion_seed = ":".join((str(state.get("day") or ""), pack.id, script.id, str(len(events))))
        state_payload = state.get("state") if isinstance(state.get("state"), Mapping) else {}
        route_key = self._dominant_route(self._route_scores(state_payload))
        route_label = self._route_label(route_key)
        return {
            "title": script.title,
            "theme_title": pack.title,
            "act_title": script.acts[min(2, int(state["act"]) - 1)],
            "route_key": route_key,
            "route_label": route_label,
            "ending": StoryDirector.compose_conclusion(
                context,
                events,
                conclusion_seed,
                route_label=route_label,
                turning_point=str(state_payload.get("turning_point") or ""),
            ),
            "sections": sections[:5],
            "stats": stats,
        }

    @staticmethod
    def _personal_ending(record: Mapping[str, Any], relation: Mapping[str, Any]) -> str:
        marks = set(str(mark) for mark in relation.get("marks", ()))
        affection = int(relation.get("affection", 0))
        if "一起找回" in marks:
            return "你们把曾经差一点丢失的东西找了回来，故事因此没有停在原地。"
        if "被看见" in marks:
            return "在最该回头的时候，有人真的看见了这段关系。"
        if affection >= 45:
            return "今晚的路没有变短，只是忽然不再像一个人走。"
        if affection <= 0:
            return "今天没有走到想象中的位置，但故事还没有因此失去下一页。"
        return "有些话没有全部说完，却已经足够成为今天的共同记忆。"

    def _global_theme(self, connection: Any, day: str) -> str:
        existing = connection.execute("SELECT theme_id FROM today_wife_global_themes WHERE day=?", (day,)).fetchone()
        if existing:
            return str(existing["theme_id"])
        recent = [str(row["theme_id"]) for row in connection.execute(
            "SELECT theme_id FROM today_wife_global_themes ORDER BY day DESC LIMIT 12"
        ).fetchall()]
        # First choose the broad style equally.  Recency affects only which
        # pack inside that style is selected, so a content-rich category can
        # never crowd out the other two visual/story modes.
        packs_by_style: dict[str, list[ThemePack]] = {}
        for pack in THEME_PACKS:
            packs_by_style.setdefault(pack.style, []).append(pack)
        style = self.rng.choice(tuple(sorted(packs_by_style)))
        weighted_packs: list[ThemePack] = []
        for pack in packs_by_style[style]:
            occurrences = recent.count(pack.id)
            weighted_packs.extend([pack] * max(1, 4 - occurrences))
        pack = self.rng.choice(weighted_packs)
        connection.execute("INSERT INTO today_wife_global_themes(day,theme_id,selected_at) VALUES (?,?,?)", (day, pack.id, utc_now()))
        return pack.id

    @staticmethod
    def _initial_day_payload(script: Any) -> dict[str, Any]:
        return {
            "version": 2,
            "opening_fact": str(getattr(script, "setup_fact", "故事正在等待第一位主角。")),
            "central_question": str(getattr(script, "central_question", "今天会怎样继续？")),
            "route_scores": {"找回": 0, "错过": 0, "被接住": 0, "留到明天": 0},
            "turning_point": "",
            "participant_count": 0,
        }

    @staticmethod
    def _route_scores(payload: Any) -> dict[str, int]:
        source = payload.get("route_scores") if isinstance(payload, Mapping) else {}
        scores = {"找回": 0, "错过": 0, "被接住": 0, "留到明天": 0}
        if isinstance(source, Mapping):
            for key in scores:
                try:
                    scores[key] = int(source.get(key, 0))
                except (TypeError, ValueError):
                    continue
        return scores

    @staticmethod
    def _dominant_route(scores: Mapping[str, Any]) -> str:
        priority = ("被接住", "找回", "留到明天", "错过")
        return max(priority, key=lambda key: (int(scores.get(key, 0) or 0), -priority.index(key)))

    @staticmethod
    def _route_label(route_key: str) -> str:
        labels = {
            "找回": "把话找回来",
            "错过": "把遗憾留在这里",
            "被接住": "有人接住了回应",
            "留到明天": "把答案留到明天",
        }
        return labels.get(str(route_key), "把答案留到明天")

    @staticmethod
    def _route_score_delta(prepared: Mapping[str, Any]) -> dict[str, int]:
        intent = str(prepared.get("intent") or "")
        kind = str(prepared.get("kind") or "")
        effects = tuple(effect for effect in prepared.get("effects", ()) if isinstance(effect, Mapping))
        total = sum(int(effect.get("delta") or 0) for effect in effects)
        scores = {"找回": 0, "错过": 0, "被接住": 0, "留到明天": 0}
        if intent == "修复" or kind == "chain":
            scores["找回"] += 3
        elif intent in {"倾听", "回应"} or kind == "response":
            scores["被接住"] += 3
        elif intent == "助攻" or kind == "assist":
            scores["被接住"] += 2
        elif intent == "靠近" or kind == "direct":
            scores["留到明天"] += 2
        if total < 0 or kind == "interference":
            scores["错过"] += 3
        elif total > 0:
            scores["被接住"] += 1
        else:
            scores["留到明天"] += 1
        return scores

    def _turning_point(self, prepared: Mapping[str, Any], route_key: str) -> str:
        intent = INTENT_LABELS.get(str(prepared.get("intent") or ""), "这次选择")
        effects = tuple(effect for effect in prepared.get("effects", ()) if isinstance(effect, Mapping))
        if effects:
            focus = effects[0]
            return f"{intent}让{str(focus.get('left') or '一段关系')}和{str(focus.get('right') or '另一位群友')}走向“{self._route_label(route_key)}”"
        return f"{intent}让故事暂时走向“{self._route_label(route_key)}”"

    def _ensure_day_state(self, connection: Any, group_id: int, current: datetime) -> Any:
        day = current.date().isoformat()
        row = connection.execute("SELECT * FROM today_wife_day_states WHERE group_id=? AND day=?", (group_id, day)).fetchone()
        if row is None:
            theme_id = self._global_theme(connection, day)
            pack = pack_for(theme_id)
            script = self._select_script(pack, group_id, day)
            stamp = current.isoformat(timespec="seconds")
            connection.execute(
                """INSERT INTO today_wife_day_states (group_id,day,theme_id,script_id,state_json,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (group_id, day, pack.id, script.id, json.dumps(self._initial_day_payload(script), ensure_ascii=False), stamp, stamp),
            )
            row = connection.execute("SELECT * FROM today_wife_day_states WHERE group_id=? AND day=?", (group_id, day)).fetchone()
        return row

    @staticmethod
    def _select_script(pack: ThemePack, group_id: int, day: str) -> Any:
        digest = hashlib.blake2s(f"{pack.id}:{group_id}:{day}".encode("utf-8"), digest_size=4).digest()
        return pack.scripts[int.from_bytes(digest, "big") % len(pack.scripts)]

    @staticmethod
    def _records(connection: Any, group_id: int, day: str) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT * FROM today_wife_records WHERE group_id=? AND day=? ORDER BY drawn_at,actor_id,draw_index", (group_id, day)
        ).fetchall()
        return [{key: row[key] for key in row.keys()} for row in rows]

    def _personal_history_rows(
        self,
        connection: Any,
        group_id: int,
        user_id: int,
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            """SELECT record.*, relation.affection, relation.frozen_affection,
                      relation.marks_json, relation.minimum_affection
               FROM today_wife_records AS record
               LEFT JOIN today_wife_relation_states AS relation
                 ON relation.group_id=record.group_id AND relation.day=record.day
                AND relation.actor_id=record.actor_id AND relation.draw_index=record.draw_index
               WHERE record.group_id=? AND record.actor_id=?
               ORDER BY record.day DESC, record.draw_index DESC, record.drawn_at DESC
               LIMIT ? OFFSET ?""",
            (group_id, user_id, max(1, int(limit)), max(0, int(offset))),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            record = {key: row[key] for key in row.keys() if key not in {"affection", "frozen_affection", "marks_json", "minimum_affection"}}
            relation = {
                "affection": int(row["affection"] or 0),
                "frozen_affection": row["frozen_affection"],
                "minimum_affection": int(row["minimum_affection"] or 0),
                "marks": tuple(json.loads(str(row["marks_json"] or "[]"))),
            }
            result.append({**record, "relation": relation})
        return result

    def _group_day_summary(
        self,
        connection: Any,
        group_id: int,
        day: str,
        current_day: date,
    ) -> dict[str, Any]:
        relation_count = int(connection.execute(
            "SELECT COUNT(*) AS value FROM today_wife_records WHERE group_id=? AND day=?", (group_id, day)
        ).fetchone()["value"])
        divorced = int(connection.execute(
            "SELECT COUNT(*) AS value FROM today_wife_records WHERE group_id=? AND day=? AND status='divorced'", (group_id, day)
        ).fetchone()["value"])
        interactions = int(connection.execute(
            "SELECT COUNT(*) AS value FROM today_wife_interaction_events WHERE group_id=? AND day=?", (group_id, day)
        ).fetchone()["value"])
        state = connection.execute(
            "SELECT * FROM today_wife_day_states WHERE group_id=? AND day=?", (group_id, day)
        ).fetchone()
        presented = self._present_day_state(state) if state is not None else self._archived_day_state(day)
        return {
            "day": day,
            "relations": relation_count,
            "divorces": divorced,
            "interactions": interactions,
            "title": presented["script_title"],
            "theme_title": presented["theme_title"],
            "full_detail": date.fromisoformat(day) >= current_day - timedelta(days=GROUP_DETAIL_RETENTION_DAYS - 1),
        }

    @staticmethod
    def _archived_day_state(day: str) -> dict[str, Any]:
        return {
            "day": day,
            "theme_title": "旧日篇章",
            "script_title": "留档摘要",
            "act_title": "已落幕",
            "route_key": "留档",
            "status": "archived",
            "mechanisms": (),
        }

    @staticmethod
    def _participants(records: list[Mapping[str, Any]]) -> set[int]:
        return {int(record["actor_id"]) for record in records} | {int(record["target_id"]) for record in records}

    @staticmethod
    def _active_actor_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return one current active relationship for every opted-in player."""

        latest: dict[int, dict[str, Any]] = {}
        for record in records:
            if str(record.get("status") or "") != "active":
                continue
            actor_id = int(record["actor_id"])
            current = latest.get(actor_id)
            if current is None or int(record.get("draw_index") or 1) > int(current.get("draw_index") or 1):
                latest[actor_id] = record
        return sorted(
            latest.values(),
            key=lambda row: (str(row.get("drawn_at") or ""), int(row["actor_id"])),
        )

    @staticmethod
    def _collective_event_rank(event: Mapping[str, Any]) -> tuple[int, int, int]:
        impact = sum(
            abs(int(effect.get("delta") or 0))
            for effect in event.get("effects", ())
            if isinstance(effect, Mapping)
        )
        return int(bool(event.get("key_event"))), impact, int(event.get("event_id") or 0)

    @staticmethod
    def _latest_active(records: list[dict[str, Any]], actor_id: int) -> dict[str, Any] | None:
        matches = [record for record in records if int(record["actor_id"]) == int(actor_id) and str(record["status"]) == "active"]
        return max(matches, key=lambda item: int(item["draw_index"]), default=None)

    @staticmethod
    def _target_record(records: list[dict[str, Any]], person_id: int | None) -> dict[str, Any] | None:
        if person_id is None:
            return None
        return next((record for record in records if int(record["actor_id"]) == int(person_id) or int(record["target_id"]) == int(person_id)), None)

    @staticmethod
    def _person_name(records: list[Mapping[str, Any]], person_id: int | None) -> str:
        if person_id is None:
            return ""
        for record in records:
            if int(record["actor_id"]) == int(person_id):
                return str(record["actor_nickname"])
            if int(record["target_id"]) == int(person_id):
                return str(record["target_nickname"])
        return "这位群友"

    def _pending_relation(self, connection: Any, relation: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if relation is None:
            return None
        state = self.ensure_relation(connection, relation)
        return dict(state["pending"]) or None

    def _existing_relation_state(
        self, connection: Any, record: Mapping[str, Any] | None
    ) -> dict[str, Any] | None:
        """Read action prerequisites without creating a relation row in a prompt."""

        if record is None:
            return None
        row = connection.execute(
            """SELECT * FROM today_wife_relation_states
               WHERE group_id=? AND day=? AND actor_id=? AND draw_index=?""",
            (
                int(record["group_id"]),
                str(record["day"]),
                int(record["actor_id"]),
                int(record["draw_index"] or 1),
            ),
        ).fetchone()
        return self._present_relation_state(row) if row is not None else None

    @staticmethod
    def _rescue_success_rate(affection: int, base_rate: int) -> int:
        if affection < 0:
            return min(MAX_RESCUE_SUCCESS_RATE, int(base_rate) + NEGATIVE_AFFECTION_RESCUE_BONUS)
        return int(base_rate)

    def _weighted_record(self, items: list[tuple[dict[str, Any], int]]) -> dict[str, Any]:
        total = sum(weight for _record, weight in items)
        ticket = self.rng.randint(1, total)
        for record, weight in items:
            ticket -= weight
            if ticket <= 0:
                return record
        return items[-1][0]

    def _swing(self, kind: str, delta_bias: int = 0) -> int:
        roll = self.rng.randrange(100)
        if roll < 12:
            value = -self.rng.randint(21, 30)
        elif roll < 24:
            value = -self.rng.randint(11, 20)
        elif roll < 32:
            value = 0
        elif roll < 57:
            value = self.rng.randint(1, 10)
        elif roll < 85:
            value = self.rng.randint(11, 20)
        else:
            value = self.rng.randint(21, 30)
        return self._bounded_delta(value + int(delta_bias))

    @staticmethod
    def _bounded_delta(value: int) -> int:
        return max(-30, min(30, int(value)))

    @staticmethod
    def _effect(record: Mapping[str, Any], delta: int, mark: str, source_id: int, response: bool = False, third_party: bool = False, clear_pending: bool = False) -> dict[str, Any]:
        return {"actor_id": int(record["actor_id"]), "target_id": int(record["target_id"]), "draw_index": int(record["draw_index"]), "left": str(record["actor_nickname"]), "right": str(record["target_nickname"]), "delta": int(delta), "mark": mark, "source_id": int(source_id), "response": response, "third_party": third_party, "clear_pending": clear_pending}

    @staticmethod
    def _event_title(delta: int, role: str) -> str:
        if delta <= -6:
            return f"今日互动｜{role}失手了"
        if delta < 0:
            return f"今日互动｜{role}有点尴尬"
        if delta == 0:
            return f"今日互动｜{role}没有赶上时机"
        if delta >= 21:
            return f"今日互动｜{role}非常出色"
        if delta >= 11:
            return f"今日互动｜{role}让故事升温"
        return f"今日互动｜{role}留下了小小进展"

    @staticmethod
    def _mark_for(delta: int, kind: str) -> str:
        if delta <= -6:
            return "搞砸了"
        if delta < 0:
            return "小摩擦"
        if delta == 0:
            return "差一点"
        if delta >= 21:
            return "高光时刻"
        if delta >= 11:
            return "默契发生"
        return "有了后续"

    @staticmethod
    def _name(value: str) -> str:
        return str(value).strip()[:40] or "这位群友"

    @staticmethod
    def _json_object(raw: Any) -> dict[str, Any]:
        try:
            parsed = json.loads(str(raw or "{}"))
            return dict(parsed) if isinstance(parsed, dict) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}

    def _present_relation_state(self, row: Any) -> dict[str, Any]:
        value = {key: row[key] for key in row.keys()}
        value["marks"] = tuple(json.loads(str(value.pop("marks_json") or "[]")))
        value["pending"] = self._json_object(value.pop("pending_json") or "{}")
        value["narrative"] = self._json_object(value.pop("narrative_json", "{}") or "{}")
        return value

    def _present_event(self, row: Any) -> dict[str, Any]:
        value = {key: row[key] for key in row.keys()}
        value["effects"] = tuple(json.loads(str(value.pop("effects_json") or "[]")))
        value["marks"] = tuple(json.loads(str(value.pop("marks_json") or "[]")))
        value["narrative_plan"] = self._json_object(value.pop("narrative_json", "{}") or "{}")
        value["key_event"] = bool(value["key_event"])
        return value

    def _present_day_state(self, row: Any) -> dict[str, Any]:
        value = {key: row[key] for key in row.keys()}
        value["state"] = self._json_object(value.pop("state_json") or "{}")
        value["conclusion"] = self._json_object(value.pop("conclusion_json") or "{}")
        pack = pack_for(str(value["theme_id"]))
        script = script_for(str(value["script_id"]), pack.id)
        value.update(theme_title=pack.title, theme_style=pack.style, script_title=script.title, prop=script.prop, act_title=script.acts[max(0, min(2, int(value["act"]) - 1))], mechanisms=pack.mechanisms)
        return value
