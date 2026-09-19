"""Group summary ingestion, incremental merge and prompt injection."""
from __future__ import annotations

import asyncio
import json

import nonebot

nonebot.init()

from bot.services.group_summary import (  # noqa: E402
    GroupSummaryService,
    GroupSummaryWorker,
    SummaryMerge,
    SummaryTopic,
    parse_summary_json,
)
from bot.services.tangtang_chat import TangtangConfig, TangtangService  # noqa: E402
from bot.services.tangtang_db import TangtangDb  # noqa: E402
from tests.test_tangtang_chat import enabled_config, group_message, make_service  # noqa: E402


class SummaryProvider:
    def __init__(self, payload: dict | None = None) -> None:
        self.payload = payload or {
            "state": "active",
            "title": "聚会安排",
            "summary": "周六下午三点聚会，报名进行中，地点尚未确定。",
            "keywords": ["聚会", "周六", "地点"],
            "participants": ["甲", "乙"],
            "unresolved": ["地点未定"],
        }
        self.calls = 0

    async def generate(self, config, persona, prompt, images=()):
        self.calls += 1
        return json.dumps(self.payload, ensure_ascii=False), {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "reasoning_tokens": 0,
            "total_tokens": 150,
            "latency_ms": 100,
        }


class Loader:
    def __init__(self, config: TangtangConfig) -> None:
        self.config = config

    def load(self) -> TangtangConfig:
        return self.config


def test_summary_merge_is_incremental_and_advances_cursor(tmp_path):
    db = TangtangDb(tmp_path / "tangtang.db")
    db.insert_group_message(
        group_id=1001, user_id=1, nickname="甲", text="周六下午三点聚会，地点待定。",
        message_id="1", created_at="2026-09-19T10:00:00+08:00",
    )
    db.insert_group_message(
        group_id=1001, user_id=2, nickname="乙", text="聚会我先报名。",
        message_id="2", created_at="2026-09-19T10:01:00+08:00",
    )
    provider = SummaryProvider()
    service = GroupSummaryService(
        db, provider, Loader(enabled_config(TANGTANG_GROUP_SUMMARY_ENABLED="true")),
        chat_id=lambda: "2026-09-19T10:02:00+08:00",
    )
    worker = GroupSummaryWorker(service, batch_messages=50)

    first = asyncio.run(worker.tick((1001,)))
    assert first == 2
    topics = db.group_summary_sources(1001)
    assert len(topics) == 1
    assert topics[0]["version"] == 1
    assert "地点未定" in topics[0]["unresolved"]
    assert db.group_summary_pending(1001) == []

    # No new messages: the worker must not call the model again.
    assert asyncio.run(worker.tick((1001,))) == 0
    assert provider.calls == 1

    db.insert_group_message(
        group_id=1001, user_id=1, nickname="甲", text="地点定在公园门口。",
        message_id="3", created_at="2026-09-19T10:05:00+08:00",
    )
    provider.payload = {
        "state": "active",
        "title": "聚会安排",
        "summary": "周六下午三点聚会，地点已定为公园门口。",
        "keywords": ["聚会", "公园"],
        "participants": ["甲", "乙"],
        "unresolved": [],
    }
    assert asyncio.run(worker.tick((1001,))) == 1
    assert provider.calls == 2
    topic = db.group_summary_sources(1001)[0]
    assert topic["version"] == 2
    with db._connect() as conn:
        versions = [dict(row) for row in conn.execute(
            "SELECT version FROM group_summary_versions WHERE topic_id=? ORDER BY version",
            (topic["topic_id"],),
        )]
    assert [row["version"] for row in versions] == [1, 2]
    assert db.group_summary_pending(1001) == []


