# Explicit legacy-layout compatibility contracts; new defaults are tested in test_cache_spine.py.
import asyncio
import json
import sqlite3
import time
from dataclasses import asdict, replace
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest

from tangtang_harness.business.asoul import CALENDAR_CACHE_KEY, CALENDAR_URL, ScheduleItem
from tangtang_harness.config import HarnessConfig
from tangtang_harness.runtime import Runtime
from tangtang_harness.store import Store
from tangtang_harness.topics import PersonaTopics, WW_MENU, collect_topics
from tangtang_harness.types import InboundEvent


def event(group=102, user=101):
    return InboundEvent("topic-test", 103, user, group, "娅娅鸣潮最近更新了吗")


def news(identity=42, title="鸣潮版本更新", body="维护公告公布新增活动"):
    return {"url": f"https://mc.kurogames.com/main/news/detail/{identity}", "title": title,
            "body": body, "published_at": "2026-10-01"}


def test_fixed_sources_strip_html_and_replace_same_url(tmp_path):
    topics = PersonaTopics(Store(tmp_path))
    with pytest.raises(ValueError, match="unapproved"):
        topics.ingest("任意网址", [news()], 100_000)
    assert topics.ingest("鸣潮官网", [{**news(), "url": "https://example.invalid/news"}], 100_000) == 0
    assert topics.ingest("鸣潮官网", [news(body="<p>维护公告&amp;活动</p>")], 100_000) == 1
    row = topics.rows("鸣潮官网")[0]
    assert row["body"] == "维护公告&活动"
    identity = row["topic_id"]
    assert topics.ingest("鸣潮官网", [news(body="调整维护公告内容")], 100_001) == 1
    assert len(topics.rows("鸣潮官网")) == 1
    assert topics.rows("鸣潮官网")[0]["topic_id"] == identity
    assert topics.rows("鸣潮官网")[0]["content_hash"] != row["content_hash"]


def test_cache_selection_is_local_fresh_and_links_follow_confirmed_overlap(tmp_path, monkeypatch):
    topics = PersonaTopics(Store(tmp_path))
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: pytest.fail("selection attempted network"))
    topics.ingest("鸣潮官网", [news()], 100_000)
    selected = topics.select(event(), "鸣潮最近更新", now=100_001)
    assert selected.source == "鸣潮官网" and selected.url not in selected.text
    assert not topics.delivered(event(), "嗯嗯知道啦", topic_id=selected.topic_id, now=100_002)
    assert selected.url not in topics.select(event(), "来源呢", now=100_003).text
    assert topics.delivered(event(), "维护公告已经发布了", topic_id=selected.topic_id, now=100_004)
    assert selected.url in topics.select(event(), "哪里看到的", now=100_005).text
    expired = topics.select(event(), "鸣潮今天有什么新闻", now=200_000)
    assert not expired.topic_id and "缺少有效" in expired.text and topics.refresh_requested
    # An exact source for an earlier answer remains available after cache expiry.
    assert selected.url in topics.select(event(), "原文链接", now=200_000).text


def test_provenance_and_proactive_reuse_are_scoped_by_persona_session(tmp_path):
    topics = PersonaTopics(Store(tmp_path))
    topics.ingest("鸣潮官网", [news()], 100_000)
    selected = topics.select(event(), "鸣潮更新", now=100_001)
    assert topics.delivered(event(), "维护公告公布了", topic_id=selected.topic_id, now=100_002)
    for other, persona in ((event(104), "denia"), (event(None), "denia"), (event(), "tangtang")):
        assert selected.url not in topics.select(other, "来源", persona=persona, now=100_003).text
    assert not topics.select(event(), "鸣潮更新", proactive=True, now=100_004).topic_id
    assert topics.select(event(104), "鸣潮更新", proactive=True, now=100_004).topic_id
    topics.ingest("鸣潮官网", [news()], 200_000)
    assert topics.select(event(), "鸣潮更新", proactive=True, now=200_001).topic_id


