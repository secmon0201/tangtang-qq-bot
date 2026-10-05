# Explicit legacy-layout compatibility contracts; new defaults are tested in test_cache_spine.py.
import asyncio
import json
import ssl
from dataclasses import replace
from unittest.mock import AsyncMock

import httpx
from fastapi.testclient import TestClient

from tangtang_harness.app import create_app
from tangtang_harness.chat import ChatService
from tangtang_harness.config import HarnessConfig, ModelProfile
from tangtang_harness.models import ModelClient, model_error_summary
from tangtang_harness.runtime import Runtime
from tangtang_harness.router import RouteDecision
from tangtang_harness.store import Store
from tangtang_harness.types import InboundEvent


def configured(tmp_path, **kwargs):
    profile = ModelProfile('synthetic', 'Synthetic', 'custom', 'synthetic', 'https://example.invalid/v1', **kwargs)
    return HarnessConfig(root=tmp_path, mode='live', profiles=(profile,), active_model=profile.id,
                         background_enabled=False, extra={'context_mode': 'legacy'})


def inbound():
    return InboundEvent('diagnostic', 999, 101, None, '合成消息正文仅用于诊断测试',
                        sender={'nickname': '诊断用户甲'})


def test_http_status_and_safe_provider_message_are_saved_without_public_message(tmp_path):
    calls = []
    def reject(request):
        calls.append(request)
        return httpx.Response(400, json={'error': {'message': "Unsupported parameter: 'temperature'"}})

    rt = Runtime(configured(tmp_path), model_client=ModelClient(transport=httpx.MockTransport(reject)))
    rt.bot.send = AsyncMock(return_value={'message_id': '42'})
    async def run():
        await rt.process(inbound(), RouteDecision('chat'))
        await rt.close()
    asyncio.run(run())

    assert len(calls) == 1
    row = rt.store.requests()[0]
    assert row['outcome'] == 'failed'
    assert row['error'] == "HTTPStatusError HTTP 400: Unsupported parameter: 'temperature'"
    assert row['usage'].get('input_tokens') is None
    assert row['usage'].get('output_tokens') is None
    assert row['usage'].get('cache_read_tokens') is None
    assert row['usage']['latency_ms'] >= 0
    rt.bot.send.assert_not_awaited()
    with TestClient(create_app(runtime=Runtime(replace(rt.config, mode='observe')))) as client:
        result = client.get('/api/requests/' + row['id'])
        assert result.status_code == 200
        assert result.json()['error'] == row['error']


def test_connection_cause_reports_expired_certificate_without_fabricating_usage(tmp_path):
    calls = []
    def reject(request):
        calls.append(request)
        try:
            raise ssl.SSLCertVerificationError(1, 'certificate has expired: example.invalid')
        except ssl.SSLCertVerificationError as cause:
            raise httpx.ConnectError('All connection attempts failed', request=request) from cause

    config = configured(tmp_path)
    store = Store(tmp_path)
    service = ChatService(config, store, ModelClient(transport=httpx.MockTransport(reject)))
    reply = asyncio.run(service.respond(inbound()))
    assert reply.status == 'failed'
    assert 'HTTPS证书已过期或不在有效期内' in reply.error
    assert 'SSLCertVerificationError' in reply.error
    assert 'certificate has expired' in reply.error
    assert 'example.invalid' not in reply.error
    assert len(calls) == len(store.requests()) == 1
    assert store.request(reply.request_id)['error'] == reply.error
    assert reply.usage.get('input_tokens') is None
    assert reply.usage.get('cache_read_tokens') is None
    assert store.history(inbound().session_key) == []


def test_streaming_http_error_retains_provider_message_without_retry(tmp_path):
    calls = []
    def reject(request):
        calls.append(request)
        return httpx.Response(429, json={'error': {'message': 'Rate limit exceeded'}})

    config = configured(tmp_path, extra_body={'stream': True})
    store = Store(tmp_path)
    service = ChatService(config, store, ModelClient(transport=httpx.MockTransport(reject)))
    reply = asyncio.run(service.respond(inbound()))
    assert reply.error == 'HTTPStatusError HTTP 429: Rate limit exceeded'
    assert len(calls) == 1
    assert store.request(reply.request_id)['error'] == reply.error


def test_error_summary_removes_credentials_endpoints_identity_and_request_echo(tmp_path, monkeypatch):
    monkeypatch.setenv('SYNTHETIC_MODEL_KEY', 'secret-sentinel')
    config = configured(tmp_path, api_key_env='SYNTHETIC_MODEL_KEY')
    text = '说话人：诊断用户甲（101）\n合成消息正文仅用于诊断测试'
    request = httpx.Request('POST', 'https://example.invalid/v1/responses',
        json={'input': [{'role': 'user', 'content': [{'type': 'input_text', 'text': text}]}]})
    response = httpx.Response(400, request=request, json={'error': {'message':
        'Unsupported parameter for 诊断用户甲 101: 合成消息正文仅用于诊断测试; secret-sentinel '
        'https://example.invalid/private-path; user_id=900000001; admin@example.invalid; '
        'request body: body-sentinel ' + 'x' * 1000}})
    exc = httpx.HTTPStatusError('ignored exception dump', request=request, response=response)
    result = model_error_summary(exc, config.profile())
    assert 'HTTP 400' in result and 'Unsupported parameter' in result
    for forbidden in ('secret-sentinel', 'example.invalid', '诊断用户甲', '101', '900000001',
                      '合成消息正文仅用于诊断测试', 'body-sentinel', 'ignored exception dump'):
        assert forbidden not in result
    assert len(result) < 300


def test_large_error_response_is_not_saved_as_a_diagnostic(tmp_path):
    calls = []
    def reject(request):
        calls.append(request)
        return httpx.Response(502, json={'error': {'message': 'large-response-sentinel' * 10000}})
    config = configured(tmp_path)
    store = Store(tmp_path)
    service = ChatService(config, store, ModelClient(transport=httpx.MockTransport(reject)))
    reply = asyncio.run(service.respond(inbound()))
    assert reply.error == 'HTTPStatusError HTTP 502: Bad Gateway'
    row = store.request(reply.request_id)
    assert 'large-response-sentinel' not in json.dumps(row)
    assert len(calls) == 1


def test_background_http_diagnosis_is_shared_by_job_and_request(tmp_path):
    calls = []
    def reject(request):
        calls.append(request)
        return httpx.Response(401, json={'error': {'message': 'Invalid API key'}})
    config = replace(configured(tmp_path), background_enabled=True)
    store = Store(tmp_path)
    service = ChatService(config, store, ModelClient(transport=httpx.MockTransport(reject)))
    service.enqueue_memory(inbound())
    result = asyncio.run(service.run_background_once())
    assert result['status'] == 'failed'
    assert result['error'] == 'HTTPStatusError HTTP 401: Invalid API key'
    assert store.requests()[0]['error'] == result['error']
    assert store.jobs()[0]['result']['error'] == result['error']
    assert len(calls) == len(store.requests()) == 1
    assert store.requests()[0]['usage'].get('input_tokens') is None


def test_native_certificate_time_status_has_clear_diagnosis():
    exc = httpx.ConnectError('certificate verify failed: NotTimeValid',
                            request=httpx.Request('GET', 'https://example.invalid/models'))
    result = model_error_summary(exc)
    assert 'HTTPS证书已过期或不在有效期内' in result
    assert 'NotTimeValid' in result
