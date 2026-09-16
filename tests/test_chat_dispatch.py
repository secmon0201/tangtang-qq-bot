import asyncio

from bot.services.chat_dispatch import ChatDispatcher


def test_two_waiting_calls_run_in_order_without_blocking_other_groups():
    async def scenario():
        dispatcher = ChatDispatcher(limit=2)
        release = asyncio.Event()
        other_done = asyncio.Event()
        order = []
        async def first():
            order.append(1)
            await release.wait()
        async def second():
            order.append(2)
        async def third():
            order.append(3)
        async def other():
            other_done.set()
        assert dispatcher.submit(1, first, request_id="1:1", proactive=True)
        assert dispatcher.submit(1, second, request_id="1:2")
        assert dispatcher.submit(1, third, request_id="1:3")
        assert not dispatcher.submit(1, other, request_id="1:4")
        assert not dispatcher.submit(1, other, request_id="1:5", proactive=True)
        assert not dispatcher.submit(1, other, request_id="1:2")
        assert dispatcher.submit(2, other, request_id="2:1")
        assert not dispatcher.submit(3, other, request_id="3:1")
        worker = dispatcher.tasks[1]
        await asyncio.wait_for(other_done.wait(), 1)
        assert order == [1]
        release.set()
        await worker
        assert order == [1, 2, 3]
        assert not dispatcher.tasks and not dispatcher.pending and not dispatcher.request_ids
        await dispatcher.close()
    asyncio.run(scenario())


def test_stale_queued_calls_skip_and_failure_does_not_strand_next_call():
    async def scenario():
        dispatcher = ChatDispatcher()
        release = asyncio.Event()
        valid = True
        order = []
        async def first():
            await release.wait()
            raise TimeoutError()
        async def stale():
            order.append("stale")
        async def last():
            order.append("last")
        dispatcher.submit(1, first, request_id="1:1")
        dispatcher.submit(1, stale, request_id="1:2", current=lambda: valid)
        dispatcher.submit(1, last, request_id="1:3")
        worker = dispatcher.tasks[1]
        valid = False
        release.set()
        await worker
        assert order == ["last"]
        assert not dispatcher.request_ids
        await dispatcher.close()
    asyncio.run(scenario())


def test_shutdown_cancels_running_and_waiting_calls_even_before_worker_starts():
    async def scenario(start_worker):
        dispatcher = ChatDispatcher()
        started = asyncio.Event()
        queued_ran = False
        async def first():
            started.set()
            await asyncio.Event().wait()
        async def second():
            nonlocal queued_ran
            queued_ran = True
        dispatcher.submit(1, first, request_id="1:1")
        dispatcher.submit(1, second, request_id="1:2")
        if start_worker:
            await asyncio.wait_for(started.wait(), 1)
        await dispatcher.close()
        assert not queued_ran
        assert not dispatcher.tasks and not dispatcher.pending and not dispatcher.request_ids
        assert not dispatcher.submit(1, second)
    asyncio.run(scenario(False))
    asyncio.run(scenario(True))


def test_dispatch_trace_carries_same_request_id(monkeypatch):
    traces = []
    monkeypatch.setattr(ChatDispatcher, "trace", staticmethod(lambda *args: traces.append(args)))
    async def scenario():
        dispatcher = ChatDispatcher()
        async def done():
            pass
        dispatcher.submit(1, done, request_id="1:10")
        dispatcher.submit(1, done, request_id="1:11")
        await dispatcher.tasks[1]
        assert [row[2] for row in traces if row[1] == "1:11"] == ["received", "queued", "started", "finished"]
        await dispatcher.close()
    asyncio.run(scenario())
