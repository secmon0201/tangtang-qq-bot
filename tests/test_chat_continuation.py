import asyncio
import sqlite3
from dataclasses import replace
from types import SimpleNamespace

from nonebot.adapters.onebot.v11 import Message

from bot.application.chat_continuation import ContinuationCoordinator, has_other_addressee
from bot.services.chat_dispatch import ChatDispatcher
from bot.services.continuation_policy import ContinuationConfig, ContinuationStore, continuation_turn
from bot.services.persona_profiles import ChatContext, PersonaProfile


def make_case(tmp_path, *, config=None, sleeper=asyncio.sleep):
    now = [1789617600.0]
    enabled = {1001: True, 1002: True}
    revision = [0]
    profile = PersonaProfile("denia", "达妮娅", "娅娅", tmp_path, "v1")
    personas = SimpleNamespace(current=lambda c: c.selection_revision == revision[0])
    store = ContinuationStore(tmp_path / "continuation.db")
    dispatcher = ChatDispatcher()
    calls = []
    service = SimpleNamespace()
    coordinator = ContinuationCoordinator(service, dispatcher, personas,
        enabled=lambda group: enabled[group], connected=lambda bot: True,
        config=lambda: config or ContinuationConfig(debounce_seconds=0, max_debounce_seconds=0),
        store=lambda: store, clock=lambda: now[0], sleeper=sleeper)

    def context(event):
        return ChatContext(profile, event.group_id, event.user_id, f"{event.group_id}:{event.message_id}",
                           revision[0], 0, "model")

    async def explicit(bot, event, config, context):
        calls.append(("explicit", event.get_plaintext(), context))
        coordinator.outcome(context, "reply")

    async def automatic(bot, event, config, context):
        turn = continuation_turn()
        if turn.admit():
            calls.append(("automatic", event.get_plaintext(), context))
            turn.outcome("reply", "")

    service.handle, service.handle_continuation = explicit, automatic
    return coordinator, now, enabled, revision, calls, context


def message(now, text="再讲一点", *, group=1001, user=2001, mid=1):
    return SimpleNamespace(group_id=group, user_id=user, message_id=mid, time=now,
        self_id=3001, message=Message(text), original_message=Message(text), reply=None,
        get_plaintext=lambda: text)


async def drain(coordinator):
    await asyncio.gather(*(p.task for p in tuple(coordinator.pending.values())))
    await asyncio.gather(*tuple(coordinator.dispatcher.tasks.values()))


def test_only_confirmed_delivery_opens_same_group_same_user_window(tmp_path):
    c, now, _, revision, _, ctx = make_case(tmp_path)
    first = message(now[0])
    for outcome in ("model_result", "silent", "error", "voice_uncertain"):
        c.outcome(ctx(first), outcome)
        assert not c.eligible(first)
    c.outcome(ctx(first), "reply")
    assert c.eligible(first)
    assert not c.eligible(message(now[0], user=2002))
    assert not c.eligible(message(now[0], group=1002))
    revision[0] += 1
    assert not c.eligible(first)


def test_idle_and_hard_cap_are_not_reset_by_automatic_delivery(tmp_path):
    async def scenario():
        c, now, _, _, calls, ctx = make_case(tmp_path,
            config=ContinuationConfig(max_attempts=20, debounce_seconds=0, max_debounce_seconds=0))
        opener = message(now[0]); c.outcome(ctx(opener), "reply")
        started = now[0]
        for mid in range(2, 8):
            now[0] += 99
            follow = message(now[0], mid=mid)
            assert c.offer(object(), follow, object(), ctx(follow), explicit=False)
            await drain(c)
        assert len(calls) == 6 and c.windows[1001].opened_at == started
        now[0] = started + 600
        assert not c.eligible(message(now[0]))
        c.outcome(ctx(message(now[0])), "reply")
        now[0] += 120
        assert not c.eligible(message(now[0]))
        await c.close()
    asyncio.run(scenario())


