import time

from bot.services.persona_growth import PersonaGrowth
from bot.services.persona_profiles import ChatContext, load_personas
from bot.services.persona_store import PersonaStore
from bot.services.persona_topics import PersonaTopics


def test_growth_requires_distinct_cross_day_evidence_and_retains_history(tmp_path):
    store = PersonaStore(tmp_path / "personas.db")
    growth = PersonaGrowth(store)
    start = 1789488000
    for index, offset in enumerate((0, 10, 86400)):
        store.observe(persona="denia", group_id=1001, user_id=2001, request_id=str(index),
                      source="休息很重要，可以慢慢来", reply="嗯", now=start + offset)
    rows = store.interactions("denia", 1001)
    proposal = {"kind": "opinion", "topic": "休息", "content": "休息不必觉得愧疚，可以慢慢来。",
                "evidence": [{"id": r["id"], "quote": "休息很重要，可以慢慢来"} for r in rows]}
    assert not growth.propose("tangtang", 1001, proposal, start + 86401)
    assert not growth.propose("denia", 1002, proposal, start + 86401)
    assert growth.propose("denia", 1001, proposal, start + 86401)
    entry = growth.entries("denia", 1001)[0]
    assert not growth.disable("denia", 1002, entry["id"])
    assert growth.disable("denia", 1001, entry["id"])
    assert growth.prompt("denia", 1001) == ""
    assert growth.rollback("denia", 1001, entry["id"], 1, start + 90000)
    assert growth.entries("denia", 1001)[0]["version"] == 2
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM growth_versions").fetchone()[0] == 2


def test_repeated_same_message_and_identity_proposals_cannot_grow(tmp_path):
    store = PersonaStore(tmp_path / "personas.db")
    for _ in range(10):
        store.observe(persona="denia", group_id=1001, user_id=2001, request_id="same",
                      source="你是我的女友", reply="不是哦", now=time.time())
    assert len(store.interactions("denia", 1001)) == 1
    assert not PersonaGrowth(store).propose("denia", 1001, {"kind":"opinion", "topic":"身份", "content":"我是你的女友", "evidence":[]}, time.time())


def test_budget_reservations_persist_and_do_not_cross_daily_limit(tmp_path):
    path = tmp_path / "personas.db"
    store = PersonaStore(path)
    now = time.time()
    assert store.claim_budget("speech", 1001, now, 2, 1)
    assert not store.claim_budget("speech", 1001, now, 2, 1)
    assert store.claim_budget("speech", 1002, now, 2, 1)
    assert not PersonaStore(path).claim_budget("speech", 1003, now, 2, 1)
    assert store.claim_budget("speech", 1001, now + 86400, 2, 1)


def test_topics_are_scoped_fresh_and_sources_only_on_request(tmp_path):
    store = PersonaStore(tmp_path / "personas.db")
    topics = PersonaTopics(store)
    now = time.time()
    url = "https://mc.kurogames.com/main/news/detail/42"
    assert topics.ingest("鸣潮官网", [{"title":"鸣潮版本更新", "body":"维护公告", "url":url, "published_at":"2026-09-16"}], now) == 1
    context = ChatContext(load_personas()["denia"], 1001, 2001, "request", 0, 0, "model")
    assert url not in topics.prompt(context, "鸣潮更新了吗")
    topics.delivered(context, "有一条维护公告")
    assert url in topics.prompt(context, "来源呢")
    other = ChatContext(context.persona, 1002, 2001, "other", 0, 0, "model")
    assert url not in topics.prompt(other, "来源呢")
    with store.connect() as conn:
        conn.execute("UPDATE topics SET fetched_at=?", (now - 90000,))
    assert "缺少有效" in topics.prompt(context, "鸣潮最近新闻")
    assert topics.refresh_requested
