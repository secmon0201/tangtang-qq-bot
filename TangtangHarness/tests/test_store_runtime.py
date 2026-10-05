import pytest

from tangtang_harness.config import ModelProfile
from tangtang_harness.store import Store
from tangtang_harness.types import InboundEvent


def event():
    return InboundEvent("1", 999, 101, 201, "我喜欢画画", sender={"nickname": "合成"})


def test_delivery_requires_platform_receipt_and_is_idempotent(tmp_path):
    store = Store(tmp_path)
    with pytest.raises(ValueError):
        store.confirm_turn(event(), ["答复"])
    assert not store.history("group:201")
    store.confirm_turn(event(), ["答复"], message_ids=["5"])
    store.confirm_turn(event(), ["答复"], message_ids=["5"])
    assert len(store.history("group:201")) == 1
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM deliveries").fetchone()[0] == 1


def test_payload_keeps_text_and_removes_secrets(tmp_path):
    store = Store(tmp_path)
    profile = ModelProfile("p", "P", "custom", "model", "https://example.invalid/v1", api_key="secret")
    rid = store.add_request(event(), profile, {"messages": [{"role": "user", "content": "合成正文"}], "api_key": "secret"})
    saved = store.request(rid)
    assert saved["payload"]["messages"][0]["content"] == "合成正文"
    assert saved["payload"]["api_key"] == "[redacted]"
    assert saved["account"] == "unknown"


def test_forget_does_not_reappear_from_extraction_until_restore(tmp_path):
    store = Store(tmp_path)
    store.remember("group:201", 101, "喜欢画画", quote="我喜欢画画", event_key="synthetic")
    assert store.forget("group:201", 101, "画画") == 1
    assert store.memories("group:201", 101) == []
    assert store.remember("group:201", 101, "喜欢画画", quote="我喜欢画画") is None
    assert store.restore_memory("group:201", 101, "画画") == 1
    assert len(store.memories("group:201", 101)) == 1


def test_background_jobs_do_not_drop_queued_memory_events(tmp_path):
    store = Store(tmp_path)
    first = event().to_dict()
    second = {**first, "event_id": "2"}
    job_id = store.enqueue_job("memory", "group:201:101", {"event": first})
    assert store.enqueue_job("memory", "group:201:101", {"event": second}) == job_id
    rows = store.due_jobs()
    assert len(rows) == 1
    assert len(rows[0]["source"]["events"]) == 2


def test_restart_finishes_owned_running_work_without_retrying_or_losing_usage(tmp_path):
    import time

    store = Store(tmp_path)
    profile = ModelProfile("p", "P", "custom", "model", "https://example.invalid/v1")
    running = store.add_request(event(), profile, {"messages": []}, purpose="memory",
        telemetry={"estimated_input_tokens": 90, "reserved_output_tokens": 20})
    completed = store.add_request(event(), profile, {"messages": []}, purpose="memory")
    store.finish_request(completed, usage={"total_tokens": 30}, outcome="completed")
    job = store.enqueue_job("memory", "group:201:101", {"event": event().to_dict()})
    store.update_job(job, "running")
    queued = store.enqueue_job("memory", "group:201:101", {"event": event().to_dict()})
    paused = store.enqueue_job("summary", "group:201", {})
    store.update_job(paused, "paused")
    store.confirm_turn(event(), ["已送达答复"], message_ids=["5"])
    with store.connect() as conn:
        conn.execute("CREATE TABLE legacy_rows(origin TEXT,table_name TEXT,row_key TEXT,source_hash TEXT,data TEXT,imported_at REAL)")
        conn.execute("INSERT INTO legacy_rows VALUES('legacy','background_jobs','old','hash',?,?)",
                     ('{"status":"running"}', time.time()))
    budget = store.background_usage(time.time() - 3600)
    usage = store.request(completed)["usage"]
    history = store.history("group:201")

    assert store.finish_interrupted_work() == {"requests": 1, "jobs": 1}
    assert store.request(running)["outcome"] == "failed"
    assert store.request(running)["usage"] == {}
    assert store.request(completed)["usage"] == usage
    assert store.request(completed)["outcome"] == "completed"
    assert store.background_usage(time.time() - 3600) == budget
    assert store.history("group:201") == history
    assert {item["id"]: item["status"] for item in store.jobs()} == {job: "failed", queued: "queued", paused: "paused"}
    assert store.legacy_rows("background_jobs")[0]["data"] == {"status": "running"}
    assert store.finish_interrupted_work() == {"requests": 0, "jobs": 0}
