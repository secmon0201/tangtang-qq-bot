import asyncio
from contextvars import ContextVar
from types import SimpleNamespace

from bot.application.proactive_chat import ProactiveCoordinator
from bot.services.chat_dispatch import ChatDispatcher
from bot.services.proactive_policy import proactive_turn
from bot.services.proactive_store import ProactiveStore


def setup(tmp_path):
    now = [1789617600.0]  # 2026-09-17 12:00 Asia/Shanghai
    store = ProactiveStore(tmp_path / "proactive.db")
    store.set_policy(0, "low_traffic_v1", now[0])
    enabled = {1001: True, 1002: False}
    personas = SimpleNamespace(
        snapshot=lambda event, model, proactive: SimpleNamespace(request_id=f"{event.group_id}:{event.message_id}"),
        current=lambda context: True)
    service = SimpleNamespace(proactive_text_allowed=lambda text: True, proactive_last_attempt=lambda group: 0)
    dispatcher = ChatDispatcher()
    coordinator = ProactiveCoordinator(store, service, dispatcher, personas,
        enabled=lambda group: enabled[group], connected=lambda bot: True,
        groups=lambda: tuple(enabled), clock=lambda: now[0], draw=lambda: 0)
    return coordinator, now, enabled


def message(now, group=1001, message_id=1):
    return SimpleNamespace(group_id=group, user_id=2001, message_id=message_id,
                           time=now, get_plaintext=lambda: "今天游戏更新内容很有趣")


def test_disabled_group_never_reaches_model_and_enabled_single_user_does(tmp_path):
    async def run():
        c, now, enabled = setup(tmp_path);calls=[]
        async def handler(bot, event, config, context):
            if proactive_turn().admit():
                calls.append(event.group_id)
                proactive_turn().outcome("silent", "")
        c.service.handle_proactive = handler
        for group in enabled:
            c.observe(object(), message(now[0], group), SimpleNamespace(model="model"))
        c.tick()
        await asyncio.gather(*tuple(c.dispatcher.tasks.values()))
        assert calls == [1001]
        assert "silent=1" in c.store.status(1001, now[0])
        assert "今日暂无" in c.store.status(1002, now[0])
    asyncio.run(run())


def test_busy_dispatcher_does_not_spend_opportunity_or_quota(tmp_path):
    async def run():
        c, now, _ = setup(tmp_path)
        c.observe(object(), message(now[0]), SimpleNamespace(model="model"))
        started=asyncio.Event();release=asyncio.Event()
        async def busy():
            started.set();await release.wait()
        c.dispatcher.submit(1001,busy)
        await started.wait();c.tick()
        with c.store.connection() as conn:
            assert conn.execute("SELECT count(*) FROM attempts").fetchone()[0] == 0
            assert c.store._state(conn,1001).offered_episode == -1
        release.set();await asyncio.gather(*tuple(c.dispatcher.tasks.values()))
    asyncio.run(run())


def test_strategy_change_during_slow_generation_cancels_delivery(tmp_path):
    async def run():
        c, now, _ = setup(tmp_path);sent=[];started=asyncio.Event();release=asyncio.Event()
        async def handler(bot,event,config,context):
            turn=proactive_turn()
            assert turn.admit()
            turn.outcome("model_started", "")
            started.set();await release.wait()
            if turn.current():sent.append(True)
        c.service.handle_proactive=handler
        c.observe(object(),message(now[0]),SimpleNamespace(model="model"));c.tick()
        await started.wait()
        c.store.set_policy(1001,"legacy",now[0]+1)
        release.set();await asyncio.gather(*tuple(c.dispatcher.tasks.values()))
        assert not sent
        assert "cancelled=1" in c.store.status(1001,now[0])
    asyncio.run(run())


def test_timer_preserves_original_matcher_context_and_stale_events_do_not_count(tmp_path):
    async def run():
        c, now, _ = setup(tmp_path);origin=ContextVar("origin",default="timer");seen=[]
        async def handler(bot,event,config,context):
            assert proactive_turn().admit()
            seen.append(origin.get());proactive_turn().outcome("proactive_reply", "")
        c.service.handle_proactive=handler
        c.observe(object(),message(now[0]-100),SimpleNamespace(model="model"))
        assert not c.pending
        token=origin.set("incoming_event")
        c.observe(object(),message(now[0]),SimpleNamespace(model="model"))
        origin.reset(token);c.tick()
        await asyncio.gather(*tuple(c.dispatcher.tasks.values()))
        assert seen == ["incoming_event"]
        assert "delivered=1" in c.store.status(1001,now[0])
    asyncio.run(run())
