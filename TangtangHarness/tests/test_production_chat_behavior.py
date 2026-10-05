import asyncio
import json
import time
from dataclasses import replace

import httpx
import pytest

from tangtang_harness.models import ModelClient, model_error_summary
from tangtang_harness.onebot import parse_event
from tangtang_harness.runtime import Runtime
from tangtang_harness.types import ToolResult
from tangtang_harness.router import RouteDecision
from test_runtime_and_console import FakeBot, FakeModel, configured, packet


def live(tmp_path, **kwargs):
    return configured(tmp_path, 'live', group_ids=(102,), continuation_enabled=False,
        proactive_enabled=False, extra={'test_prefix': '', 'isolated_scope_enabled': True,
            'continuation': {'debounce_seconds': .001, 'max_debounce_seconds': .002}}, **kwargs)


@pytest.mark.asyncio
async def test_unaddressed_chat_and_natural_tools_only_collect_context(tmp_path):
    bot, model = FakeBot(), FakeModel()
    rt = Runtime(live(tmp_path), bot=bot, model_client=model)
    for index, text in enumerate(('你好', '今天发言排行', '来张表情包', '我猜 123', '查询本群功能'), 1):
        assert (await rt.receive(packet(text, index)))['status'] == 'context_only'
    assert not model.calls and not bot.sent and not rt.store.tool_results('group:102')
    assert len(rt.store.events('group:102')) == 5
    await rt.close()


@pytest.mark.asyncio
async def test_called_and_actual_bot_mention_chat_but_at_other_member_does_not(tmp_path):
    bot, model = FakeBot(), FakeModel()
    rt = Runtime(live(tmp_path), bot=bot, model_client=model)
    assert (await rt.receive(packet('娅娅你好', 1)))['status'] == 'accepted'
    await asyncio.gather(*tuple(rt.tasks))
    mentioned = packet('你好', 2)
    mentioned['message'].insert(0, {'type': 'at', 'data': {'qq': '103'}})
    assert (await rt.receive(mentioned))['status'] == 'accepted'
    await asyncio.gather(*tuple(rt.tasks))
    mentioned = packet('你好', 3)
    mentioned['message'].insert(0, {'type': 'at', 'data': {'qq': '104'}})
    assert (await rt.receive(mentioned))['status'] == 'context_only'
    assert len(model.calls) == len(bot.sent) == 2
    assert not rt.windows
    await rt.close()


@pytest.mark.asyncio
async def test_command_and_called_natural_tool_still_work_without_chat_tokens(tmp_path):
    bot, model = FakeBot(), FakeModel()
    rt = Runtime(live(tmp_path), bot=bot, model_client=model)
    async def execute(event, call):
        return ToolResult('ok', '合成业务结果')
    rt.execute_call = execute
    for index, text in enumerate(('#帮助', '娅娅今天发言排行'), 1):
        assert (await rt.receive(packet(text, index)))['route'] == 'tool'
        await asyncio.gather(*tuple(rt.tasks))
    assert len(bot.sent) == 2 and not model.calls
    await rt.close()


@pytest.mark.asyncio
async def test_tool_system_errors_are_recorded_and_never_public(tmp_path):
    bot = FakeBot()
    rt = Runtime(live(tmp_path), bot=bot, model_client=FakeModel())
    async def execute(event, call):
        raise RuntimeError('synthetic external service error')
    rt.execute_call = execute
    await rt.receive(packet('#帮助'))
    await asyncio.gather(*tuple(rt.tasks))
    assert not bot.sent
    row = rt.store.tool_results('group:102')[0]
    assert row['result']['status'] == 'error'
    assert row['result']['data']['error'] == 'synthetic external service error'
    await rt.close()


@pytest.mark.asyncio
async def test_model_timeout_is_local_only_and_does_not_open_continuation(tmp_path):
    def timeout(request):
        raise httpx.ReadTimeout('', request=request) from httpx.ReadTimeout('')
    bot = FakeBot()
    rt = Runtime(live(tmp_path), bot=bot,
        model_client=ModelClient(transport=httpx.MockTransport(timeout)))
    await rt.receive(packet('娅娅你好'))
    await asyncio.gather(*tuple(rt.tasks))
    request = rt.store.requests()[0]
    assert request['outcome'] == 'failed' and request['error'] == 'ReadTimeout'
    assert not bot.sent and not rt.windows and not rt.store.history('group:102')
    await rt.close()


@pytest.mark.asyncio
async def test_model_read_timeout_and_connect_timeout_are_separate(tmp_path):
    observed = []
    def answer(request):
        observed.append(request.extensions['timeout'])
        return httpx.Response(200, json={'choices': [{'message': {'content': '你好'}}]})
    profile = configured(tmp_path).profile()
    await ModelClient(transport=httpx.MockTransport(answer)).generate(profile,
        {'model': profile.model, 'messages': [{'role': 'user', 'content': '你好'}]})
    assert observed[0]['read'] == 120 and observed[0]['connect'] == 10


