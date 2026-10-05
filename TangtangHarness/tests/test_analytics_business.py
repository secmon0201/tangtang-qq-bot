import json
import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest

from tangtang_harness.analytics_business import BusinessAnalytics
from tangtang_harness.business.db import SCHEMA
from tangtang_harness.continuation_policy import ConversationWindow


def database(root, relative, schema, statements=()):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(schema)
        for sql, values in statements:
            conn.execute(sql, values)
        conn.commit()
    return path


def runtime(root):
    return SimpleNamespace(config=SimpleNamespace(root=root, continuation_enabled=True,
        proactive_enabled=False, mode="observe", extra={}), windows={}, traffic={},
        started_at=1, bot=SimpleNamespace(connected=False), core=SimpleNamespace(socket=None), tasks=set())


@pytest.fixture
def analytics(tmp_path):
    return BusinessAnalytics(runtime(tmp_path))


def test_speech_separates_counters_archive_and_exact_user_dimensions(analytics, tmp_path):
    database(tmp_path, "runtime/business.db", SCHEMA, [
        ("INSERT INTO managed_groups(group_id,group_name,alias,updated_at) VALUES(?,?,?,?)", (102, "合成群的完整名称", "短名", "2026-10-01")),
        ("INSERT INTO managed_groups(group_id,updated_at) VALUES(?,?)", (104, "2026-10-01")),
        ("INSERT INTO daily_counts VALUES(?,?,?,?,?,?)", (102, "2026-10-01", 101, "甲", 8, "2026-10-01")),
        ("INSERT INTO daily_counts VALUES(?,?,?,?,?,?)", (102, "2026-10-02", 101, "甲", 6, "2026-10-02")),
        ("INSERT INTO daily_counts VALUES(?,?,?,?,?,?)", (104, "2026-10-01", 101, "甲", 90, "2026-10-01")),
        ("INSERT INTO daily_counts VALUES(?,?,?,?,?,?)", (102, "2026-10-01", 105, "乙", 5, "2026-10-01")),
    ])
    database(tmp_path, "runtime/business-history.db", "CREATE TABLE tangtang_group_messages(group_id INTEGER,user_id INTEGER,text TEXT,created_at TEXT)", [
        ("INSERT INTO tangtang_group_messages VALUES(?,?,?,?)", (102, 101, "你  怎么吗？更新2!", "2026-10-01T00:30:00+08:00")),
        ("INSERT INTO tangtang_group_messages VALUES(?,?,?,?)", (102, 101, "你 怎么吗？更新2!", "2026-09-30T16:31:00+00:00")),
        ("INSERT INTO tangtang_group_messages VALUES(?,?,?,?)", (102, 101, "长" * 60, "2026-10-01T17:00:00+08:00")),
        ("INSERT INTO tangtang_group_messages VALUES(?,?,?,?)", (104, 101, "排除数据", "2026-10-01T17:00:00+08:00")),
        ("INSERT INTO tangtang_group_messages VALUES(?,?,?,?)", (102, 105, "排除数据", "2026-10-01T17:00:00+08:00")),
    ])
    selected = {"after": "2026-10-01T00:00:00+08:00", "before": "2026-10-02T00:00:00+08:00", "session_key": "group:102", "group_id": None, "user_id": 101}
    result = analytics.speech_stats(selected)
    assert result["daily"] == [{"date": "2026-10-01", "count": 8, "users": 1, "groups": 1}]
    assert result["by_group"][0]["group_name"] == "合成群的完整名称"
    assert result["sample"]["count"] == 3
    assert result["hourly"] == [{"weekday": 4, "hour": 0, "count": 2}, {"weekday": 4, "hour": 17, "count": 1}]
    assert {r["name"]: r["count"] for r in result["profile_dimensions"]} == {
        "复读魂": 2, "好奇雷达": 2, "情绪电波": 2, "小作文": 1, "接话欲": 2, "情报站": 2}
    assert "你 怎么吗" not in json.dumps(result, ensure_ascii=False)
    assert analytics.speech_stats({"group_id": 102})["profile_dimensions"] == []
    assert analytics.speech_stats({"session_key": "private:101"})["daily"] == []


