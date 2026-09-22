from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from bot.services.continuation_policy import ContinuationTurn, continuation_turn_for
from bot.services.tangtang_chat import TangtangProvider
from bot.services.tangtang_db import TangtangDb
from tests.test_persona_integration import Provider, event, make_locked_runtime, service_for
from tests.test_reply_style import add_reply


class CapturingProvider(Provider):
    def __init__(self, response):
        super().__init__(response)
        self.envelopes = []

    async def generate_agent(self, config, persona, prompt, *args):
        self.envelopes.append(args[-1])
        return await super().generate_agent(config, persona, prompt, *args)


def setup_chat(tmp_path, monkeypatch, response):
    engine, backend = make_locked_runtime(tmp_path)
    provider = CapturingProvider(response)
    service, config = service_for(tmp_path, engine, provider)
    config = replace(config, context_layout='v2', memory_enabled=False)
    sent = []

    async def send(bot, action, **params):
        sent.append(params['message'])
        return {'message_id': 77}

    monkeypatch.setattr('bot.services.tangtang_chat.call_qq_action', send)
    monkeypatch.setattr('bot.services.speech.call_qq_action', send)
    return engine, backend, provider, service, config, sent


@pytest.mark.parametrize('mode', ['call', 'continuation', 'proactive'])
@pytest.mark.parametrize('enabled', [True, False])
def test_denia_cleanup_all_chat_paths(tmp_path, monkeypatch, mode, enabled):
    raw = '说实话，今天适合休息。希望以上信息对你有帮助！'
    engine, _, provider, service, config, sent = setup_chat(tmp_path, monkeypatch,
        dict(decision='reply', messages=[raw, '唔……让我想想——还不确定。'], voice='text'))
    config = replace(config, humanize_enabled=enabled, proactive_enabled=True,
                     proactive_probability=1, proactive_probability_by_group={1001: 1},
                     proactive_cooldown_seconds=0, proactive_cooldown_seconds_by_group={1001: 0},
                     proactive_message_interval=0, proactive_message_interval_by_group={1001: 0})

    async def run():
        if mode == 'proactive':
            await service.handle_proactive(None, event('今天刚做完家务'), config)
        elif mode == 'continuation':
            with continuation_turn_for(ContinuationTurn(lambda: True, lambda: True, lambda *_: None)):
                await service.handle_continuation(None, event('然后呢'), config)
        else:
            await service.handle(None, event(), config)
        await engine.speech.close()

    asyncio.run(run())
    expected = '今天适合休息。' if enabled else raw
    assert len(provider.seen) == 1
    assert expected in str(sent[0])
    history = engine.history('denia', service.db).list_calls(2001, 1001)
    second = '唔……让我想想，还不确定。' if enabled else '唔……让我想想——还不确定。'
    assert history[0]['reply_text'] == expected + '\n' + second
    assert len(sent) == 2


@pytest.mark.parametrize('voice_available', [True, False])
def test_cleaned_voice_and_fallback_match_delivered_history(tmp_path, monkeypatch, voice_available):
    raw = '说实话，今天——很开心。希望以上信息对你有帮助！'
    engine, backend, provider, service, config, sent = setup_chat(tmp_path, monkeypatch,
        dict(decision='reply', messages=[raw], voice='accept', text_fallback=[raw]))
    if not voice_available:
        engine.store.set_option('speech_enabled', False)

    async def run():
        await service.handle(None, event('娅娅，用语音聊聊今天'), config)
        await engine.speech.close()

    asyncio.run(run())
    text = engine.history('denia', service.db).list_calls(2001, 1001)[0]['reply_text']
    assert '今天，很开心。' in text
    assert '说实话' not in text and '希望以上' not in text
    assert len(provider.seen) == 1
    if voice_available:
        assert backend.calls[0][0] == text == '今天，很开心。'
        assert sent[0].type == 'record'
    else:
        assert not backend.calls
        assert '今天，很开心。' in str(sent[-1])