def test_four_attempts_and_two_silences_close_window(tmp_path):
    async def scenario():
        c, now, _, _, calls, ctx = make_case(tmp_path)
        c.outcome(ctx(message(now[0])), "reply")
        for mid in range(2, 6):
            follow = message(now[0], mid=mid)
            assert c.offer(object(), follow, object(), ctx(follow), explicit=False)
            await drain(c)
        assert len(calls) == 4 and not c.eligible(message(now[0]))
        c.outcome(ctx(message(now[0], mid=10)), "reply")
        async def silent(*args, **kwargs):
            turn = continuation_turn()
            assert turn.admit()
            turn.outcome("silent", "")
            turn.outcome("silent", "duplicate log must not count twice")
        c.service.handle_continuation = silent
        for mid in (11, 12):
            follow = message(now[0], mid=mid)
            assert c.offer(object(), follow, object(), ctx(follow), explicit=False)
            await drain(c)
        assert not c.eligible(message(now[0]))
        await c.close()
    asyncio.run(scenario())


def test_daily_caps_are_shared_across_personas_and_survive_restart(tmp_path):
    path = tmp_path / "continuation.db"
    config = ContinuationConfig(group_daily=2, global_daily=3)
    store = ContinuationStore(path)
    now = 1789617600
    assert store.claim(1001, "denia:one", now, config)
    assert not store.claim(1001, "denia:one", now, config)
    assert ContinuationStore(path).claim(1001, "tangtang:two", now, config)
    assert not store.claim(1001, "denia:three", now, config)
    assert store.claim(1002, "other:four", now, config)
    assert not store.claim(1003, "other:five", now, config)
    assert store.claim(1001, "next:day", now + 86400, config)


def test_same_user_burst_merges_before_first_reply_without_blocking_other_group(tmp_path):
    async def scenario():
        release = asyncio.Event()
        async def sleeper(_):
            await release.wait()
        c, now, _, _, calls, ctx = make_case(tmp_path,
            config=ContinuationConfig(), sleeper=sleeper)
        first = message(now[0], "娅娅，听我说", mid=1)
        second = message(now[0], "我最近换了工作", mid=2)
        assert c.offer(object(), first, object(), ctx(first), explicit=True)
        assert c.eligible(second)
        now[0] += 1
        assert c.offer(object(), second, object(), ctx(second), explicit=False)
        assert c.offer(object(), second, object(), ctx(second), explicit=False)
        other_done = asyncio.Event()
        async def other():
            other_done.set()
        c.dispatcher.submit(1002, other)
        await asyncio.wait_for(other_done.wait(), 1)
        assert not calls
        now[0] += 2
        release.set()
        await drain(c)
        assert [(kind, text) for kind, text, _ in calls] == [("explicit", "娅娅，听我说\n我最近换了工作")]
        assert calls[0][2].request_id == "1001:1"
        await c.close()
    asyncio.run(scenario())


def test_continuous_burst_debounce_cannot_exceed_three_seconds(tmp_path):
    async def scenario():
        sleep_calls = []
        c = None
        async def sleeper(delay):
            sleep_calls.append(delay)
            now[0] += delay
            follow = message(now[0], f"补充{len(sleep_calls)}", mid=len(sleep_calls) + 1)
            assert c.offer(object(), follow, object(), ctx(follow), explicit=False)
        c, now, _, _, calls, ctx = make_case(tmp_path, config=ContinuationConfig(), sleeper=sleeper)
        first = message(now[0], "娅娅，先等等")
        c.offer(object(), first, object(), ctx(first), explicit=True)
        await drain(c)
        assert sum(sleep_calls) == 3 and len(calls) == 1
        assert "补充1" in calls[0][1] and "补充2" in calls[0][1]
        await c.close()
    asyncio.run(scenario())


def test_queue_full_does_not_spend_continuation_quota(tmp_path):
    async def scenario():
        c, now, _, _, _, ctx = make_case(tmp_path)
        release = asyncio.Event()
        async def busy():
            await release.wait()
        for n in range(3):
            assert c.dispatcher.submit(1001, busy, request_id=f"busy:{n}")
        c.outcome(ctx(message(now[0])), "reply")
        follow = message(now[0], mid=10)
        assert c.offer(object(), follow, object(), ctx(follow), explicit=False)
        await asyncio.gather(*(p.task for p in tuple(c.pending.values())))
        with sqlite3.connect(c.store().path) as conn:
            assert conn.execute("SELECT count(*) FROM continuation_attempts").fetchone()[0] == 0
        release.set()
        await asyncio.gather(*tuple(c.dispatcher.tasks.values()))
        assert c.windows[1001].attempts == 0
        await c.close()
    asyncio.run(scenario())


