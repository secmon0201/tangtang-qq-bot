import asyncio
import json
from dataclasses import replace

import pytest

from tangtang_harness.models import ModelResult
from tangtang_harness.onebot import MessageSegment
from tangtang_harness.router import RouteDecision
from tangtang_harness.runtime import Runtime
from tangtang_harness.types import InboundEvent
from test_reply_media import catalog
from test_runtime_and_console import FakeBot, configured


class ReplyModel:
    def __init__(self, **metadata):
        self.metadata = metadata
        self.calls = []

    async def generate(self, profile, payload, **kwargs):
        self.calls.append(payload)
        return ModelResult(json.dumps({'decision': 'reply', 'messages': ['你好呀', '今天也一起聊会儿吧'],
                                       **self.metadata}, ensure_ascii=False),
                           {'input_tokens': 20, 'output_tokens': 10, 'cache_read_tokens': 0})


class Speech:
    ready = True

    def __init__(self, path, fail=False):
        self.path, self.fail, self.calls = path, fail, []

    async def synthesize(self, text, settings):
        self.calls.append(text)
        if self.fail:
            raise RuntimeError('synthetic TTS failure')
        self.path.write_bytes(b'synthetic audio')
        return self.path


def runtime_fixture(tmp_path, monkeypatch, *, source='娅娅讲个短故事', roll=.1,
                    voice_probability=.1, expression_probability=0, bot=None, **metadata):
    monkeypatch.setattr('tangtang_harness.runtime.random.random', lambda: roll)
    catalog(tmp_path)
    config = configured(tmp_path, 'live', group_ids=(102,), speech_enabled=True,
                        continuation_enabled=False,
                        extra={'test_prefix': '', 'isolated_scope_enabled': True,
                               'speech_probability': voice_probability,
                               'expression_probability': expression_probability})
    model = ReplyModel(**metadata)
    runtime = Runtime(config, bot=bot or FakeBot(), model_client=model)
    runtime.tools.domains.ensure_group(102)
    runtime.speech = Speech(tmp_path / 'tts.wav')
    event = InboundEvent('1', 103, 101, 102, source)
    runtime.store.append_event(event)
    return runtime, model, event


@pytest.mark.asyncio
@pytest.mark.parametrize('metadata,spoken', [
    ({'voice': 'accept'}, '你好呀\n今天也一起聊会儿吧'),
    ({'voice': 'accept', 'speech_text': '完整的口语回答'}, '完整的口语回答'),
    ({}, '你好呀\n今天也一起聊会儿吧'),
])
async def test_explicit_voice_sends_one_record_for_multiple_bubbles_without_extra_model(tmp_path, monkeypatch, metadata, spoken):
    runtime, model, event = runtime_fixture(tmp_path, monkeypatch, source='娅娅用语音讲个短故事',
                                          expression_probability=1, expression='smile', **metadata)
    await runtime.process(event, RouteDecision('chat'))
    assert len(model.calls) == 1 and runtime.speech.calls == [spoken]
    assert len(runtime.bot.sent) == 1 and runtime.bot.sent[0][1].type == 'record'
    assert not runtime.store.get_setting('expression_history:' + event.session_key)
    assert not runtime.store.get_setting('last_random_voice:' + event.session_key)
    assert runtime.store.history(event.session_key)[0]['messages'] == ['[语音]']
    await runtime.close()


@pytest.mark.asyncio
async def test_ordinary_default_auto_voice_is_sampled_and_success_sets_cooldown(tmp_path, monkeypatch):
    runtime, model, event = runtime_fixture(tmp_path, monkeypatch, roll=.01)
    await runtime.process(event, RouteDecision('chat'))
    assert runtime.bot.sent[0][1].type == 'record' and len(runtime.speech.calls) == 1
    assert runtime.store.get_setting('last_random_voice:' + event.session_key) > 0
    followup = replace(event, event_id='2', text='娅娅再讲个短故事')
    runtime.store.append_event(followup)
    await runtime.process(followup, RouteDecision('chat'))
    assert len(runtime.speech.calls) == 1 and len(model.calls) == 2
    assert all(isinstance(message, str) for _, message in runtime.bot.sent[1:])
    await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['synthesize', 'send'])
async def test_failed_voice_silently_falls_back_to_text_and_does_not_consume_cooldown(tmp_path, monkeypatch, failure):
    class RecordFailureBot(FakeBot):
        async def send(self, event, message):
            if isinstance(message, MessageSegment) and message.type == 'record':
                raise RuntimeError('synthetic record send failure')
            return await super().send(event, message)

    runtime, model, event = runtime_fixture(tmp_path, monkeypatch, roll=.01,
                                          bot=RecordFailureBot() if failure == 'send' else None)
    runtime.speech.fail = failure == 'synthesize'
    queue = asyncio.Queue()
    runtime.subscribers.add(queue)
    await runtime.process(event, RouteDecision('chat'))
    assert [message for _, message in runtime.bot.sent] == ['你好呀', '今天也一起聊会儿吧']
    assert len(model.calls) == 1 and not runtime.store.get_setting('last_random_voice:' + event.session_key)
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    assert any(item['type'] == 'speech' and item['status'] == 'text_fallback' and item['error'] for item in events)
    await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('roll,expected_images', [(.1, 1), (.8, 0)])
async def test_text_expression_uses_local_sixty_percent_selection_without_extra_model(tmp_path, monkeypatch, roll, expected_images):
    runtime, model, event = runtime_fixture(tmp_path, monkeypatch, roll=roll, voice_probability=0,
                                          expression_probability=.6, expression='smile',
                                          expression_candidates=['laugh', 'think'])
    await runtime.process(event, RouteDecision('chat'))
    images = [message for _, message in runtime.bot.sent if isinstance(message, MessageSegment)]
    assert len(images) == expected_images and all(image.type == 'image' for image in images)
    if images:
        assert images[0].data['file'].endswith('.gif')
        assert len(runtime.store.get_setting('expression_history:' + event.session_key)) == 1
    assert len(model.calls) == 1 and not runtime.speech.calls
    await runtime.close()


@pytest.mark.asyncio
async def test_group_media_switches_preserve_text_reply(tmp_path, monkeypatch):
    runtime, model, event = runtime_fixture(tmp_path, monkeypatch, source='娅娅用语音讲个短故事',
                                          voice='accept', expression='smile', expression_probability=1)
    runtime.tools.domains.set_feature(102, 'persona_voice', False)
    runtime.tools.domains.set_feature(102, 'persona_expressions', False)
    await runtime.process(event, RouteDecision('chat'))
    assert [message for _, message in runtime.bot.sent] == ['你好呀', '今天也一起聊会儿吧']
    assert len(model.calls) == 1 and not runtime.speech.calls
    await runtime.close()