def test_business_filters_participants_and_relationship_targets_without_source_writes(analytics, tmp_path):
    database(tmp_path, "runtime/business.db", SCHEMA + "CREATE TABLE harness_pending_outputs(delivery_key TEXT,group_id INTEGER,result_json TEXT,next_attempt REAL,attempts INTEGER);", [
        ("INSERT INTO managed_groups(group_id,group_name,alias,updated_at) VALUES(?,?,?,?)", (102, "测试群的完整名称", "短名", "2026-10-01")),
        ("INSERT INTO group_features VALUES(?,?,?,?)", (102, "mini_games", 1, "2026-10-01")),
        ("INSERT INTO group_members VALUES(?,?,?,?,?,?,?,?)", (102, 101, "甲", "", "", "member", 1, "2026-10-01")),
        ("INSERT INTO mini_game_sessions(session_id,group_id,game_type,status,creator_id,started_at,ends_at,state_json) VALUES(?,?,?,?,?,?,?,?)", (1, 102, "bomb", "active", 105, "2026-10-01T12:00:00+08:00", "2026-10-01T13:00:00+08:00", '{"bomb_idiom_mode":true}')),
        ("INSERT INTO mini_game_participants(session_id,user_id,joined_at) VALUES(?,?,?)", (1, 101, "2026-10-01")),
        ("INSERT INTO today_wife_records(group_id,day,actor_id,target_id,drawn_at) VALUES(?,?,?,?,?)", (102, "2026-10-01", 105, 101, "2026-10-01T12:00:00+08:00")),
        ("INSERT INTO harness_pending_outputs VALUES(?,?,?,?,?)", ("job-1", 102, '{"secret":"hidden"}', 10, 2)),
        ("INSERT INTO asoul_plugin_state VALUES(?,?,?)", ("credential", '{"cookie":"credential-sentinel"}', "2026-10-01")),
        ("INSERT INTO asoul_plugin_state VALUES(?,?,?)", ("bilibili_monitor", '{"initialized":true,"synthetic":{"live":"1","live_session":{"online_samples":[[10,20]]},"cookie":"nested-sentinel"}}', "2026-10-01")),
    ])
    database(tmp_path, "runtime/gallery.db", "CREATE TABLE denia_gallery_draws(id INTEGER,image_id TEXT,group_id INTEGER,user_id INTEGER,drawn_at REAL)", [
        ("INSERT INTO denia_gallery_draws VALUES(?,?,?,?,?)", (1, "synthetic", 102, 101, 10)),
    ])
    path = tmp_path / "runtime/business.db"
    before = path.read_bytes()
    result = analytics.overview({"group_id": 102, "user_id": 101})
    assert result["groups"]["items"][0]["members"]["count"] == 1
    assert result["games"]["by_kind"] == [{"kind": "idiom_bomb", "status": "active", "count": 1}]
    assert result["relationships"]["count"] == 1
    assert result["deliveries"]["pending_count"] == 1
    for record in (result["games"]["recent"][0], result["relationships"]["recent"][0],
                   result["gallery"]["recent"][0], result["deliveries"]["pending"][0]):
        assert record["group_name"] == "测试群的完整名称"
    assert result["groups"]["items"][0]["alias"] == "短名"
    assert result["subscriptions"]["monitor"]["targets"][0]["online_samples"] == [{"at": 10, "value": 20}]
    serialized = json.dumps(result)
    assert "credential-sentinel" not in serialized and "nested-sentinel" not in serialized and "hidden" not in serialized
    assert path.read_bytes() == before
    assert analytics.overview({"user_id": 106})["games"]["count"] == 0


def test_continuation_user_filter_joins_actual_event_and_keeps_group_quota(analytics, tmp_path):
    database(tmp_path, "runtime/business.db", SCHEMA, [
        ("INSERT INTO managed_groups(group_id,group_name,alias,updated_at) VALUES(?,?,?,?)", (102, "续聊合成群的完整名称", "短名", "2026-10-01")),
    ])
    database(tmp_path, "data/harness.db", "CREATE TABLE events(event_key TEXT,user_id INTEGER); CREATE TABLE requests(event_key TEXT,session_key TEXT,started_at REAL,purpose TEXT,outcome TEXT);", [
        ("INSERT INTO events VALUES(?,?)", ("group:102:event-1", 101)),
        ("INSERT INTO events VALUES(?,?)", ("group:102:event-2", 105)),
        ("INSERT INTO requests VALUES(?,?,?,?,?)", ("group:102:event-1", "group:102", 10, "proactive", "completed")),
    ])
    database(tmp_path, "data/continuation.db", "CREATE TABLE continuation_attempts(request_id TEXT,group_id INTEGER,day TEXT,created_at REAL,outcome TEXT); CREATE TABLE continuation_group_quotas(group_id INTEGER,day TEXT,member_count INTEGER,daily_limit INTEGER); CREATE TABLE continuation_quota_refreshes(day TEXT,started_at REAL,outcome TEXT);", [
        ("INSERT INTO continuation_attempts VALUES(?,?,?,?,?)", ("group:102:event-1", 102, "1970-01-01", 10, "silent")),
        ("INSERT INTO continuation_attempts VALUES(?,?,?,?,?)", ("group:102:event-2", 102, "1970-01-01", 11, "delivered")),
        ("INSERT INTO continuation_group_quotas VALUES(?,?,?,?)", (102, "1970-01-01", 101, 40)),
    ])
    analytics.rt.windows["group:102"] = ConversationWindow(101, 1, 2, 3, 1)
    result = analytics.continuation({"group_id": 102, "user_id": 101})
    assert result["counts"] == [{"outcome": "silent", "count": 1}]
    assert result["quotas"][0]["daily_limit"] == 40
    assert result["quotas"][0]["group_name"] == "续聊合成群的完整名称"
    assert result["windows"][0]["user_id"] == 101
    assert result["windows"][0]["group_id"] == 102
    assert result["windows"][0]["group_name"] == "续聊合成群的完整名称"
    assert result["proactive"]["counts"] == [{"outcome": "completed", "count": 1}]


