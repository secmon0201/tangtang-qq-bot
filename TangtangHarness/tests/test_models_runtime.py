import asyncio

import httpx

from tangtang_harness.config import ModelProfile
from tangtang_harness.models import ModelClient, build_payload, normalize_usage


def profile(**kwargs):
    return ModelProfile(id="test", name="Test", provider="custom", model="synthetic", base_url="https://example.invalid/v1", **kwargs)


def test_extra_provider_options_do_not_replace_compiled_context_or_output_budget():
    messages = [{'role': 'user', 'content': '实际上下文'}]
    settings = profile(cache_key_enabled=True, extra_body={'model': 'wrong', 'messages': [], 'input': [], 'max_tokens': 999,
                                   'prompt_cache_key': 'wrong', 'temperature': .2})
    payload = build_payload(settings, messages, cache_key='actual')
    assert payload['model'] == 'synthetic' and payload['messages'] == messages
    assert payload['max_completion_tokens'] == settings.max_output_tokens
    assert payload['prompt_cache_key'] == 'actual' and payload['temperature'] == .2
    assert 'max_tokens' not in payload and 'input' not in payload


def test_missing_cache_fields_remain_unknown():
    usage = normalize_usage({"usage": {"prompt_tokens": 100, "completion_tokens": 20}})
    assert usage["input_tokens"] == 100
    assert usage["cache_read_tokens"] is None
    assert usage["cache_write_tokens"] is None
    assert usage["cache_ratio"] is None
    assert usage["cache_status"] == "unknown"


def test_openai_cache_zero_is_known_and_partial_write_is_unknown():
    usage = normalize_usage({"usage": {"input_tokens": 100, "output_tokens": 20,
                                       "input_tokens_details": {"cached_tokens": 0}}})
    assert usage["cache_status"] == "reported"
    assert usage["cache_ratio"] == 0
    assert usage["cache_write_tokens"] is None
    write_only = normalize_usage({"usage": {"input_tokens": 100, "cache_write_tokens": 20}})
    assert write_only["cache_read_tokens"] is None
    assert write_only["cache_ratio"] is None


def test_anthropic_total_input_denominator_includes_split_components():
    usage = normalize_usage({"usage": {"input_tokens": 70, "output_tokens": 8,
                                       "cache_read_input_tokens": 50, "cache_creation_input_tokens": 10}})
    assert usage["input_tokens"] == 130
    assert usage["cache_ratio"] == 50 / 130
    assert usage["cache_miss_tokens"] == 80
    missing = normalize_usage({"usage": {"input_tokens": 70, "cache_read_input_tokens": 50}})
    assert missing["input_tokens"] is None


def test_openai_gateway_creation_counter_does_not_double_count_cached_input():
    usage = normalize_usage({'usage': {'prompt_tokens': 1720, 'completion_tokens': 11,
        'total_tokens': 1731, 'cache_creation_input_tokens': 0,
        'prompt_tokens_details': {'cached_tokens': 1536}}})
    assert usage['input_tokens'] == 1720 and usage['total_tokens'] == 1731
    assert usage['cache_miss_tokens'] == 184 and usage['cache_ratio'] == 1536 / 1720
    assert usage['input_semantics'] == 'total_input'


def test_gateway_visible_completion_with_separate_reasoning_reconciles_actual_total():
    usage = normalize_usage({'usage': {'prompt_tokens': 2018, 'completion_tokens': 1,
        'total_tokens': 2084, 'completion_tokens_details': {'reasoning_tokens': 65}}})
    assert usage['output_tokens'] == 66 and usage['reasoning_tokens'] == 65
    assert usage['total_tokens'] == usage['input_tokens'] + usage['output_tokens']
    assert usage['output_semantics'] == 'visible_plus_reasoning'


def test_known_cached_cost_requires_configured_prices():
    priced = profile(input_price_per_million=1, output_price_per_million=2, cache_read_price_per_million=.1)
    usage = normalize_usage({"usage": {"input_tokens": 100, "output_tokens": 20,
                                       "input_tokens_details": {"cached_tokens": 80}}}, priced)
    assert usage["cost"] == .000068
    assert normalize_usage({"usage": {"input_tokens": 100, "output_tokens": 20}}, priced)["cost"] is None


def test_http_request_uses_actual_payload_without_implicit_retries():
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "你好"}}],
                                            "usage": {"prompt_tokens": 10, "completion_tokens": 2}})

    client = ModelClient(transport=httpx.MockTransport(handle))
    actual = profile(api_key="synthetic-key")
    payload = build_payload(actual, [{"role": "user", "content": "合成输入"}])
    result = asyncio.run(client.generate(actual, payload))
    assert result.text == "你好"
    assert len(seen) == 1
    assert seen[0].url.path == "/v1/chat/completions"
    assert result.account == "unknown"
    assert result.usage["cache_ratio"] is None


def test_sse_stream_collects_text_and_final_usage():
    body = ('data: {"choices":[{"delta":{"content":"你"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"好"}}]}\n\n'
            'data: {"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":2}}\n\n'
            'data: [DONE]\n\n')
    client = ModelClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=body)))
    result = asyncio.run(client.generate(profile(), {"model": "synthetic", "stream": True, "messages": []}))
    assert result.text == "你好"
    assert result.usage["input_tokens"] == 10