def test_calendar_selection_and_unrelated_query_do_not_mix_sources(tmp_path):
    topics = PersonaTopics(Store(tmp_path))
    topics.ingest("鸣潮官网", [news()], 100_000)
    topics.ingest("枝江日历", [{"url": CALENDAR_URL + "#live", "title": "嘉然今晚直播",
                               "body": "嘉然今晚八点直播", "published_at": "2026-10-01T20:00:00"}], 100_000)
    selected = topics.select(event(), "嘉然今天直播吗", now=100_001)
    assert selected.source == "枝江日历" and "鸣潮" not in selected.text
    assert topics.select(event(), "吃饭了吗", now=100_001).text == ""


def test_delivered_source_keeps_request_snapshot_when_catalog_refreshes(tmp_path):
    topics = PersonaTopics(Store(tmp_path))
    topics.ingest("鸣潮官网", [news(title="先前资料", body="首轮补偿名单已公布")], 100_000)
    frozen = topics.select(event(), "鸣潮最近更新", now=100_001)
    topics.ingest("鸣潮官网", [news(title="刷新后的资料", body="活动时间改为明天上午")], 100_002)
    assert topics.rows("鸣潮官网")[0]["content_hash"] != frozen.content_hash
    assert topics.delivered(event(), "首轮补偿名单已公布哦", topic_id=frozen.topic_id,
                            selection=asdict(frozen), now=100_003)
    source = topics.select(event(), "出处呢", now=100_004)
    assert source.title == "先前资料" and source.content_hash == frozen.content_hash
    assert frozen.url in source.text
    # Merely echoing a source instruction is not actual topic-content evidence.
    assert not topics.delivered(event(), "固定来源的临时素材", topic_id=frozen.topic_id,
                                selection=asdict(frozen), now=100_005)


def test_refresh_intervals_keep_source_failure_in_background(tmp_path):
    topics = PersonaTopics(Store(tmp_path))
    assert topics.refresh_due(100_000)
    topics.store.set_setting("topics:refresh", {"attempted_at": 100_000, "errors": {}})
    assert not topics.refresh_due(100_001) and topics.refresh_due(121_600)
    topics.store.set_setting("topics:refresh", {"attempted_at": 100_000, "errors": {"鸣潮官网": "ReadTimeout"}})
    assert not topics.refresh_due(100_299) and topics.refresh_due(100_300)


@pytest.mark.asyncio
async def test_collect_runs_both_fixed_sources_and_retains_true_calendar_fetch_time(tmp_path):
    topics = PersonaTopics(Store(tmp_path))
    now = time.time()
    calendar_at = now - 90_000
    item = ScheduleItem(datetime.now(ZoneInfo("Asia/Shanghai")), ("嘉然",), "嘉然直播", "直播")
    class Calendar:
        timezone = ZoneInfo("Asia/Shanghai")
        db = SimpleNamespace(asoul_state=lambda key, default: {"fetched_at": calendar_at})
        async def schedule_for_days(self, first, last):
            assert (last - first).days == 7
            return {first: [item]}
        def render_schedule(self, day, title, items):
            return "嘉然直播八点开始"
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"article": [
        {"articleId": "42", "articleTitle": "鸣潮公告", "articleContent": "维护公告公布活动", "startTime": "2026-10-01"},
        {"articleId": "not-an-id", "articleTitle": "忽略"}]})))
    async with client:
        result = await collect_topics(topics, Calendar(), client=client)
    assert result["counts"] == {"鸣潮官网": 1, "枝江日历": 1}
    assert result["errors"] == {"枝江日历": "calendar_cache_expired"}
    assert topics.rows("枝江日历")[0]["fetched_at"] == calendar_at
    assert "缺少有效" in topics.select(event(), "嘉然今天直播吗", now=now).text
    assert "维护公告" in topics.select(event(), "鸣潮更新", now=now).text
    assert result["model_calls"] == result["qq_writes"] == 0


