"""Read-only state fingerprints for explicit mutating Agent tools."""

from __future__ import annotations

import hashlib
import json
from typing import Any


WIFE_STATE_ACTIONS = frozenset({"wife_draw", "wife_take", "wife_divorce"})
MINI_GAME_STATE_ACTIONS = frozenset({
    "roulette_load", "roulette_fire", "bomb_load", "bomb_pass",
    "idiom_bomb_load", "idiom_bomb_pass", "dice_start", "guess_start",
    "guess_submit",
})
STATE_ACTIONS = WIFE_STATE_ACTIONS | MINI_GAME_STATE_ACTIONS


def action_state_versions(database: Any, group_id: int) -> dict[str, str]:
    """Return privacy-safe hashes; no chat text or identity leaves this module."""

    with database.connect() as connection:
        session = connection.execute(
            "SELECT session_id,game_type,status,ends_at,state_json FROM mini_game_sessions "
            "WHERE group_id=? AND status='active' ORDER BY session_id DESC LIMIT 1",
            (int(group_id),),
        ).fetchone()
        participants = []
        if session is not None:
            participants = [
                tuple(row)
                for row in connection.execute(
                    "SELECT user_id,action_order,dice_value,pass_count FROM mini_game_participants "
                    "WHERE session_id=? ORDER BY user_id",
                    (int(session["session_id"]),),
                )
            ]
        wife_day = connection.execute(
            "SELECT MAX(day) AS day FROM today_wife_records WHERE group_id=?",
            (int(group_id),),
        ).fetchone()
        day = str(wife_day["day"] or "") if wife_day is not None else ""
        wife_rows = [
            tuple(row)
            for row in connection.execute(
                "SELECT actor_id,draw_index,target_id,status,drawn_at,COALESCE(divorced_at,'') "
                "FROM today_wife_records WHERE group_id=? AND day=? "
                "ORDER BY actor_id,draw_index",
                (int(group_id), day),
            )
        ] if day else []
        day_state = connection.execute(
            "SELECT status,updated_at,state_json FROM today_wife_day_states "
            "WHERE group_id=? AND day=?",
            (int(group_id), day),
        ).fetchone() if day else None

    mini_payload = {
        "session": dict(session) if session is not None else None,
        "participants": participants,
    }
    wife_payload = {
        "day": day,
        "records": wife_rows,
        "day_state": dict(day_state) if day_state is not None else None,
    }
    mini_version = _hash(mini_payload)
    wife_version = _hash(wife_payload)
    return {
        **{action: mini_version for action in MINI_GAME_STATE_ACTIONS},
        **{action: wife_version for action in WIFE_STATE_ACTIONS},
    }


def _hash(value: Any) -> str:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


__all__ = [
    "MINI_GAME_STATE_ACTIONS",
    "STATE_ACTIONS",
    "WIFE_STATE_ACTIONS",
    "action_state_versions",
]
