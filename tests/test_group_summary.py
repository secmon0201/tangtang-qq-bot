"""Group summary ingestion, incremental merge and prompt injection."""
from __future__ import annotations

import asyncio
import json

import nonebot

nonebot.init()

from bot.services.group_summary import (  # noqa: E402
    GroupSummaryService,
    GroupSummaryWorker,
    SummaryTopic,
    parse_daily_digest,
    parse_summary_json,
)
from bot.services.persona_profiles import ChatContext, load_personas  # noqa: E402
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
        payload = self.payload
        if '一批' in persona or '本批所有消息' in persona:
            batch = json.loads(prompt)
            payload = {'updates': [{**self.payload,
                'topic_id': batch['topics'][0]['topic_id'] if batch['topics'] else 0,
                'message_ids': [m['id'] for m in batch['messages']]}], 'ignored_message_ids': []}
        return json.dumps(payload, ensure_ascii=False), {
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


class RoutingProvider(SummaryProvider):
    def __init__(self, route: dict, payload: dict | None = None) -> None:
        super().__init__(payload)
        self.route = route
        self.route_prompts: list[str] = []

    async def generate(self, config, persona, prompt, images=()):
        if "群聊话题路由" in persona:
            self.route_prompts.append(prompt)
            return json.dumps(self.route, ensure_ascii=False), {"total_tokens": 20}
        return await super().generate(config, persona, prompt, images)


class DailyDigestProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.prompts: list[str] = []

    async def generate(self, config, persona, prompt, images=()):
        self.calls += 1
        self.prompts.append(prompt)
        return json.dumps({"summary": f"第{self.calls}段已归档"}, ensure_ascii=False), {
            "total_tokens": 20,
        }


def test_daily_digest_parsing_and_group_isolation(tmp_path):
    db = TangtangDb(tmp_path / "tangtang.db")
    with db._connect() as conn:
        conn.executemany(
            "INSERT INTO tangtang_group_messages "
            "(group_id,user_id,nickname,text,message_id,created_at) VALUES(?,?,?,?,?,?)",
            [
                (1001, 1, "甲", f"消息{index}", str(index), "2026-09-19T10:00:00+08:00")
                for index in range(6000)
            ],
        )
    db.insert_group_message(
        group_id=1002, user_id=2, nickname="乙", text="别群消息",
        message_id="other", created_at="2026-09-19T10:00:00+08:00",
    )
    provider = DailyDigestProvider()
    service = GroupSummaryService(
        db, provider, Loader(enabled_config(TANGTANG_GROUP_SUMMARY_ENABLED="true")),
        chat_id=lambda: "2026-09-20T00:00:05+08:00",
    )
    rows = db.group_daily_digest_pending(
        1001, cutoff_id=db.latest_group_message_id(1001), limit=6000
    )
    for batch_index in range(3):
        batch = rows[batch_index * 2000:(batch_index + 1) * 2000]
        assert asyncio.run(service.apply_daily_digest(1001, "2026-09-20", batch_index, batch, rows[-1]["id"]))
    assert provider.calls == 3
    assert len(db.group_daily_digest_lines(1001, limit=10)) == 3
    assert db.group_daily_digest_lines(1002, limit=10) == []
    assert db.latest_group_daily_digest_cutoff(1001) == rows[-1]["id"]
    assert parse_daily_digest('{"summary":"保留顺序"}') == "保留顺序"


def test_daily_digest_claim_is_once_per_group_and_day(tmp_path):
    db = TangtangDb(tmp_path / "tangtang.db")
    assert db.claim_group_daily_digest_run(1001, "2026-09-20", 10, now="now")
    assert not db.claim_group_daily_digest_run(1001, "2026-09-20", 20, now="later")
    assert db.claim_group_daily_digest_run(1001, "2026-09-21", 20, now="next")


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
        chat_id=lambda: "2026-09-19T10:05:00+08:00",
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
    service._chat_id = lambda: "2026-09-19T10:10:00+08:00"
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
        chat_id=lambda: "2026-09-19T10:05:00+08:00",
    )
    assert asyncio.run(GroupSummaryWorker(service).tick((1001,))) == 0
    assert [row["id"] for row in db.group_summary_pending(1001)] == [1]
    assert db.group_summary_sources(1001) == []


