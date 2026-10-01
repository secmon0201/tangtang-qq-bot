from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from bot.services.context_compaction import parse_snapshot, validate_snapshot
from bot.services.tangtang_chat import TangtangService, _context_compaction_due
from bot.services.tangtang_db import TangtangDb
from tests.test_tangtang_chat import enabled_config


def message(role: str, content: str) -> dict:
    return {"type": "message", "role": role, "content": content}


def session(db: TangtangDb, group_id: int = 1001, user_id: int = 2001) -> int:
    return db.ensure_context_session(
        group_id=group_id,
        user_id=user_id,
        layout_version="agent-context-v2",
        persona_version="persona-v1",
        tool_version="tools-v1",
        now="2026-09-21T00:00:00+08:00",
    )


def test_confirmed_context_is_idempotent_and_audit_is_inactive(tmp_path: Path):
    db = TangtangDb(tmp_path / "context.db")
    session_id = session(db)
    items = (message("user", "hello"), message("assistant", "world"))
    first = db.commit_context_items(session_id, "request-1", items, now="now")
    second = db.commit_context_items(session_id, "request-1", items, now="later")
    assert first == second
    db.commit_context_items(
        session_id, "request-2", (message("user", "not delivered"),),
        now="later", delivery_status="audit",
    )
    snapshot, active = db.context_window(session_id)
    assert snapshot is None
    assert active == items
    with db._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM chat_context_turns").fetchone()[0] == 3
        assert db.context_session(session_id)["last_confirmed_turn_id"] == first[-1]


def test_state_action_execution_key_is_at_most_once(tmp_path: Path):
    db = TangtangDb(tmp_path / "context.db")
    assert db.claim_action_execution(
        "request-1:roulette_load:0",
        action="roulette_load",
        state_version="state-a",
        now="now",
    )
    assert not db.claim_action_execution(
        "request-1:roulette_load:0",
        action="roulette_load",
        state_version="state-a",
        now="later",
    )
    db.finish_action_execution(
        "request-1:roulette_load:0", status="delivered", now="later"
    )
    with db._connect() as conn:
        row = conn.execute(
            "SELECT status,state_version FROM agent_action_executions WHERE execution_key=?",
            ("request-1:roulette_load:0",),
        ).fetchone()
    assert dict(row) == {"status": "delivered", "state_version": "state-a"}


def test_context_scope_isolated_by_group_user_and_persona_database(tmp_path: Path):
    tangtang = TangtangDb(tmp_path / "tangtang.db")
    denia = TangtangDb(tmp_path / "denia-history.db")
    scopes = [session(tangtang, 1001, 2001), session(tangtang, 1002, 2001),
              session(tangtang, 1001, 2002), session(denia, 1001, 2001)]
    for index, (db, session_id) in enumerate(
        ((tangtang, scopes[0]), (tangtang, scopes[1]), (tangtang, scopes[2]),
         (denia, scopes[3]))
    ):
        db.commit_context_items(
            session_id, f"r-{index}", (message("user", f"scope-{index}"),), now="now"
        )
    assert tangtang.context_window(scopes[0])[1][0]["content"] == "scope-0"
    assert tangtang.context_window(scopes[1])[1][0]["content"] == "scope-1"
    assert tangtang.context_window(scopes[2])[1][0]["content"] == "scope-2"
    assert denia.context_window(scopes[3])[1][0]["content"] == "scope-3"


def test_private_personal_session_is_explicit_and_separate_from_group_spines(
    tmp_path: Path,
):
    db = TangtangDb(tmp_path / "context.db")
    private = db.ensure_context_session(
        group_id=0,
        user_id=2001,
        layout_version="agent-context-v2",
        persona_version="persona-v1",
        tool_version="tools-v1",
        session_scope="private_personal",
        now="now",
    )
    group = db.ensure_group_context_spine(
        group_id=1001,
        layout_version="agent-context-v2",
        persona_version="persona-v1",
        tool_version="tools-v1",
        stable_prefix_hash="a" * 64,
        now="now",
    )

    assert private != group
    assert db.context_session(private)["session_scope"] == "private_personal"
    assert db.context_session(group)["session_scope"] == "group_spine"


