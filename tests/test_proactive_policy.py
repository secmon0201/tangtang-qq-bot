from datetime import datetime
from zoneinfo import ZoneInfo

from bot.services.proactive_policy import TrafficState, decide, ordinary_text
from bot.services.proactive_store import ProactiveStore


def noon(day=17):
    return datetime(2026, 9, day, 12, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()


def observe(store, group=1001, now=None, msg="first", text="今天有一个好消息"):
    return store.observe(group, 2001, msg, text, now or noon())


def test_heat_caps_one_user_per_minute_and_deduplicates():
    state = TrafficState()
    now = noon()
    assert state.observe(1, "1", "聊天内容", now)
    assert not state.observe(1, "1", "聊天内容", now + 1)
    assert not state.observe(1, "2", "聊天内容", now + 2)
    assert state.observe(1, "3", "另一个话题", now + 3)
    assert state.heat < 1.01
    before = state.heat
    state.decay(now + 303)
    assert abs(state.heat - before / 2) < 1e-9


def test_active_accepts_two_messages_from_single_person_but_not_old_context():
    now = noon(); state = TrafficState(heat=2, heat_at=now)
    state.observe(1, "1", "第一条消息", now)
    state.observe(1, "2", "第二条消息", now + 60)
    assert decide(state, "active_v1", now + 60, 12, 0).reason == "candidate"
    assert decide(state, "active_v1", now + 151, 12, 0).reason == "stale"
    assert decide(state, "active_v1", now + 61, 3, 0).reason == "quiet"


def test_small_third_episode_is_evaluated_and_never_retries_same_episode(tmp_path):
    store = ProactiveStore(tmp_path / "proactive.db")
    now = noon();store.set_policy(1001, "low_traffic_v1", now)
    for i in range(3):
        t = now + i * 601
        observe(store, now=t, msg=str(i))
        hit = store.candidate(1001, t, .99, 0, (1001,))
        assert hit == (i == 2)
        # Compensation survives reopening the database.
        store = ProactiveStore(store.path)
        assert not store.candidate(1001, t + 15, 0, 0, (1001,))
    assert store.admit(1001, "1001:2", store.selection(1001), now + 1202, (1001,))
    store.outcome("1001:2", "silent")
    assert "silent=1" in store.status(1001, now + 1202)


def test_quota_atomic_dedup_and_day_reset(tmp_path):
    store = ProactiveStore(tmp_path / "proactive.db"); now = noon()
    store.set_policy(1001, "low_traffic_v1", now)
    observe(store, now=now)
    selection = store.selection(1001)
    assert store.admit(1001, "1001:first", selection, now, (1001,))
    assert not store.admit(1001, "1001:first", selection, now, (1001,))
    with store.connection() as c:
        template = c.execute("SELECT * FROM attempts").fetchone()
        for i in range(11):
            row = list(template);row[0] = f"quota:{i}"
            c.execute("INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?)", row)
    observe(store, now=now + 1900, msg="second")
    assert not store.candidate(1001, now + 1900, 0, 0, (1001,))
    tomorrow = noon(18)
    observe(store, now=tomorrow, msg="tomorrow")
    assert store.candidate(1001, tomorrow, 0, 0, (1001,))


def test_global_quota_reserves_small_group_capacity(tmp_path):
    store = ProactiveStore(tmp_path / "proactive.db");now = noon()
    store.set_policy(0, "active_v1", now)
    store.set_policy(1002, "low_traffic_v1", now)
    observe(store, now=now)
    assert store.admit(1001, "first", store.selection(1001), now, (1002,))
    with store.connection() as c:
        template = list(c.execute("SELECT * FROM attempts").fetchone())
        for i in range(387):
            row = template.copy();row[0] = f"quota:{i}";row[1] = 3000 + i // 50
            c.execute("INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?)", row)
    observe(store, group=1003, now=now)
    assert not store.admit(1003, "blocked", store.selection(1003), now, (1002,))
    observe(store, group=1002, now=now)
    assert store.admit(1002, "reserved", store.selection(1002), now, (1002,))


def test_strategy_switch_invalidates_turn_without_resetting_cooldown(tmp_path):
    store = ProactiveStore(tmp_path / "proactive.db");now = noon()
    store.set_policy(1001, "active_v1", now);observe(store, now=now)
    selection = store.selection(1001)
    assert store.admit(1001, "1001:first", selection, now, ())
    store.set_policy(1001, "legacy", now + 1)
    assert not store.current(1001, selection, now + 1)
    store.set_policy(1001, "active_v1", now + 2)
    observe(store, now=now + 3, msg="next", text="新的聊天内容")
    assert not store.candidate(1001, now + 3, 0, 0, ())


def test_legacy_timestamp_and_silence_do_not_refund_attempt(tmp_path):
    store = ProactiveStore(tmp_path / "proactive.db");now = noon()
    store.set_policy(1001, "low_traffic_v1", now);observe(store, now=now)
    assert not store.candidate(1001, now, 0, now - 10, (1001,))
    assert "cooldown" in store.status(1001, now)
    assert ordinary_text("[图片]") == ""
    assert ordinary_text("#ww 查询") == ""
    assert ordinary_text("https://example.invalid/a") == ""


def test_confirmed_delivery_cannot_be_overwritten_by_later_optional_error(tmp_path):
    store = ProactiveStore(tmp_path / "proactive.db");now = noon()
    store.set_policy(1001, "active_v1", now);observe(store, now=now)
    store.admit(1001, "first", store.selection(1001), now, ())
    store.outcome("first", "proactive_reply");store.outcome("first", "error");store.finish("first")
    assert "delivered=1" in store.status(1001, now)


def test_concurrent_admission_only_reserves_one_group_slot(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    store = ProactiveStore(tmp_path / "proactive.db");now = noon()
    store.set_policy(1001, "active_v1", now);observe(store, now=now)
    selection = store.selection(1001)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda i: store.admit(1001, f"request:{i}", selection, now, ()), range(2)))
    assert sum(results) == 1


def test_restart_marks_interrupted_attempt_without_refund(tmp_path):
    store = ProactiveStore(tmp_path / "proactive.db");now = noon()
    store.set_policy(1001, "active_v1", now);observe(store, now=now)
    assert store.admit(1001, "request", store.selection(1001), now, ())
    reopened = ProactiveStore(store.path)
    reopened.recover_interrupted()
    assert "interrupted=1" in reopened.status(1001, now)
    assert not reopened.admit(1001, "after_restart", reopened.selection(1001), now + 1, ())