def test_first_run_seeds_cursor_at_tail_without_replaying_history(tmp_path):
    db = TangtangDb(tmp_path / "tangtang.db")
    for index in range(3):
        db.insert_group_message(
            group_id=1001, user_id=1, nickname="甲", text=f"旧消息{index}",
            message_id=str(index), created_at="2026-09-19T09:00:00+08:00",
        )
    seeded = db.group_summary_seed(1001, now="2026-09-19T10:00:00+08:00")
    assert seeded == 3
    assert db.group_summary_pending(1001) == []
    db.insert_group_message(
        group_id=1001, user_id=1, nickname="甲", text="上线后的新消息",
        message_id="new", created_at="2026-09-19T10:01:00+08:00",
    )
    assert [row["text"] for row in db.group_summary_pending(1001)] == ["上线后的新消息"]
    # Seeding is idempotent and never rewinds an existing cursor.
    assert db.group_summary_seed(1001, now="2026-09-19T10:02:00+08:00") == 0


def test_seed_all_covers_every_group_without_replay(tmp_path):
    db = TangtangDb(tmp_path / "tangtang.db")
    for group_id in (1001, 1002):
        for index in range(2):
            db.insert_group_message(
                group_id=group_id, user_id=1, nickname="甲", text=f"旧{group_id}-{index}",
                message_id=f"{group_id}-{index}", created_at="2026-09-19T09:00:00+08:00",
            )
    assert db.group_summary_seed_all((1001, 1002), now="2026-09-19T10:00:00+08:00") == 2
    assert db.group_summary_pending(1001) == []
    assert db.group_summary_pending(1002) == []
    # A second pass changes nothing.
    assert db.group_summary_seed_all((1001, 1002), now="2026-09-19T10:01:00+08:00") == 0


def test_switching_summary_source_reseeds_at_tail(tmp_path):
    db = TangtangDb(tmp_path / "tangtang.db")
    db.insert_group_message(
        group_id=1001, user_id=1, nickname="甲", text="旧的人格库交互",
        message_id="old", created_at="2026-09-19T09:00:00+08:00",
    )
    assert db.group_summary_seed(1001, now="2026-09-19T10:00:00+08:00") == 1
    db.insert_group_message(
        group_id=1001, user_id=1, nickname="乙", text="切换来源后的完整群消息",
        message_id="raw", created_at="2026-09-19T10:05:00+08:00",
    )
    assert db.group_summary_seed(
        1001, now="2026-09-19T10:10:00+08:00", source="raw"
    ) == 2
    # 切换来源只把游标放到新来源尾部，不追旧积压、不删除已有话题。
    assert db.group_summary_pending(1001) == []


def test_stale_messages_are_skipped_without_model_calls(tmp_path):
    db = TangtangDb(tmp_path / "tangtang.db")
    db.insert_group_message(
        group_id=1001, user_id=1, nickname="甲", text="很久以前的旧消息",
        message_id="old", created_at="2026-09-10T09:00:00+08:00",
    )
    db.group_summary_seed(1001, now="2026-09-10T10:00:00+08:00")
    # A later row (another group) shifts the next group row past the cursor.
    db.insert_group_message(
        group_id=1002, user_id=1, nickname="乙", text="别群消息",
        message_id="other", created_at="2026-09-10T09:30:00+08:00",
    )
    db.insert_group_message(
        group_id=1001, user_id=1, nickname="甲", text="窗口外的第二条旧消息",
        message_id="stale", created_at="2026-09-10T09:40:00+08:00",
    )
    db.insert_group_message(
        group_id=1001, user_id=1, nickname="甲", text="窗口内的新消息",
        message_id="fresh", created_at="2026-09-19T09:00:00+08:00",
    )
    skipped = db.group_summary_skip_older_than(1001, "2026-09-18T09:00:00+08:00")
    assert skipped > 0
    provider = SummaryProvider()
    service = GroupSummaryService(
        db, provider, Loader(enabled_config(TANGTANG_GROUP_SUMMARY_ENABLED="true")),
        chat_id=lambda: "2026-09-19T10:00:00+08:00",
    )
    assert asyncio.run(GroupSummaryWorker(service).tick((1001,))) == 1
    assert provider.calls == 1
    topics = db.group_summary_sources(1001)
    assert topics and "聚会" in topics[0]["summary"]


