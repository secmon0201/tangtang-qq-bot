import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

from bot.services.persona_background import PersonaBackground
from bot.services.persona_engine import PersonaEngine
from bot.services.persona_mood import mood_decay
from bot.services.persona_store import PersonaStore
from bot.services.tangtang_chat import TangtangConfig


def test_mood_returns_to_baseline_without_identity_changes():
    assert mood_decay(0.9, "2026-09-16T00:00:00", "2026-09-16T06:00:00") == 0.7
    assert mood_decay(0.1, "2026-09-16T00:00:00", "2026-09-17T00:00:00") == 0.475


def test_background_is_fair_bounded_and_preserves_old_pending_rows(tmp_path, monkeypatch):
    store = PersonaStore(tmp_path / "state.db")
    engine = PersonaEngine(store, None, feature_enabled=lambda *_: True, chat_enabled=lambda *_: True)
    for group in (1001, 1002):
        for index in range(35):
            store.observe(persona="denia", group_id=group, user_id=2001, request_id=f"{group}:{index}", source="休息很重要，可以慢慢来", reply="嗯", now=1789488000 + index)
    engine.evidence_allowed = lambda *_: True
    for group in (1001, 1002):
        store.observe(persona='denia', group_id=group, user_id=2001, request_id='later', source='休息很重要，可以慢慢来', reply='嗯', now=1789574400)
    clock = [1789617600.]  # afternoon, cumulative quota permits both groups
    monkeypatch.setattr('bot.services.persona_background.time.time', lambda: clock[0])
    original_ids = [r["id"] for r in store.pending_interactions("denia", 1001)]
    calls = []
    class Provider:
        async def generate(self, config, identity, prompt):
            calls.append(json.loads(prompt[prompt.index('{"current"'):]))
            assert len(prompt) <= 8000 and config.max_output_tokens == 800 and config.reasoning_effort == "none"
            return '{"proposals":[]}', {"total_tokens":20}
    config = replace(TangtangConfig.disabled(), enabled=True)
    worker = PersonaBackground(engine, Provider(), SimpleNamespace(load=lambda:config))
    async def run():
        for _ in range(2):
            await worker.tick({1001, 1002})
        await worker.tick({1001, 1002})  # six-hour per-group spacing survives workers
        clock[0] += 21600
        for _ in range(3):
            await worker.tick({1001, 1002})
    asyncio.run(run())
    assert len(calls) == 4  # two per group, both personas share this cap
    assert not set(original_ids) & {r["id"] for r in store.pending_interactions("denia", 1001)}
    assert store.pending_groups()  # backlog remains available for later days


def test_persona_management_permissions(monkeypatch):
    import nonebot
    try:
        nonebot.get_driver()
    except ValueError:
        nonebot.init()
    from bot.plugins import persona_management as plugin
    event = SimpleNamespace(group_id=1001, user_id=2001)
    role = "member"
    class Platform:
        def __init__(self, bot): pass
        async def member_info(self, group_id, user_id):
            assert group_id == 1001 and user_id == 2001
            return {"role":role}
    monkeypatch.setattr(plugin, "QQPlatform", Platform)
    monkeypatch.setattr(plugin, "is_super_admin", lambda user:False)
    assert not asyncio.run(plugin.can_manage_persona(None, event))
    for role in ("admin", "owner"):
        assert asyncio.run(plugin.can_manage_persona(None, event))
    monkeypatch.setattr(plugin, "is_super_admin", lambda user:True)
    role = "member"
    assert asyncio.run(plugin.can_manage_persona(None, event))