@pytest.mark.asyncio
async def test_one_topic_source_failure_does_not_discard_other_source(tmp_path):
    topics = PersonaTopics(Store(tmp_path))
    item = ScheduleItem(datetime.now(ZoneInfo("Asia/Shanghai")), ("嘉然",), "嘉然直播", "直播")
    class Calendar:
        timezone = ZoneInfo("Asia/Shanghai")
        db = SimpleNamespace(asoul_state=lambda key, default: {"fetched_at": time.time()})
        async def schedule_for_days(self, first, last):
            return {first: [item]}
        def render_schedule(self, day, title, items):
            return "嘉然直播八点开始"
    def fail(request):
        assert str(request.url) == WW_MENU
        raise httpx.ReadTimeout("synthetic timeout", request=request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
        result = await collect_topics(topics, Calendar(), client=client)
    assert result["errors"] == {"鸣潮官网": "ReadTimeout"}
    assert result["counts"] == {"枝江日历": 1}
    assert topics.store.get_setting("topics:refresh") == result
    assert topics.select(event(), "嘉然直播").source == "枝江日历"


def test_import_uses_only_owned_snapshot_preserves_freshness_and_provenance(tmp_path):
    store, now = Store(tmp_path), time.time()
    snapshot = tmp_path / "data" / "imports" / "old" / "persona.db"
    snapshot.parent.mkdir(parents=True)
    with sqlite3.connect(snapshot) as conn:
        conn.executescript("CREATE TABLE topics(id,source,url,title,body,published_at,fetched_at,content_hash);"
                           "CREATE TABLE topic_use(persona,group_id,topic_id,used_at);")
        conn.execute("INSERT INTO topics VALUES(?,?,?,?,?,?,?,?)", (42, "鸣潮官网", news()["url"], "鸣潮版本更新",
                     "维护公告公布新增活动", "2026-10-01", now - 90_000, "legacy-hash"))
        conn.execute("INSERT INTO topic_use VALUES(?,?,?,?)", ("denia", 102, 42, now - 80_000))
    store.record_import("persona:old", "hash", {"snapshot": snapshot.relative_to(tmp_path).as_posix()})
    topics = PersonaTopics(store)
    assert topics.import_legacy_snapshot() == {"topics": 1, "uses": 1}
    assert topics.import_legacy_snapshot() == {"topics": 0, "uses": 0}
    assert "缺少有效" in topics.select(event(), "鸣潮今天更新了吗", now=now).text
    assert news()["url"] in topics.select(event(), "来源", now=now).text
    assert news()["url"] not in topics.select(event(104), "来源", now=now).text


class QuietBot:
    self_id = 103
    connected = True
    async def call_api(self, *args, **kwargs):
        pytest.fail("topic worker attempted QQ action")
    def detach(self, *args):
        self.connected = False


class NoModel:
    async def generate(self, *args, **kwargs):
        pytest.fail("topic worker attempted model call")


@pytest.mark.asyncio
async def test_runtime_topic_worker_obeys_existing_switches_without_locking_chat(tmp_path, monkeypatch):
    config = HarnessConfig(root=tmp_path, mode="live", group_ids=(102,),
                           extra=dict({"test_prefix": "", "isolated_scope_enabled": True}, context_mode='legacy'))
    runtime = Runtime(config, bot=QuietBot(), model_client=NoModel())
    runtime.tools.domains.ensure_group(102)
    calls = []
    async def collect(topics, service):
        calls.append(topics)
        return {"counts": {}, "errors": {}, "model_calls": 0, "qq_writes": 0}
    monkeypatch.setattr("tangtang_harness.runtime.collect_topics", collect)
    lock = runtime.session_locks.setdefault("group:102", asyncio.Lock())
    await lock.acquire()
    try:
        assert (await runtime._topics_tick())["model_calls"] == 0
        runtime.tools.domains.set_feature(102, "persona_topics", False)
        assert await runtime._topics_tick() is None
        runtime.tools.domains.set_feature(102, "persona_topics", True)
        runtime.tools.domains.set_feature(102, "mention_chat", False)
        assert await runtime._topics_tick() is None
        runtime.tools.domains.set_feature(102, "mention_chat", True)
        runtime.config = replace(config, background_enabled=False)
        assert await runtime._topics_tick() is None
        runtime.config = replace(config, mode="observe")
        assert await runtime._topics_tick() is None
        assert len(calls) == 1
    finally:
        lock.release()
        await runtime.close()