def test_context_version_change_starts_fresh_window_without_deleting_raw_turns(
    tmp_path: Path,
):
    db = TangtangDb(tmp_path / "context.db")
    session_id = session(db)
    db.commit_context_items(
        session_id,
        "old-request",
        (message("user", "old-layout"), message("assistant", "old-answer")),
        now="old",
    )
    assert db.context_window(session_id)[1]

    same_id = db.ensure_context_session(
        group_id=1001,
        user_id=2001,
        layout_version="agent-context-v3",
        persona_version="persona-v1",
        tool_version="tools-v1",
        now="new",
    )

    assert same_id == session_id
    assert db.context_window(session_id) == (None, ())
    with db._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM chat_context_turns WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0] == 2
    db.commit_context_items(
        session_id,
        "new-request",
        (message("user", "new-layout"),),
        now="new",
    )
    assert db.context_window(session_id)[1] == (message("user", "new-layout"),)


def test_group_spine_keeps_legacy_personal_sessions_historical(tmp_path: Path):
    db = TangtangDb(tmp_path / "context.db")
    legacy = session(db, group_id=1001, user_id=2001)
    spine = db.ensure_group_context_spine(
        group_id=1001,
        layout_version="agent-context-v2",
        persona_version="persona-v1",
        tool_version="tools-v1",
        stable_prefix_hash="a" * 64,
        now="now",
    )

    assert legacy != spine
    assert db.context_session(legacy)["session_scope"] == "legacy_personal"
    assert db.context_session(spine)["session_scope"] == "group_spine"
    assert db.context_session(spine)["context_epoch"] == 0

    same_spine = db.ensure_group_context_spine(
        group_id=1001,
        layout_version="agent-context-v2",
        persona_version="persona-v1",
        tool_version="tools-v1",
        stable_prefix_hash="b" * 64,
        now="later",
    )
    assert same_spine == spine
    assert db.context_session(spine)["context_epoch"] == 1


def test_hard_budget_keeps_complete_recent_rounds_without_deleting_history(tmp_path: Path):
    db = TangtangDb(tmp_path / "context.db")
    session_id = session(db)
    for index in range(4):
        db.commit_context_items(
            session_id,
            f"round-{index}",
            (
                message("user", f"question-{index}-" + "x" * 100),
                {"type": "tool_call", "call_id": f"call-{index}", "name": "search", "arguments": "{}"},
                {"type": "tool_result", "call_id": f"call-{index}", "output": "ok"},
                message("assistant", f"answer-{index}-" + "y" * 100),
            ),
            now=f"round-{index}",
        )

    _snapshot, items, budget = db.context_window_with_budget(
        session_id, soft_chars=450, hard_chars=700
    )

    assert budget["budget_action"] == "hard_trimmed"
    assert [item["type"] for item in items] == [
        "message", "tool_call", "tool_result", "message"
    ]
    assert items[0]["content"].startswith("question-3")
    with db._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM chat_context_turns").fetchone()[0] == 16


def test_group_context_cursor_advances_only_with_confirmed_delivery(tmp_path: Path):
    db = TangtangDb(tmp_path / "context.db")
    session_id = session(db)
    for index in range(35):
        db.insert_group_message(
            group_id=1001,
            user_id=3000 + index,
            nickname=f"member-{index}",
            text=f"group-{index}",
            message_id=f"message-{index}",
            created_at=f"time-{index}",
        )
    baseline = db.group_context_messages(1001, limit=30)
    assert [row["text"] for row in baseline] == [f"group-{i}" for i in range(5, 35)]
    baseline_cursor = baseline[-1]["id"]

    db.commit_context_items(
        session_id,
        "request-1",
        (message("user", "delivered"),),
        now="now",
        group_context_cursor_id=baseline_cursor,
    )
    assert db.context_session(session_id)["group_context_cursor_id"] == baseline_cursor

    db.insert_group_message(
        group_id=1001,
        user_id=4001,
        nickname="new-member",
        text="new-group-message",
        message_id="new-message",
        created_at="later",
    )
    delta = db.group_context_messages(1001, after_id=baseline_cursor, limit=30)
    delta_cursor = delta[-1]["id"]
    db.commit_context_items(
        session_id,
        "request-2",
        (message("user", "not delivered"),),
        now="later",
        delivery_status="audit",
        group_context_cursor_id=delta_cursor,
    )
    assert db.context_session(session_id)["group_context_cursor_id"] == baseline_cursor

    db.ensure_context_session(
        group_id=1001,
        user_id=2001,
        layout_version="agent-context-v3",
        persona_version="persona-v1",
        tool_version="tools-v1",
        now="new-layout",
    )
    assert db.context_session(session_id)["group_context_cursor_id"] == 0


