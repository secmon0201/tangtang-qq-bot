import asyncio

import pytest

from tangtang_harness.runtime import Runtime
from test_runtime_and_console import FakeBot, FakeModel, configured


async def idle_worker():
    await asyncio.Future()


def create_runtime(tmp_path, *, mode='live', enabled=True):
    runtime = Runtime(configured(tmp_path, mode, speech_enabled=enabled, background_enabled=False),
                      bot=FakeBot(), model_client=FakeModel())
    runtime._scheduler = idle_worker
    runtime._ai_worker = idle_worker
    runtime.stopped_speech = []
    runtime.speech_runtime.stop = lambda *, disable: runtime.stopped_speech.append(disable)
    return runtime


@pytest.mark.asyncio
@pytest.mark.parametrize('poll_state', ['blocked', 'failed'])
async def test_speech_cold_start_does_not_wait_for_bilibili_polling(tmp_path, poll_state):
    runtime = create_runtime(tmp_path)
    external_entered, speech_checked = asyncio.Event(), asyncio.Event()
    calls = []

    async def poll(**kwargs):
        external_entered.set()
        if poll_state == 'failed':
            raise RuntimeError('synthetic Bilibili network failure')
        await asyncio.Future()

    async def ensure():
        calls.append(('ensure', asyncio.current_task().get_name()))

    async def check(settings):
        calls.append(('check', asyncio.current_task().get_name()))
        runtime.speech.ready = True
        speech_checked.set()

    runtime.tools.poll_external = poll
    runtime.speech_runtime.ensure_running = ensure
    runtime.speech.check = check
    await runtime.start()
    owned_tasks = list(runtime.tasks)
    try:
        await asyncio.wait_for(asyncio.gather(external_entered.wait(), speech_checked.wait()), 1)
        assert calls == [('ensure', 'harness-speech'), ('check', 'harness-speech')]
        assert runtime.speech.ready and not runtime.bot.sent and not runtime.chat.model_client.calls
    finally:
        await runtime.close()
    assert all(task.done() for task in owned_tasks)
    assert runtime.stopped_speech == [False]


@pytest.mark.asyncio
@pytest.mark.parametrize('mode,enabled', [('live', False), ('observe', True)])
async def test_disabled_or_observe_speech_never_starts_owned_instance(tmp_path, mode, enabled):
    runtime = create_runtime(tmp_path, mode=mode, enabled=enabled)
    visited = []
    speech_task_started = asyncio.Event()

    async def forbidden_ensure():
        visited.append('ensure')
        raise AssertionError('disabled speech cannot start')

    async def forbidden_check(settings):
        visited.append('check')
        raise AssertionError('disabled speech cannot probe')

    async def poll(**kwargs):
        await asyncio.Future()

    worker = runtime._speech_worker

    async def entered_speech_worker():
        speech_task_started.set()
        await worker()

    runtime._speech_worker = entered_speech_worker
    runtime.tools.poll_external = poll
    runtime.speech_runtime.ensure_running = forbidden_ensure
    runtime.speech.check = forbidden_check
    await runtime.start()
    owned_task = next(task for task in runtime.tasks if task.get_name() == 'harness-speech')
    try:
        await asyncio.wait_for(speech_task_started.wait(), 1)
        assert not visited and not runtime.speech.ready
    finally:
        await runtime.close()
    assert owned_task.cancelled() and runtime.stopped_speech == [False]


@pytest.mark.asyncio
async def test_shutdown_cancels_in_progress_owned_speech_startup(tmp_path):
    runtime = create_runtime(tmp_path)
    starting, cancelled = asyncio.Event(), asyncio.Event()
    checks = []

    async def ensure():
        starting.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def check(settings):
        checks.append(settings)

    async def poll(**kwargs):
        await asyncio.Future()

    runtime.tools.poll_external = poll
    runtime.speech_runtime.ensure_running = ensure
    runtime.speech.check = check
    await runtime.start()
    owned_task = next(task for task in runtime.tasks if task.get_name() == 'harness-speech')
    await asyncio.wait_for(starting.wait(), 1)
    await asyncio.wait_for(runtime.close(), 1)
    assert cancelled.is_set() and owned_task.cancelled() and not checks
    assert runtime.stopped_speech == [False]
    assert not runtime.bot.sent and not runtime.chat.model_client.calls