def test_persona_switch_gate_close_and_stale_queue_cancel_without_delivery(tmp_path):
    async def scenario(reason):
        root = tmp_path / reason; root.mkdir()
        c, now, enabled, revision, calls, ctx = make_case(root)
        release = asyncio.Event()
        async def busy():
            await release.wait()
        c.dispatcher.submit(1001, busy)
        c.outcome(ctx(message(now[0])), "reply")
        follow = message(now[0], mid=2)
        c.offer(object(), follow, object(), ctx(follow), explicit=False)
        await asyncio.gather(*(p.task for p in tuple(c.pending.values())))
        if reason == "switch": revision[0] += 1
        elif reason == "disabled": enabled[1001] = False
        else: now[0] += 121
        release.set()
        await asyncio.gather(*tuple(c.dispatcher.tasks.values()))
        assert not calls
        await c.close()
    for reason in ("switch", "disabled", "stale"):
        asyncio.run(scenario(reason))


def test_shutdown_cancels_pending_burst_and_slow_automatic_send(tmp_path):
    async def scenario():
        c, now, _, _, calls, ctx = make_case(tmp_path)
        c.outcome(ctx(message(now[0])), "reply")
        started = asyncio.Event(); release = asyncio.Event()
        async def slow(*args, **kwargs):
            turn = continuation_turn()
            assert turn.admit()
            started.set()
            await release.wait()
            if turn.current(): calls.append("sent")
        c.service.handle_continuation = slow
        follow = message(now[0], mid=2)
        c.offer(object(), follow, object(), ctx(follow), explicit=False)
        await started.wait()
        await c.close()
        release.set()
        await asyncio.gather(*tuple(c.dispatcher.tasks.values()))
        assert not calls
        assert not c.offer(object(), follow, object(), ctx(follow), explicit=True)
    asyncio.run(scenario())


def test_explicit_burst_can_absorb_supplement_when_automatic_gate_is_closed(tmp_path):
    async def scenario():
        c, now, enabled, _, calls, ctx = make_case(tmp_path)
        enabled[1001] = False
        first = message(now[0], "娅娅，听我说")
        follow = message(now[0], "还有一件事", mid=2)
        assert c.offer(object(), first, object(), ctx(first), explicit=True)
        assert c.eligible(follow)
        assert c.offer(object(), follow, object(), ctx(follow), explicit=False)
        await drain(c)
        assert len(calls) == 1 and calls[0][1] == "娅娅，听我说\n还有一件事"
        assert not c.eligible(message(now[0], mid=3))
        await c.close()
    asyncio.run(scenario())


def test_queued_explicit_call_keeps_proactive_suppressed_after_debounce(tmp_path):
    async def scenario():
        c, now, _, _, _, ctx = make_case(tmp_path)
        release = asyncio.Event()
        async def busy():
            await release.wait()
        c.dispatcher.submit(1001, busy)
        first = message(now[0], "娅娅，听我说")
        assert c.offer(object(), first, object(), ctx(first), explicit=True)
        await asyncio.gather(*(p.task for p in tuple(c.pending.values())))
        assert not c.pending and c.has_active_group(1001)
        release.set()
        await asyncio.gather(*tuple(c.dispatcher.tasks.values()))
        assert not c.dispatched
        await c.close()
    asyncio.run(scenario())


def test_other_addressee_and_unknown_quotes_do_not_resume():
    assert has_other_addressee(message(1, "[CQ:at,qq=4001]你好"))
    assert has_other_addressee(message(1, "[CQ:reply,id=7]你好"))
    own = message(1, "你好")
    own.reply = SimpleNamespace(sender=SimpleNamespace(user_id=3001))
    assert not has_other_addressee(own)
    own.reply.sender.user_id = 4001
    assert has_other_addressee(own)