def populate(db: TangtangDb, session_id: int, count: int = 12) -> None:
    for index in range(count):
        db.commit_context_items(
            session_id, f"request-{index}", (message("user", f"turn-{index}"),),
            now=f"2026-09-21T00:00:{index:02d}+08:00",
        )


def populate_rounds(
    db: TangtangDb, session_id: int, count: int, *, start: int = 0
) -> None:
    for index in range(start, start + count):
        db.commit_context_items(
            session_id,
            f"round-{index}",
            (
                message("user", f"question-{index}"),
                {"type": "tool_call", "call_id": f"call-{index}", "name": "search", "arguments": "{}"},
                {"type": "tool_result", "call_id": f"call-{index}", "output": "ok"},
                message("assistant", f"answer-{index}"),
            ),
            now=f"round-{index}",
        )


def test_generation_window_appends_thirty_to_fifty_complete_rounds(tmp_path: Path):
    db = TangtangDb(tmp_path / "context.db")
    session_id = session(db)
    populate_rounds(db, session_id, 30)
    first = db.context_window(session_id)[1]
    assert len(first) == 120
    assert db.uncompacted_context_round_count(session_id) == 30

    populate_rounds(db, session_id, 20, start=30)
    second = db.context_window(session_id)[1]
    assert second[: len(first)] == first
    assert len(second) == 200
    assert db.uncompacted_context_round_count(session_id) == 50


def test_round_compaction_rebases_to_latest_thirty_without_splitting_requests(
    tmp_path: Path,
):
    db = TangtangDb(tmp_path / "context.db")
    session_id = session(db)
    populate_rounds(db, session_id, 51)
    source = db.compaction_source(session_id, keep_recent_rounds=30)
    assert source is not None
    assert len(source["source"]["turns"]) == 21 * 4
    assert source["source"]["turns"][-1]["item"]["content"] == "answer-20"

    job_id = db.enqueue_compaction_job(
        session_id, source["source_hash"], source["cutoff_turn_id"], now="now"
    )
    assert db.claim_compaction_job(
        job_id, owner="worker", now_epoch=100.0, lease_seconds=60, now="now"
    )
    loaded_source = db.compaction_job_source(job_id)
    assert db.complete_compaction_job(
        job_id,
        owner="worker",
        summary=valid_summary(loaded_source, 1),
        now="done",
    ) == 1

    snapshot, recent = db.context_window(session_id)
    assert snapshot is not None
    assert len(recent) == 30 * 4
    assert recent[0]["content"] == "question-21"
    assert recent[-1]["content"] == "answer-50"
    with db._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM chat_context_turns WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0] == 51 * 4


def test_compaction_threshold_stays_inside_thirty_to_fifty_round_band():
    assert not _context_compaction_due(
        30, prompt_tokens=50_000, cache_status="unsupported", serialized_chars=50_000
    )
    assert not _context_compaction_due(
        50, prompt_tokens=1_000, cache_status="reported", serialized_chars=50_000
    )
    assert _context_compaction_due(
        31, prompt_tokens=12_000, cache_status="reported", serialized_chars=1_000
    )
    assert _context_compaction_due(
        31, prompt_tokens=0, cache_status="unsupported", serialized_chars=12_000
    )
    assert _context_compaction_due(
        51, prompt_tokens=1_000, cache_status="reported", serialized_chars=1_000
    )


def valid_summary(source: dict, revision: int) -> dict:
    return {
        "facts": ["fact"],
        "commitments": [],
        "unresolved": ["open"],
        "topic_progress": ["progress"],
        "source_turn_ids": [row["id"] for row in source["turns"]],
        "scope": "session",
        "revision": revision,
    }


