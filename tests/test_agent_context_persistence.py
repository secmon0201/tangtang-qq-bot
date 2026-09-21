from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from bot.services.context_compaction import parse_snapshot, validate_snapshot
from bot.services.tangtang_chat import TangtangService
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


def populate(db: TangtangDb, session_id: int, count: int = 12) -> None:
    for index in range(count):
        db.commit_context_items(
            session_id, f"request-{index}", (message("user", f"turn-{index}"),),
            now=f"2026-09-21T00:00:{index:02d}+08:00",
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