def test_plugin_routes_followup_once_and_excludes_commands_and_other_people(tmp_path, monkeypatch):
    from tests.test_tangtang_chat import group_message, enabled_config, enable_plugin_group_features
    import bot.plugins.tangtang_chat as plugin
    c, now, _, _, _, ctx = make_case(tmp_path)
    initial = message(now[0])
    c.outcome(ctx(initial), "reply")
    monkeypatch.setattr(plugin, "continuation_coordinator", c)
    monkeypatch.setattr(plugin, "runtime_config", lambda: enabled_config(TANGTANG_PROACTIVE_ENABLED="true"))
    monkeypatch.setattr(plugin, "persona_engine", lambda: SimpleNamespace(profile=lambda group: ctx(initial).persona))
    monkeypatch.setattr(plugin, "automation_is_paused", lambda: False)
    enable_plugin_group_features(monkeypatch, 1001)
    follow = group_message(group_id=1001, user_id=2001, text="然后你觉得呢")
    assert plugin.is_continuation_event(follow)
    assert not plugin.is_proactive_event(follow)
    for text in ("#帮助", "nte帮助", "NTE角色列表", "ww帮助", "/help", "[CQ:at,qq=4001]然后呢", "[CQ:reply,id=5]然后呢"):
        assert not plugin.is_continuation_event(group_message(group_id=1001, user_id=2001, text=text))
    assert not plugin.is_continuation_event(group_message(group_id=1001, user_id=2002, text="然后呢"))
    assert not plugin.is_continuation_event(group_message(group_id=1002, user_id=2001, text="然后呢"))
    assert not plugin.is_continuation_event(group_message(group_id=1001, user_id=2001, text="娅娅继续说"))


def test_real_service_merged_call_then_ordinary_followup_uses_one_shared_model_path(tmp_path, monkeypatch):
    from tests.test_persona_integration import make_runtime, Provider, service_for, event
    async def scenario():
        engine, _ = make_runtime(tmp_path)
        engine.store.switch(1001, "denia")
        provider = Provider({"decision": "reply", "messages": ["继续说，我听着呢。"], "voice": "text"})
        service, chat_config = service_for(tmp_path, engine, provider)
        chat_config = replace(chat_config, ignore_probability=1)
        sent = []
        async def send(bot, api, **kwargs):
            sent.append(kwargs["message"])
            return {"message_id": len(sent) + 100}
        monkeypatch.setattr("bot.services.tangtang_chat.call_qq_action", send)
        c = ContinuationCoordinator(service, ChatDispatcher(), engine,
            enabled=lambda group: True, connected=lambda bot: True,
            config=lambda: ContinuationConfig(debounce_seconds=0, max_debounce_seconds=0),
            store=lambda: ContinuationStore(tmp_path / "continuation.db"))
        service.turn_observer = c.outcome
        import time
        first = event("娅娅，今天想聊聊旅行", message=1); first.time = time.time()
        # The initial ordinary call is explicitly permitted; followups bypass call-ignore.
        c.offer(object(), first, replace(chat_config, ignore_probability=0), engine.snapshot(first, "synthetic", False), explicit=True)
        supplement = event("尤其是海边旅行", message=2); supplement.time = time.time()
        assert c.offer(object(), supplement, chat_config, engine.snapshot(supplement, "synthetic", False), explicit=False)
        await drain(c)
        assert len(sent) == 1 and c.eligible(first)
        assert "尤其是海边旅行" in provider.seen[0][1]
        # A transport replay of any merged supplement cannot become a second model turn.
        assert c.offer(object(), supplement, chat_config, engine.snapshot(supplement, "synthetic", False), explicit=False)
        await drain(c)
        assert len(sent) == 1 and len(provider.seen) == 1
        follow = event("海边和山里你更喜欢哪里", message=3); follow.time = time.time()
        assert c.offer(object(), follow, chat_config, engine.snapshot(follow, "synthetic", False), explicit=False)
        await drain(c)
        assert len(sent) == 2 and len(provider.seen) == 2
        assert "续聊" in provider.seen[-1][1]
        assert len(service._base_db.recent_user_messages(2001, 1002, 10)) == 0
        await c.close()
    asyncio.run(scenario())