def test_compaction_lease_completion_and_raw_retention(tmp_path: Path):
    db = TangtangDb(tmp_path / "context.db")
    session_id = session(db)
    populate(db, session_id)
    source = db.compaction_source(session_id, keep_recent=4)
    assert source and len(source["source"]["turns"]) == 8
    job_id = db.enqueue_compaction_job(
        session_id, source["source_hash"], source["cutoff_turn_id"], now="now"
    )
    claimed = db.claim_compaction_job(
        job_id, owner="worker-a", now_epoch=100.0, lease_seconds=60, now="now"
    )
    assert claimed and claimed["attempts"] == 1
    assert db.claim_compaction_job(
        job_id, owner="worker-b", now_epoch=120.0, lease_seconds=60, now="now"
    ) is None
    loaded_source = db.compaction_job_source(job_id)
    summary = valid_summary(loaded_source, 1)
    assert db.complete_compaction_job(
        job_id, owner="worker-a", summary=summary, now="done"
    ) == 1
    snapshot, recent = db.context_window(session_id, keep_recent=4)
    assert snapshot["summary"] == summary
    assert [item["content"] for item in recent] == [f"turn-{i}" for i in range(8, 12)]
    with db._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM chat_context_turns WHERE session_id = ?", (session_id,)
        ).fetchone()[0] == 12


def test_compaction_failure_keeps_cursor_and_allows_expired_lease_recovery(tmp_path: Path):
    db = TangtangDb(tmp_path / "context.db")
    session_id = session(db)
    populate(db, session_id)
    source = db.compaction_source(session_id, keep_recent=4)
    job_id = db.enqueue_compaction_job(
        session_id, source["source_hash"], source["cutoff_turn_id"], now="now"
    )
    assert db.claim_compaction_job(
        job_id, owner="crashed", now_epoch=100.0, lease_seconds=10, now="now"
    )
    recovered = db.claim_compaction_job(
        job_id, owner="recovered", now_epoch=111.0, lease_seconds=10, now="later"
    )
    assert recovered and recovered["attempts"] == 2
    db.fail_compaction_job(
        job_id, owner="recovered", reason="invalid", now_epoch=111.0, now="failed"
    )
    assert db.context_session(session_id)["snapshot_version"] == 0
    assert db.context_window(session_id)[0] is None
    assert db.claim_compaction_job(
        job_id, owner="early", now_epoch=112.0, lease_seconds=10, now="early"
    ) is None
    assert db.claim_compaction_job(
        job_id, owner="retry", now_epoch=4000.0, lease_seconds=10, now="retry"
    )


def test_snapshot_validation_rejects_wrong_source_and_preserves_old_shape():
    summary = {
        "facts": [], "commitments": [], "unresolved": [], "topic_progress": [],
        "source_turn_ids": [1, 2], "scope": "session", "revision": 3,
    }
    assert validate_snapshot(summary, source_turn_ids=(1, 2), revision=3) == summary
    assert parse_snapshot(json.dumps(summary), source_turn_ids=(1, 2), revision=3) == summary
    with pytest.raises(ValueError, match="source_turn_ids"):
        validate_snapshot(summary, source_turn_ids=(1, 3), revision=3)
    with pytest.raises(ValueError, match="fields"):
        validate_snapshot({**summary, "extra": True}, source_turn_ids=(1, 2), revision=3)
    with pytest.raises(ValueError, match="characters"):
        validate_snapshot(
            {**summary, "facts": ["x" * 100]},
            source_turn_ids=(1, 2),
            revision=3,
            max_chars=80,
        )


class CompactionProvider:
    def __init__(self, payload: dict):
        self.payload = payload
        self.config = None

    async def generate(self, config, persona, prompt, images=()):
        self.config = config
        return json.dumps(self.payload, ensure_ascii=False), {
            "prompt_tokens": 20, "completion_tokens": 10, "cache_status": "unsupported",
        }


def test_compaction_worker_uses_bounded_model_contract(tmp_path: Path):
    db = TangtangDb(tmp_path / "context.db")
    session_id = session(db)
    populate(db, session_id)
    source = db.compaction_source(session_id, keep_recent=4)
    job_id = db.enqueue_compaction_job(
        session_id, source["source_hash"], source["cutoff_turn_id"], now="now"
    )
    payload = valid_summary(source["source"], 1)
    provider = CompactionProvider(payload)
    service = TangtangService(
        db=db, provider=provider, resource_dir=tmp_path / "resources",
        usage_dir=tmp_path / "usage",
    )
    asyncio.run(service._run_compaction_job(db, enabled_config(), job_id))
    assert provider.config.reasoning_effort == "low"
    assert provider.config.timeout_seconds == 45
    assert provider.config.max_output_tokens == 1600
    assert db.context_session(session_id)["snapshot_version"] == 1
