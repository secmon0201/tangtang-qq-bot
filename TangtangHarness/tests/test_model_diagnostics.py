"""Safe provider diagnostics from synthetic HTTP/SSE, without external IO."""
import asyncio
import json

import httpx
import pytest

from tangtang_harness.config import ModelProfile
from tangtang_harness.models import (ModelClient, ModelRequestError, ModelResult,
                                     build_payload, model_error_summary)


REQUEST_TEXT = 'synthetic request body sentinel'
KEY = 'synthetic-private-header-key'
HEADERS = {'x-request-id': 'request-synthetic', 'x-backend-account': 'backend-A',
           'x-backend-channel': 'channel-A', 'set-cookie': 'private-cookie-sentinel',
           'authorization': 'Bearer private-response-token', 'x-arbitrary': 'unrelated-header'}


def profile():
    return ModelProfile('test', 'Synthetic', 'custom', 'configured-model',
                        'https://example.invalid/v1', api_style='responses', api_key=KEY)


def payload(stream=True):
    result = build_payload(profile(), [{'role': 'user', 'content': REQUEST_TEXT}])
    result['stream'] = stream
    result['vendor'] = {'token': 'synthetic-body-token'}
    return result


def sse(*events):
    return ''.join('data: ' + json.dumps(event) + '\n\n' for event in events)


def failed(body, *, status=200, stream=True, headers=None):
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(status, text=body, headers=headers or HEADERS)

    client = ModelClient(transport=httpx.MockTransport(handle))
    original = payload(stream)
    with pytest.raises(ModelRequestError) as caught:
        asyncio.run(client.generate(profile(), original))
    assert len(seen) == 1
    assert json.loads(seen[0].content) == original
    assert seen[0].headers['authorization'] == 'Bearer ' + KEY
    assert isinstance(caught.value, ValueError)
    assert caught.value.usage['latency_ms'] >= 0
    return caught.value


def test_sse_failed_retains_safe_error_metadata_and_actual_usage():
    usage = {'input_tokens': 2048, 'output_tokens': 7,
             'input_tokens_details': {'cached_tokens': 1024}}
    error = failed(sse(
        {'type': 'response.created', 'response': {'id': 'response-A', 'model': 'actual-model',
                                                'service_tier': 'default', 'status': 'in_progress'}},
        {'type': 'response.failed', 'response': {'status': 'failed', 'usage': usage,
            'output': [{'reasoning': 'reasoning-private-sentinel'}],
            'error': {'type': 'server_error', 'code': 'upstream_timeout', 'param': None,
                      'message': f'Backend timeout {KEY}; {REQUEST_TEXT}; https://example.invalid/private; '
                                 'token=private-error-token; request body: never-store-this',
                      'debug_body': 'never-store-debug'}}}))
    diagnostic = error.diagnostics
    assert diagnostic['http_status'] == 200
    assert diagnostic['headers'] == {name: HEADERS[name] for name in (
        'x-request-id', 'x-backend-account', 'x-backend-channel')}
    assert diagnostic['response'] == {'id': 'response-A', 'model': 'actual-model',
                                     'service_tier': 'default', 'status': 'failed'}
    assert diagnostic['event_type'] == 'response.failed'
    assert diagnostic['error']['code'] == 'upstream_timeout'
    assert diagnostic['error']['type'] == 'server_error'
    assert diagnostic['error']['param'] is None
    assert error.account == 'backend-A'
    assert error.usage['raw'] == usage
    assert error.usage['input_tokens'] == 2048
    assert error.usage['cache_read_tokens'] == 1024
    assert error.usage['cache_ratio'] == .5
    assert 'upstream_timeout' in model_error_summary(error, profile())
    recorded = json.dumps(diagnostic) + str(error)
    for secret in (KEY, REQUEST_TEXT, 'example.invalid', 'private-cookie-sentinel',
                   'private-response-token', 'private-error-token', 'never-store-this',
                   'reasoning-private-sentinel', 'never-store-debug'):
        assert secret not in recorded


def test_failed_without_usage_remains_unknown():
    error = failed(sse({'type': 'response.failed', 'response': {
        'error': {'code': 'backend_failure', 'message': 'Provider failed'}}}))
    for name in ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_ratio'):
        assert error.usage.get(name) is None
    assert 'raw' not in error.usage
    assert error.usage['first_token_latency_ms'] is None


@pytest.mark.parametrize('final_usage', [{}, {'output_tokens': 8}, {
    'output_tokens': 8, 'input_tokens': None, 'input_tokens_details': {'cached_tokens': None, 'other': 5}}])
def test_empty_or_partial_failed_usage_preserves_previously_reported_fields(final_usage):
    error = failed(sse(
        {'usage': {'input_tokens': 2048, 'output_tokens': 3,
                   'input_tokens_details': {'cached_tokens': 1024}}},
        {'type': 'response.failed', 'response': {'usage': final_usage,
                                               'error': {'code': 'backend_failure'}}}))
    assert error.usage['input_tokens'] == 2048
    assert error.usage['cache_read_tokens'] == 1024
    assert error.usage['output_tokens'] == final_usage.get('output_tokens', 3)


def test_incomplete_retains_only_allowed_reason_and_received_usage():
    error = failed(sse({'type': 'response.incomplete', 'response': {
        'id': 'response-incomplete', 'status': 'incomplete',
        'incomplete_details': {'reason': 'max_output_tokens', 'output': 'never-store-body'},
        'usage': {'input_tokens': 1024, 'output_tokens': 64}}}))
    assert error.diagnostics['incomplete_details'] == {'reason': 'max_output_tokens'}
    assert 'max_output_tokens' in str(error)
    assert error.usage['cache_read_tokens'] is None
    assert error.usage['input_tokens'] == 1024
    assert 'never-store-body' not in json.dumps(error.diagnostics)