def test_runtime_capacity_excludes_dependency_directories_and_reports_current_sample(analytics, tmp_path):
    folder = tmp_path / "runtime"
    (folder / "node_modules").mkdir(parents=True)
    (folder / "node_modules/large.bin").write_bytes(b"x" * 500)
    (folder / "actual.bin").write_bytes(b"123")
    result = analytics.runtime()
    assert result["owned_capacity"]["bytes"] == 3
    assert result["owned_capacity"]["files"] == 1
    assert result["resources"]["rss_bytes"] > 0
    assert result["resources"]["cpu_percent"] is None
    assert result["history_available"] is False
    assert result["incident_counts"] is None


def test_experiments_read_history_without_payload_and_exclude_unknown_times(analytics, tmp_path):
    offline = {"id": "off", "kind": "cold_warm", "status": "offline", "model_calls": 0, "qq_writes": 0, "result": {"steps": [{"label": "冷请求", "preview": {"payload": "body-sentinel"}}]}}
    paid = {**offline, "id": "paid", "status": "completed", "model_calls": 2}
    database(tmp_path, "data/harness.db", "CREATE TABLE settings(key TEXT,value TEXT); CREATE TABLE requests(started_at REAL,telemetry TEXT);", [
        ("INSERT INTO settings VALUES(?,?)", ("experiment:off", json.dumps(offline))),
        ("INSERT INTO settings VALUES(?,?)", ("experiment:paid", json.dumps(paid))),
        ("INSERT INTO requests VALUES(?,?)", (10, '{"experiment_id":"paid"}')),
        ("INSERT INTO requests VALUES(?,?)", (11, '{"experiment_id":"paid"}')),
    ])
    result = analytics.experiments()
    assert result["count"] == 2
    assert "body-sentinel" not in json.dumps(result)
    assert result["items"][0]["steps"] == [{"label": "冷请求"}]
    assert analytics.experiments({"after": 5})["count"] == 1
    assert result["items"][1]["created_at"] is None