def test_summary_failure_does_not_advance_cursor(tmp_path):
    class Broken(SummaryProvider):
        async def generate(self, config, persona, prompt, images=()):
            raise RuntimeError("provider down")

    db = TangtangDb(tmp_path / "tangtang.db")
    db.insert_group_message(
        group_id=1001, user_id=1, nickname="甲", text="这个话题还没处理。",
        message_id="1", created_at="2026-09-19T10:00:00+08:00",
    )
    service = GroupSummaryService(
        db, Broken(), Loader(enabled_config(TANGTANG_GROUP_SUMMARY_ENABLED="true")),
        chat_id=lambda: "2026-09-19T10:02:00+08:00",
    )
    assert asyncio.run(GroupSummaryWorker(service).tick((1001,))) == 0
    assert [row["id"] for row in db.group_summary_pending(1001)] == [1]
    assert db.group_summary_sources(1001) == []


def test_topic_routing_reuses_existing_topic(tmp_path):
    db = TangtangDb(tmp_path / "tangtang.db")
    db.group_summary_merge(
        1001,
        topic_id=None,
        title="聚会安排",
        summary="周六下午三点聚会。",
        keywords=("聚会", "周六"),
        participants=("甲",),
        unresolved=("地点未定",),
        state="active",
        message_ids=(1,),
        now="2026-09-19T10:00:00+08:00",
    )
    service = GroupSummaryService(db, SummaryProvider(), Loader(enabled_config()), chat_id=lambda: "")
    rows = [
        {"id": 2, "nickname": "乙", "text": "聚会地点还没定吗？"},
        {"id": 3, "nickname": "甲", "text": "周末天气不错。"},
    ]
    plan = service.merge_plan(1001, rows)
    assert plan[0].topic.topic_id is not None
    assert plan[1].topic.topic_id is None


def test_unrelated_new_messages_do_not_share_a_topic(tmp_path):
    db = TangtangDb(tmp_path / "tangtang.db")
    service = GroupSummaryService(db, SummaryProvider(), Loader(enabled_config()), chat_id=lambda: "")
    rows = [
        {"id": 1, "nickname": "甲", "text": "今晚谁打游戏？"},
        {"id": 2, "nickname": "乙", "text": "我妈今天做了红烧肉。"},
    ]
    plan = service.merge_plan(1001, rows)
    assert [item.topic.topic_id for item in plan] == [None, None]


def test_summary_parse_rejects_invalid_json():
    try:
        parse_summary_json("not-json")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid JSON must be rejected")


def test_prompt_includes_summary_and_unlimited_history(tmp_path, monkeypatch):
    service, _sent, provider, _usage = make_service(tmp_path, monkeypatch)
    service.db.group_summary_merge(
        1001,
        topic_id=None,
        title="聚会安排",
        summary="周六下午三点聚会，地点未定。",
        keywords=("聚会",),
        participants=("甲",),
        unresolved=("地点未定",),
        state="active",
        message_ids=(1,),
        now="2026-09-19T10:00:00+08:00",
    )
    config = enabled_config(
        TANGTANG_GROUP_SUMMARY_ENABLED="true",
        TANGTANG_GROUP_SUMMARY_INJECT_TOPICS="3",
        TANGTANG_MAX_INPUT_CHARS="0",
        TANGTANG_HISTORY_CHARS="0",
    )
    event = group_message(group_id=1001, text="聚会地点定了吗？")
    prompt = service._build_prompt(event, config)
    assert "[当前群聊话题摘要" in prompt
    assert "地点未定" in prompt
    assert len(prompt) > 0


def test_unlimited_history_keeps_the_entire_oldest_message(tmp_path, monkeypatch):
    service, _sent, _provider, _usage = make_service(tmp_path, monkeypatch)
    oldest = "开头" + "甲" * 12000 + "结尾标记"
    service.db.insert_call(
        group_id=1001,
        user_id=3,
        message_id="1",
        call_text=oldest,
        reply_text="收到",
        reply_kind="model",
        mode="d",
        created_at="2026-09-19T10:00:00+08:00",
    )
    history = service.db.model_reply_lines(3, 1001, 10, None)
    assert oldest in history
    config = enabled_config(TANGTANG_MAX_INPUT_CHARS="0", TANGTANG_HISTORY_CHARS="0")
    prompt = service._build_prompt(group_message(group_id=1001, text="继续"), config)
    assert "结尾标记" in prompt
