import sqlite3

import pytest

from bot.db import Database, SCHEMA
from bot.services.group_domains import GroupDomainService
from bot.services.passive_settings import PassiveSettingsStore


def runtime_controls(tmp_path):
    db = Database(tmp_path / "bot.db")
    domains = GroupDomainService(db)
    for group_id in (1001, 1002):
        domains.ensure_group(group_id)
    return db, domains, PassiveSettingsStore(db)


@pytest.mark.parametrize("feature", (
    "mention_chat", "proactive_chat", "persona_voice", "persona_growth",
    "persona_expressions", "persona_topics",
))
def test_group_off_on_invalidation_is_durable_and_group_local(tmp_path, feature):
    db, domains, _ = runtime_controls(tmp_path)
    domains.set_feature(1001, feature, True)
    before = db.chat_gate_revision(1001)
    other = db.chat_gate_revision(1002)
    domains.set_feature(1001, feature, False)
    domains.set_feature(1001, feature, True)
    assert db.chat_gate_revision(1001) == (before[0], before[1] + 2)
    assert db.chat_gate_revision(1002) == other
    assert Database(db.path).chat_gate_revision(1001) == db.chat_gate_revision(1001)
    domains.set_feature(1001, feature, True)
    assert db.chat_gate_revision(1001) == (before[0], before[1] + 2)


@pytest.mark.parametrize("feature", ("mention_chat", "proactive_chat"))
def test_global_off_on_invalidation_reaches_every_group(tmp_path, feature):
    db, _, controls = runtime_controls(tmp_path)
    before = {group: db.chat_gate_revision(group) for group in (1001, 1002)}
    controls.set_chat_globally_enabled(feature, False)
    controls.set_chat_globally_enabled(feature, True)
    for group, revision in before.items():
        assert db.chat_gate_revision(group) == (revision[0] + 2, revision[1])
    controls.set_chat_globally_enabled(feature, True)
    assert db.chat_gate_revision(1001)[0] == before[1001][0] + 2


def test_pause_transitions_and_group_disable_invalidate_old_work(tmp_path):
    db, domains, _ = runtime_controls(tmp_path)
    db.set_passive_setting("automation_pause_active", "false")
    before = db.chat_gate_revision(1001)
    db.set_passive_setting("automation_pause_active", "true")
    db.set_passive_setting("automation_pause_active", "false")
    assert db.chat_gate_revision(1001) == (before[0] + 2, before[1])
    domains.disable_group(1001)
    domains.ensure_group(1001)
    assert db.chat_gate_revision(1001) == (before[0] + 2, before[1] + 2)


def test_unrelated_settings_and_bulk_noops_preserve_chat_generation(tmp_path):
    db, domains, _ = runtime_controls(tmp_path)
    before = db.chat_gate_revision(1001)
    domains.set_feature(1001, "nte", False)
    domains.set_feature(1001, "nte", True)
    db.set_passive_setting("reaction_probability", "0.15")
    with db.connect() as conn:
        conn.execute("UPDATE group_features SET configured_enabled=configured_enabled")
    assert db.chat_gate_revision(1001) == before


def test_bulk_feature_updates_cannot_bypass_generation_tracking(tmp_path):
    db, _, _ = runtime_controls(tmp_path)
    before = {group: db.chat_gate_revision(group) for group in (1001, 1002)}
    with db.connect() as conn:
        conn.execute("UPDATE group_features SET configured_enabled=0 WHERE feature_key='mention_chat'")
        conn.execute("UPDATE group_features SET configured_enabled=1 WHERE feature_key='mention_chat'")
    for group, revision in before.items():
        assert db.chat_gate_revision(group) == (revision[0], revision[1] + 2)


def test_old_database_gains_triggers_without_rewriting_existing_settings(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO managed_groups(group_id,updated_at) VALUES(1001,'old')")
        conn.execute("INSERT INTO group_features VALUES(1001,'mention_chat',1,'old')")
        conn.commit()
    finally:
        conn.close()
    db = Database(path)
    assert db.chat_gate_revision(1001) == (0, 0)
    assert db.feature_enabled(1001, "mention_chat")
    db.set_group_feature(1001, "mention_chat", False)
    db.set_group_feature(1001, "mention_chat", True)
    assert db.chat_gate_revision(1001) == (0, 2)
    db.initialize()
    assert db.chat_gate_revision(1001) == (0, 2)


def test_delete_and_reinsert_gate_row_cannot_resurrect_generation(tmp_path):
    db, _, _ = runtime_controls(tmp_path)
    before = db.chat_gate_revision(1001)
    with db.connect() as conn:
        conn.execute("DELETE FROM group_features WHERE group_id=1001 AND feature_key='mention_chat'")
    db.set_group_feature(1001, "mention_chat", True)
    assert db.chat_gate_revision(1001) == (before[0], before[1] + 2)


@pytest.mark.parametrize("scope,feature", (
    ("group", "mention_chat"), ("group", "persona_voice"),
    ("global", "mention_chat"), ("global", "proactive_chat"),
    ("pause", "automation_pause_active"),
))
def test_inflight_reply_cannot_resurrect_after_gate_is_restored(tmp_path, monkeypatch, scope, feature):
    import asyncio
    from tests.test_persona_integration import Provider, event, make_runtime, service_for

    async def scenario():
        db, domains, controls = runtime_controls(tmp_path)
        engine, _ = make_runtime(tmp_path)
        engine.store.switch(1001, "denia")
        engine.gate_revision = db.chat_gate_revision
        engine.chat_enabled = lambda group, proactive: (
            domains.effective_feature_enabled(group, "mention_chat")
            and controls.is_chat_globally_enabled("mention_chat"))
        domains.set_feature(1001, "persona_voice", True)
        db.set_passive_setting("automation_pause_active", "false")

        def toggle_while_model_is_running():
            if scope == "group":
                domains.set_feature(1001, feature, False)
                domains.set_feature(1001, feature, True)
            elif scope == "global":
                controls.set_chat_globally_enabled(feature, False)
                controls.set_chat_globally_enabled(feature, True)
            else:
                db.set_passive_setting(feature, "true")
                db.set_passive_setting(feature, "false")

        provider = Provider({"decision": "reply", "messages": ["旧请求的迟到回复。"], "voice": "text"},
                            after=toggle_while_model_is_running)
        service, config = service_for(tmp_path, engine, provider)
        sent = []
        async def send(*args, **kwargs):
            sent.append(kwargs["message"])
            return {"message_id": 72}
        monkeypatch.setattr("bot.services.tangtang_chat.call_qq_action", send)
        current_event = event("娅娅，今天想聊聊旅行", message=71)
        frozen = engine.snapshot(current_event, "synthetic", False)
        other = engine.snapshot(event(group=1002), "synthetic", False)
        assert engine.current(frozen)
        await service.handle(object(), current_event, config, context=frozen)
        assert len(provider.seen) == 1 and sent == []
        assert not engine.current(frozen)
        assert engine.current(engine.snapshot(current_event, "synthetic", False))
        assert engine.current(other) is (scope == "group")
    asyncio.run(scenario())