def test_standalone_sse_error_retains_code_type_and_param():
    error = failed(sse({'type': 'error', 'code': 'invalid_parameter', 'param': 'reasoning.effort',
                        'message': 'Unsupported reasoning effort', 'output': 'never-store-output'}))
    assert error.diagnostics['error'] == {'code': 'invalid_parameter', 'type': 'error',
                                        'param': 'reasoning.effort', 'message': 'Unsupported reasoning effort'}
    assert 'invalid_parameter' in str(error)
    assert 'never-store-output' not in json.dumps(error.diagnostics)


@pytest.mark.parametrize('stream', [True, False])
def test_timeout_after_headers_retains_received_metadata_without_partial_answer(stream):
    seen = []

    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            if stream:
                yield sse({'type': 'response.created', 'response': {'id': 'response-started',
                            'model': 'actual-model', 'status': 'in_progress'}}).encode()
            else:
                yield b'{"id":"response-not-yet-parsed",'
            raise httpx.ReadTimeout(f'{KEY} https://example.invalid/private; {REQUEST_TEXT}', request=seen[0])

    def handle(request):
        seen.append(request)
        return httpx.Response(200, stream=BrokenStream(), headers=HEADERS)

    client = ModelClient(transport=httpx.MockTransport(handle))
    with pytest.raises(ModelRequestError) as caught:
        asyncio.run(client.generate(profile(), payload(stream)))
    error = caught.value
    assert len(seen) == 1
    assert str(error).startswith('ReadTimeout')
    assert error.diagnostics['transport_error_type'] == 'ReadTimeout'
    assert error.diagnostics['http_status'] == 200
    assert error.diagnostics['headers']['x-request-id'] == 'request-synthetic'
    assert error.account == 'backend-A'
    if stream:
        assert error.diagnostics['response']['id'] == 'response-started'
    else:
        assert 'response' not in error.diagnostics
    assert error.usage.get('input_tokens') is None
    for secret in (KEY, REQUEST_TEXT, 'example.invalid'):
        assert secret not in str(error)


def test_transport_failure_before_headers_does_not_guess_metadata():
    def timeout(request):
        raise httpx.ConnectTimeout('connect deadline exceeded', request=request)

    client = ModelClient(transport=httpx.MockTransport(timeout))
    with pytest.raises(ModelRequestError) as caught:
        asyncio.run(client.generate(profile(), payload()))
    assert caught.value.diagnostics == {'transport_error_type': 'ConnectTimeout'}
    assert caught.value.account == 'unknown'
    assert caught.value.usage.get('cache_read_tokens') is None
    assert str(caught.value).startswith('ConnectTimeout')


def test_abrupt_sse_close_preserves_metadata_and_usage_without_recording_delta():
    error = failed(sse(
        {'type': 'response.created', 'response': {'id': 'response-started'}},
        {'type': 'response.output_text.delta', 'delta': 'private-output-delta'},
        {'usage': {'input_tokens': 1024}}))
    assert error.diagnostics['event_type'] == 'stream_disconnected'
    assert error.diagnostics['response']['id'] == 'response-started'
    assert error.usage['input_tokens'] == 1024
    assert error.usage['first_token_latency_ms'] >= 0
    assert 'private-output-delta' not in json.dumps(error.diagnostics) + str(error)


@pytest.mark.parametrize('stream', [True, False])
def test_http_failure_preserves_status_safe_error_and_headers(stream):
    error = failed(json.dumps({'id': 'error-response', 'error': {
        'code': 'unsupported_parameter', 'type': 'invalid_request_error',
        'message': "Unsupported parameter: 'temperature'", 'param': 'temperature'},
        'usage': {'input_tokens': 100}}), status=400, stream=stream)
    assert model_error_summary(error, profile()) == "HTTPStatusError HTTP 400: Unsupported parameter: 'temperature'"
    assert error.diagnostics['http_status'] == 400
    assert error.diagnostics['error']['code'] == 'unsupported_parameter'
    assert error.usage['input_tokens'] == 100
    assert error.usage['cache_ratio'] is None


def test_large_http_error_body_is_not_captured():
    error = failed(json.dumps({'error': {'message': 'private-large-body' * 2000}}), status=502)
    assert str(error) == 'HTTPStatusError HTTP 502: Bad Gateway'
    assert 'error' not in error.diagnostics
    assert error.usage.get('input_tokens') is None


def test_success_reports_actual_response_metadata_and_default_fields_are_independent():
    body = sse({'type': 'response.completed', 'response': {
        'id': 'response-complete', 'model': 'actual-model', 'service_tier': 'default',
        'status': 'completed', 'output_text': 'successful output',
        'usage': {'input_tokens': 2048, 'output_tokens': 20,
                  'input_tokens_details': {'cached_tokens': 1024}}}})
    client = ModelClient(transport=httpx.MockTransport(lambda request:
        httpx.Response(200, text=body, headers=HEADERS)))
    result = asyncio.run(client.generate(profile(), payload()))
    assert result.text == 'successful output'
    assert result.diagnostics['response']['model'] == 'actual-model'
    assert result.diagnostics['response']['status'] == 'completed'
    assert result.usage['cache_read_tokens'] == 1024
    assert 'output_text' not in json.dumps(result.diagnostics)
    first, second = ModelResult('one', {}), ModelResult('two', {})
    first.diagnostics['changed'] = True
    assert second.diagnostics == {}