@pytest.mark.parametrize('body', ['很高兴帮到你。', '——'])
def test_cleanup_empty_denia_reply_is_silent(tmp_path, monkeypatch, body):
    engine, backend, provider, service, config, sent = setup_chat(tmp_path, monkeypatch,
        dict(decision='reply', messages=[body], voice='accept'))

    async def run():
        await service.handle(None, event(), config)
        await engine.speech.close()

    asyncio.run(run())
    assert not sent and not backend.calls
    assert len(provider.seen) == 1
    assert not engine.history('denia', service.db).list_calls(2001, 1001)


@pytest.mark.parametrize('layout', ['v1', 'shadow', 'v2'])
def test_style_history_read_once_and_shared_with_shadow(tmp_path, monkeypatch, layout):
    engine, _, provider, service, config, _ = setup_chat(tmp_path, monkeypatch,
        dict(decision='reply', messages=['今天挺好的。'], voice='text'))
    config = replace(config, context_layout=layout)
    history = engine.history('denia', service.db)
    for _ in range(3):
        add_reply(history, '唔，PRIVATE_EXAMPLE。', timestamp=service._now())
    reads = []
    original = TangtangDb.recent_style_replies

    def read(db, *args, **kwargs):
        reads.append(db.path)
        return original(db, *args, **kwargs)

    monkeypatch.setattr(TangtangDb, 'recent_style_replies', read)
    candidates = []
    original_payload = TangtangProvider._responses_payload

    def capture(*args, **kwargs):
        candidates.append(kwargs.get('envelope', args[6] if len(args) > 6 else None))
        return original_payload(*args, **kwargs)

    monkeypatch.setattr(TangtangProvider, '_responses_payload', staticmethod(capture))

    async def run():
        await service.handle(None, event(), config)
        await engine.speech.close()

    asyncio.run(run())
    assert reads == [history.path]
    assert len(provider.seen) == 1
    assert provider.seen[0][1].count('[本轮表达提醒]') == 1
    if layout == 'shadow':
        candidate = next(value for value in candidates if value is not None)
        assert candidate.dynamic_status.count('[本轮表达提醒]') == 1
        assert '[本轮表达提醒]' not in candidate.static_text


def test_reminder_changes_only_dynamic_context(tmp_path, monkeypatch):
    engine, _, provider, service, config, _ = setup_chat(tmp_path, monkeypatch,
        dict(decision='reply', messages=['今天挺好的。'], voice='text'))

    async def run():
        await service.handle(None, event(), config)
        for _ in range(3):
            add_reply(engine.history('denia', service.db), '唔，今天不错。', timestamp=service._now())
        await service.handle(None, event('娅娅，明天有什么安排', message=2), config)
        await engine.speech.close()

    asyncio.run(run())
    first, second = provider.envelopes
    assert '[本轮表达提醒]' not in first.dynamic_status
    assert '[本轮表达提醒]' in second.dynamic_status
    assert first.static_prefix_hash == second.static_prefix_hash
    assert first.tool_schema_hash == second.tool_schema_hash
    assert first.static_text == second.static_text


@pytest.mark.parametrize('enabled', [True, False])
def test_optional_style_read_failure_does_not_block_chat(tmp_path, monkeypatch, enabled):
    engine, _, provider, service, config, sent = setup_chat(tmp_path, monkeypatch,
        dict(decision='reply', messages=['今天挺好的。'], voice='text'))
    reads = []

    def fail(*args, **kwargs):
        reads.append(True)
        raise RuntimeError('private source must not be logged')

    monkeypatch.setattr(TangtangDb, 'recent_style_replies', fail)

    async def run():
        await service.handle(None, event(), replace(config, humanize_enabled=enabled))
        await engine.speech.close()

    asyncio.run(run())
    assert len(reads) == int(enabled)
    assert len(provider.seen) == 1 and len(sent) == 1
    assert '[本轮表达提醒]' not in provider.envelopes[0].dynamic_status