def test_loose_reuse_routes_semantically_related_message_to_old_topic(tmp_path):
    db = TangtangDb(tmp_path / "tangtang.db")
    db.group_summary_merge(
        1001, topic_id=None, title="聚会安排", summary="周末聚餐的时间与地点讨论。",
        keywords=("聚餐",), participants=("甲",), unresolved=("地点未定",),
        state="active", message_ids=(1,), now="2026-09-19T10:00:00+08:00",
    )
    topic_id = db.group_summary_sources(1001)[0]["topic_id"]
    provider = RoutingProvider({"topic_id": topic_id})
    service = GroupSummaryService(
        db, provider, Loader(enabled_config(TANGTANG_GROUP_SUMMARY_ENABLED="true")),
        chat_id=lambda: "2026-09-19T10:10:00+08:00",
    )
    db.insert_group_message(
        group_id=1001, user_id=1, nickname="乙", text="那我们假期去哪里碰头？",
        message_id="2", created_at="2026-09-19T10:04:00+08:00",
    )
    assert asyncio.run(GroupSummaryWorker(service).tick((1001,))) == 1
    topics = db.group_summary_sources(1001)
    assert len(topics) == 1
    assert topics[0]["topic_id"] == topic_id
    assert topics[0]["version"] == 2
    assert not provider.route_prompts  # Routing and merging now share one call.


def test_topic_cap_archives_oldest_beyond_limit(tmp_path):
    db = TangtangDb(tmp_path / "tangtang.db")
    for index in range(6):
        db.group_summary_merge(
            1001, topic_id=None, title=f"话题{index}", summary=f"摘要{index}",
            keywords=(), participants=(), unresolved=(), state="active",
            message_ids=(index + 1,), now=f"2026-09-19T10:{index:02d}:00+08:00",
        )
    archived = db.group_summary_archive_excess(1001, limit=4)
    assert archived == 2
    active = db.group_summary_sources(1001)
    assert len(active) == 4
    assert [row["title"] for row in active] == ["话题5", "话题4", "话题3", "话题2"]


def test_summary_parse_rejects_invalid_json():
    try:
        parse_summary_json("not-json")
    except ValueError:
        pass
    else:
        raise AssertionError("invalid JSON must be rejected")


def test_prompt_includes_summary_and_unlimited_history(tmp_path, monkeypatch):
    service, _sent, provider, _usage = make_service(tmp_path, monkeypatch)
    service.db.insert_group_daily_digest(
        1001, "2026-09-19", 0, 1, "周六下午三点聚会，地点未定。",
        now="2026-09-19T10:00:00+08:00",
    )
    config = enabled_config(
        TANGTANG_GROUP_SUMMARY_ENABLED="true",
        TANGTANG_GROUP_SUMMARY_INJECT_TOPICS="3",
        TANGTANG_MAX_INPUT_CHARS="0",
        TANGTANG_HISTORY_CHARS="0",
    )
    event = group_message(group_id=1001, text="聚会地点定了吗？")
    context = service._turn.set(
        ChatContext(load_personas()["denia"], 1001, 3, "1001:4", 0, 0, config.model)
    )
    prompt = service._build_prompt(event, config)
    service._turn.reset(context)
    assert "[当前群聊话题摘要" in prompt
    assert "地点未定" in prompt
    assert len(prompt) > 0


def test_group_summary_is_not_injected_for_tangtang(tmp_path, monkeypatch):
    service, _sent, _provider, _usage = make_service(tmp_path, monkeypatch)
    service.db.insert_group_daily_digest(
        1001, "2026-09-19", 0, 1, "周六下午三点聚会。",
        now="2026-09-19T10:00:00+08:00",
    )
    config = enabled_config(
        TANGTANG_GROUP_SUMMARY_ENABLED="true",
        TANGTANG_GROUP_SUMMARY_INJECT_TOPICS="3",
    )
    from bot.services.tangtang_chat import TangtangService
    event = group_message(group_id=1001, text="聚会地点定了吗？")
    # No persona context: the summary must stay out.
    prompt = service._build_prompt(event, config)
    assert "[当前群聊话题摘要" not in prompt
    context = service._turn.set(
        ChatContext(load_personas()["tangtang"], 1001, 3, "1001:4", 0, 0, config.model)
    )
    prompt = service._build_prompt(event, config)
    service._turn.reset(context)
    assert "[当前群聊话题摘要" not in prompt


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
    prompt = service._build_prompt(group_message(group_id=1001, text="记得我吗"), config)
    assert "结尾标记" in prompt