def test_large_experiment_get_projects_sql_rows_without_loading_prompt_or_media(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from tangtang_harness.analytics_business import _Reader
    from tangtang_harness.app import create_app
    from tangtang_harness.config import HarnessConfig
    from tangtang_harness.runtime import Runtime

    rt = Runtime(HarnessConfig(root=tmp_path, mode="observe"))
    large_prompt = "实验固定提示词正文哨兵" * 16000
    media = "data:image/png;base64," + "media-sentinel" * 16000
    payload = {"messages": [{"role": "system", "content": large_prompt},
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": media}}]}]}
    summary = {"previous_request_id": "synthetic:before", "common_prefix_bytes": 128,
               "changed_layers": ["history", "current"], "note": "本地公共前缀不等于实际缓存命中"}
    normalized_usage = {"input_tokens": 100, "output_tokens": 0, "reasoning_tokens": 0,
        "total_tokens": 100, "cache_read_tokens": 80, "cache_write_tokens": None,
        "cache_miss_tokens": 20, "cache_status": "reported", "cache_ratio": .8,
        "cost": .0001, "currency": "CNY", "input_semantics": "total_input",
        "output_semantics": "reported_total_output", "latency_ms": 12.5, "first_token_latency_ms": None}
    usage = {**normalized_usage,
        "raw": {"prompt_tokens": 100, "input_tokens_details": {"cached_tokens": 80},
                "provider_body": "raw-usage-sentinel" * 16000, "prompt": large_prompt},
        "pricing_snapshot": {"input_price_per_million": 1.5,
                             "provider_data": "pricing-snapshot-sentinel" * 16000}}
    saved = {"id": "large", "kind": "cache_append", "created_at": 100, "status": "completed",
        "model_calls": 1, "qq_writes": 0, "result": {
            "steps": [{"label": "冷请求", "preview": {"payload": payload}},
                      {"label": "追加", "request_id": "synthetic:paid", "usage": usage,
                       "reply": large_prompt, "preview": {"payload": payload}},
                      {"label": "已有空用量", "usage": None}],
            "diff": {**summary, "before": payload, "after": payload, "prompt": large_prompt, "media": media}}}
    rt.store.set_setting("experiment:large", saved)
    assert len(json.dumps(saved, ensure_ascii=False).encode()) > 1_000_000
    sqlite_rows = []
    read_rows = _Reader.rows

    def capture_rows(reader, table, *args, **kwargs):
        result = read_rows(reader, table, *args, **kwargs)
        if table == "settings":
            sqlite_rows.extend(result)
        return result

    monkeypatch.setattr(_Reader, "rows", capture_rows)
    with TestClient(create_app(runtime=rt)) as client:
        response = client.get("/api/analytics/experiments")
        assert response.status_code == 200
        assert len(response.content) < 5000
        item = response.json()["items"][0]
        assert item["diff"] == summary
        assert item["steps"] == [{"label": "冷请求"},
            {"label": "追加", "request_id": "synthetic:paid", "usage": normalized_usage},
            {"label": "已有空用量", "usage": None}]
        assert "固定提示词正文哨兵" not in response.text
        assert "media-sentinel" not in response.text
        assert "raw-usage-sentinel" not in response.text
        assert "pricing-snapshot-sentinel" not in response.text
        assert "raw" not in item["steps"][1]["usage"]
        assert "pricing_snapshot" not in item["steps"][1]["usage"]
    # The data crossing the SQLite/Python boundary is already the compact projection.
    assert len(json.dumps(sqlite_rows, ensure_ascii=False).encode()) < 5000
    assert "固定提示词正文哨兵" not in json.dumps(sqlite_rows, ensure_ascii=False)
    assert "raw-usage-sentinel" not in json.dumps(sqlite_rows, ensure_ascii=False)
    assert "pricing-snapshot-sentinel" not in json.dumps(sqlite_rows, ensure_ascii=False)
    assert rt.store.get_setting("experiment:large") == saved
    assert not rt.store.requests()


def test_empty_optional_sources_are_unknown_and_do_not_create_databases(analytics, tmp_path):
    assert analytics.overview()["games"]["count"] is None
    assert analytics.speech_stats()["sample"]["count"] is None
    assert analytics.continuation()["counts"] == []
    assert analytics.experiments()["count"] == 0
    assert list(tmp_path.iterdir()) == []


def test_runtime_analytics_endpoint_uses_actual_core_bridge(tmp_path):
    from fastapi.testclient import TestClient
    from tangtang_harness.app import create_app
    from tangtang_harness.config import HarnessConfig
    from tangtang_harness.runtime import Runtime

    rt = Runtime(HarnessConfig(root=tmp_path, mode="observe"))
    assert not hasattr(rt.core, "connected")
    with TestClient(create_app(runtime=rt)) as client:
        response = client.get("/api/analytics/runtime")
        assert response.status_code == 200
        result = response.json()
        assert result["connections"] == {"onebot": False, "core": False}
        assert result["mode"] == "observe"
        assert result["resources"]["rss_bytes"] > 0


def test_subscription_endpoint_separates_configuration_from_saved_cursors(tmp_path):
    from fastapi.testclient import TestClient
    from tangtang_harness.app import create_app
    from tangtang_harness.config import HarnessConfig
    from tangtang_harness.runtime import Runtime

    rt = Runtime(HarnessConfig(root=tmp_path, mode="observe"))
    database(tmp_path, "runtime/business.db", "", [
        ("INSERT INTO asoul_plugin_state VALUES(?,?,?)", ("bilibili_monitor",
         '{"initialized":true,"211":{"live":"1"},"212":{"live":"0"}}', "2026-10-01")),
    ])
    rt.store.set_setting("bili_enabled", True)
    rt.store.set_setting("bili_cookie", "credential-sentinel")
    with TestClient(create_app(runtime=rt)) as client:
        response = client.get("/api/analytics/business")
        assert response.status_code == 200
        result = response.json()["subscriptions"]
        assert result["configuration"] == {"enabled": True, "target_count": None,
            "comment_target_count": None, "poll_interval_seconds": None,
            "missing": ["bili_target_uids", "bili_comment_target_uids", "bili_poll_interval_seconds"]}
        assert result["monitor"]["initialized"] is True
        assert result["monitor"]["cursor_count"] == 2
        assert "credential-sentinel" not in response.text
        rt.store.set_setting("bili_target_uids", ["211"])
        rt.store.set_setting("bili_comment_target_uids", [])
        rt.store.set_setting("bili_poll_interval_seconds", 300)
        configured = client.get("/api/analytics/business").json()["subscriptions"]["configuration"]
        assert configured == {"enabled": True, "target_count": 1, "comment_target_count": 0,
            "poll_interval_seconds": 300, "missing": []}
    assert not rt.store.requests()