@pytest.mark.asyncio
@pytest.mark.parametrize('body', [
    'data: {"type":"response.failed","response":{"error":{"message":"failed"}}}\n\n',
    'data: {"type":"response.output_text.delta","delta":"partial"}\n\n'])
async def test_stream_failure_and_premature_close_do_not_become_chat_answers(tmp_path, body):
    def answer(request):
        return httpx.Response(200, text=body, headers={'content-type': 'text/event-stream'})
    bot = FakeBot()
    config = live(tmp_path)
    config = replace(config, profiles=(replace(config.profile(), extra_body={'stream': True}),))
    rt = Runtime(config, bot=bot, model_client=ModelClient(transport=httpx.MockTransport(answer)))
    await rt.receive(packet('娅娅你好'))
    await asyncio.gather(*tuple(rt.tasks))
    assert rt.store.requests()[0]['outcome'] == 'failed'
    assert not bot.sent and not rt.store.history('group:102')
    await rt.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('reported_usage', [None, {
    'input_tokens': 1536, 'input_tokens_details': {'cached_tokens': 1024},
    'output_tokens': 2, 'output_tokens_details': {'reasoning_tokens': 0}, 'total_tokens': 1538}])
async def test_stream_failure_keeps_diagnosis_and_only_reported_usage(tmp_path, reported_usage):
    response = {'id': 'resp_synthetic', 'model': 'synthetic-model', 'status': 'failed',
                'error': {'code': 'upstream_timeout', 'type': 'server_error',
                          'message': 'upstream deadline expired', 'param': None}}
    if reported_usage is not None:
        response['usage'] = reported_usage
    body = 'data: ' + json.dumps({'type': 'response.failed', 'response': response}) + '\n\n'

    def answer(request):
        return httpx.Response(200, text=body, headers={
            'content-type': 'text/event-stream', 'x-request-id': 'req_synthetic',
            'x-backend-account': 'synthetic-account', 'x-channel-id': 'synthetic-channel',
            'set-cookie': 'private_cookie=never_save'})

    bot = FakeBot()
    config = live(tmp_path)
    config = replace(config, profiles=(replace(config.profile(), api_style='responses',
                                              extra_body={'stream': True}),))
    rt = Runtime(config, bot=bot, model_client=ModelClient(transport=httpx.MockTransport(answer)))
    try:
        await rt.receive(packet('娅娅你好'))
        await asyncio.gather(*tuple(rt.tasks))
        record = rt.store.requests()[0]
        assert record['outcome'] == 'failed' and record['account'] == 'synthetic-account'
        diagnosis = record['telemetry']['model_diagnostics']
        assert diagnosis['event_type'] == 'response.failed'
        assert diagnosis['error']['code'] == 'upstream_timeout'
        assert diagnosis['response']['id'] == 'resp_synthetic'
        assert diagnosis['headers']['x-request-id'] == 'req_synthetic'
        assert 'private_cookie' not in json.dumps(diagnosis)
        assert 'pricing_snapshot' in record['telemetry']
        if reported_usage is None:
            assert record['usage'].get('input_tokens') is None
            assert record['usage'].get('cache_read_tokens') is None
        else:
            assert record['usage']['input_tokens'] == 1536
            assert record['usage']['cache_read_tokens'] == 1024
        assert record['usage']['latency_ms'] >= 0
        assert not bot.sent and not rt.store.history('group:102')
    finally:
        await rt.close()


@pytest.mark.asyncio
async def test_local_canned_reply_uses_voice_without_model_call(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    resources = tmp_path / 'resources/personas/denia'
    resources.mkdir(parents=True)
    (resources / 'lines.txt').write_text('我在这里呀。', encoding='utf-8')
    monkeypatch.setattr('tangtang_harness.runtime.random.random', lambda: 0)
    config = live(tmp_path, speech_enabled=True)
    config = replace(config, extra={**config.extra, 'speech_probability': 1})
    bot, model = FakeBot(), FakeModel()
    rt = Runtime(config, bot=bot, model_client=model)
    rt.tools.domains.ensure_group(102)
    rt.speech.ready = True
    rt.speech.synthesize = AsyncMock(return_value=tmp_path / 'synthesized.wav')
    event = parse_event(packet('娅娅'))
    rt.store.append_event(event)
    await rt.process(event, RouteDecision('chat'))
    assert not model.calls and len(bot.sent) == 1
    assert bot.sent[0][1].type == 'record'
    rt.speech.synthesize.assert_awaited_once_with('我在这里呀。', {})
    await rt.close()
