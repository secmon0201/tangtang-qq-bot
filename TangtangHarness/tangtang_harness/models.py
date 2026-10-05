"""HTTP model adapters with request-local usage and optional SSE streaming."""
from __future__ import annotations

import copy
import inspect
import json
import os
import re
import ssl
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from .billing import UsageBreakdown
import truststore

from .config import ModelProfile
from .redaction import SECRET_FIELDS


def model_ssl_context() -> ssl.SSLContext:
    """Use the Windows certificate store and native certificate-chain validation."""
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def model_error_summary(exc: Exception, profile: ModelProfile | None = None) -> str:
    """A short diagnosis for local records, without request or credential dumps."""
    if isinstance(exc, ModelRequestError):
        return str(exc)
    label, detail = type(exc).__name__, ''
    if isinstance(exc, httpx.HTTPStatusError):
        label += f' HTTP {exc.response.status_code}'
        detail = exc.response.reason_phrase
        try:
            if len(exc.response.content) <= 16384:
                body = exc.response.json()
                error = body.get('error', {}) if isinstance(body, dict) else {}
                if isinstance(error, dict) and isinstance(error.get('message'), str):
                    detail = error['message']
        except (ValueError, httpx.ResponseNotRead):
            pass
    else:
        cause = exc
        while cause.__cause__ is not None:
            cause = cause.__cause__
        detail = str(cause)
        if cause is not exc and type(cause) is not type(exc):
            detail = type(cause).__name__ + (': ' + detail if detail else '')

    wire = None
    if isinstance(exc, (httpx.HTTPStatusError, httpx.RequestError)):
        try:
            wire = json.loads(exc.request.content)
        except (ValueError, httpx.RequestNotRead):
            pass
    detail = _safe_detail(detail, profile, wire)
    return label + (': ' + detail if detail else '')


def _safe_detail(detail: str, profile: ModelProfile | None,
                 payload: Any = None, *, max_chars: int = 220) -> str:
    """Apply the same bounded redaction to summaries and allowlisted metadata."""
    if profile:
        key = profile.api_key or os.environ.get(profile.api_key_env, '')
        if key:
            detail = detail.replace(key, '[secret]')
        hostname = urlparse(profile.base_url).hostname
        if hostname:
            detail = detail.replace(hostname, '[endpoint]')
    detail = re.sub(r'https?://\S+|data:[^\s]+', '[url]', detail)
    detail = re.sub(r'(?i)\bBearer\s+[^\s,;]+|\bsk-[A-Za-z0-9_-]+', '[secret]', detail)
    detail = re.sub(r'(?i)\b(?:api[_ -]?key|authorization|access[_ -]?token|token|password|secret)\s*[:=]\s*[^,;\s]+', '[secret]', detail)
    detail = re.sub(r'\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b', '[identity]', detail)
    detail = re.sub(r'(?i)\b(?:user_id|group_id|account_id|organization_id|QQ)\s*[:=]\s*[\w.-]+', '[identity]', detail)
    detail = re.split(r'(?i)\b(?:request\s+body|payload|headers|prompt|messages)\s*[:=]', detail, maxsplit=1)[0]
    if isinstance(payload, dict):
        def redact_secrets(value: Any) -> None:
            nonlocal detail
            if isinstance(value, dict):
                for key, item in value.items():
                    if key.casefold() in SECRET_FIELDS and isinstance(item, str) and item:
                        detail = detail.replace(item, '[secret]')
                    else:
                        redact_secrets(item)
            elif isinstance(value, list):
                for item in value:
                    redact_secrets(item)
        redact_secrets(payload)
        for item in payload.get('messages', payload.get('input', [])):
            if isinstance(item, dict):
                content = item.get('content', '')
                texts = ([content] if isinstance(content, str) else
                         [part.get('text', '') for part in content if isinstance(part, dict)]
                         if isinstance(content, list) else [])
                encrypted = item.get('encrypted_content')
                if isinstance(encrypted, str) and encrypted:
                    detail = detail.replace(encrypted, '[reasoning state]')
                for text in texts:
                    if not isinstance(text, str):
                        continue
                    for line in text.splitlines():
                        if len(line.strip()) >= 4:
                            detail = detail.replace(line.strip(), '[request text]')
                    for name, identity in re.findall(r'说话人：([^\n]+?)（(\d+)）', text):
                        detail = detail.replace(name, '[identity]').replace(identity, '[identity]')
        instructions = payload.get('instructions')
        if isinstance(instructions, str):
            for line in instructions.splitlines():
                if len(line.strip()) >= 4:
                    detail = detail.replace(line.strip(), '[request text]')
    detail = ' '.join(detail.split())[:max_chars]
    if re.search(r'certificate has expired|NotTimeValid|not valid (?:at this time|yet)', detail, re.I):
        detail = 'HTTPS证书已过期或不在有效期内；' + detail
    return detail


DIAGNOSTIC_HEADERS = frozenset({
    'x-request-id', 'request-id', 'openai-request-id', 'x-upstream-request-id',
    'x-backend-account', 'x-backend-channel', 'x-channel-id',
})


class ModelRequestError(ValueError):
    """A safe request failure, carrying observed metadata and usage separately."""
    def __init__(self, summary: str, *, diagnostics: dict[str, Any],
                 usage: dict[str, Any], account: str = 'unknown') -> None:
        super().__init__(summary)
        self.diagnostics = diagnostics
        self.usage = usage
        self.account = account


def _safe_fields(source: dict[str, Any], names: tuple[str, ...],
                 profile: ModelProfile, payload: dict[str, Any],
                 previous: dict[str, Any] | None = None) -> dict[str, Any]:
    result = {}
    for name in names:
        value = source.get(name)
        if name in source and (value is None or isinstance(value, (str, int, float, bool))):
            if isinstance(value, str) and len(value) > 16_384 and name != 'message':
                continue
            result[name] = (previous[name] if previous and previous.get(name) == value else
                            _safe_detail(value, profile, payload, max_chars=4096 if name == 'message' else 220)
                            if isinstance(value, str) else value)
    return result


def _observe_diagnostics(diagnostics: dict[str, Any], data: dict[str, Any],
                         profile: ModelProfile, payload: dict[str, Any]) -> None:
    nested = data.get('response')
    response = nested if isinstance(nested, dict) else data
    fields = _safe_fields(response, ('id', 'model', 'service_tier', 'status'), profile, payload,
                          diagnostics.get('response'))
    if fields:
        diagnostics.setdefault('response', {}).update(fields)
    if data.get('type') in {'response.created', 'response.in_progress', 'response.completed',
                            'response.failed', 'response.incomplete', 'error'}:
        diagnostics['event_type'] = data['type']
    error = response.get('error') or data.get('error')
    if data.get('type') == 'error' and not error:
        error = data
    if isinstance(error, dict):
        diagnostics['error'] = _safe_fields(error, ('code', 'type', 'message', 'param'), profile, payload)
    elif isinstance(error, str):
        diagnostics['error'] = {'message': _safe_detail(error, profile, payload, max_chars=4096)}
    incomplete = response.get('incomplete_details') or data.get('incomplete_details')
    if isinstance(incomplete, dict):
        diagnostics['incomplete_details'] = _safe_fields(incomplete, ('reason',), profile, payload)


def _reported_usage(data: dict[str, Any], target: dict[str, Any]) -> None:
    """Retain only actually received usage objects, including failed SSE events."""
    def merge(previous: dict[str, Any], received: dict[str, Any]) -> None:
        for name, value in received.items():
            if isinstance(value, dict):
                if not isinstance(previous.get(name), dict):
                    previous[name] = {}
                merge(previous[name], value)
            elif value is not None or name not in previous:
                previous[name] = value

    nested = data.get('response')
    for source in (data, nested if isinstance(nested, dict) else {}):
        for key in ('usage', 'usage_metadata', 'usageMetadata'):
            if isinstance(source.get(key), dict):
                # A later partial/empty final frame must not erase counters
                # already observed. Counters replace, never add to each other.
                merge(target.setdefault('usage', {}), source[key])


def _number(*values: Any) -> int | None:
    for value in values:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return int(value)
    return None


def normalize_usage(data: dict[str, Any], profile: ModelProfile | None = None) -> dict[str, Any]:
    usage: dict[str, Any] = {}
    for key in ("usage", "usage_metadata", "usageMetadata"):
        if isinstance(data.get(key), dict):
            usage.update(data[key])
    if not usage:
        return {"input_tokens": None, "output_tokens": None, "reasoning_tokens": None,
                "total_tokens": None, "cache_read_tokens": None, "cache_write_tokens": None,
                "cache_miss_tokens": None, "cache_status": "unknown", "cache_ratio": None,
                "cost": None, "raw": {}}
    details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
    output_details = usage.get("output_tokens_details") or usage.get("completion_tokens_details") or {}
    read = _number(details.get("cached_tokens"), details.get("cachedTokens"),
                   usage.get("cached_tokens"), usage.get("cachedTokens"),
                   usage.get("cache_read_tokens"), usage.get("cache_read_input_tokens"),
                   usage.get("cacheReadInputTokens"), usage.get("prompt_cache_hit_tokens"),
                   usage.get("cachedContentTokenCount"))
    write = _number(usage.get("cache_creation_input_tokens"), usage.get("cache_write_input_tokens"),
                    usage.get("cache_write_tokens"), usage.get("cacheCreationInputTokens"),
                    usage.get("prompt_cache_write_tokens"))
    explicit_miss = _number(usage.get("cache_miss_input_tokens"), usage.get("cache_miss_tokens"),
                           usage.get("prompt_cache_miss_tokens"))
    input_tokens = _number(usage.get("input_tokens"), usage.get("prompt_tokens"),
                           usage.get("inputTokens"), usage.get("promptTokenCount"))
    semantics = profile.usage_semantics if profile else "auto"
    native_anthropic = semantics == "anthropic" or (
        semantics == "auto" and 'prompt_tokens' not in usage
        and not any(key in details for key in ('cached_tokens', 'cachedTokens'))
        and any(key in usage for key in ("cache_read_input_tokens", "cache_creation_input_tokens")))
    if native_anthropic and input_tokens is not None:
        # Anthropic's input_tokens excludes separately billed read/write input.
        # Missing native components stay unknown rather than being filled with 0.
        if read is not None and write is not None:
            input_tokens += read + write
        else:
            input_tokens = None
    output = _number(usage.get("output_tokens"), usage.get("completion_tokens"),
                     usage.get("outputTokens"), usage.get("candidatesTokenCount"))
    reasoning = _number(output_details.get("reasoning_tokens"), output_details.get("reasoningTokens"),
                        usage.get("thoughtsTokenCount"))
    if "candidatesTokenCount" in usage and "thoughtsTokenCount" in usage and output is not None:
        output += reasoning or 0
    total = _number(usage.get("total_tokens"), usage.get("totalTokenCount"))
    output_semantics = 'reported_total_output'
    if (input_tokens is not None and output is not None and reasoning is not None and reasoning > 0
            and total == input_tokens + output + reasoning):
        output += reasoning
        output_semantics = 'visible_plus_reasoning'
    if native_anthropic or total is None:
        total = input_tokens + output if input_tokens is not None and output is not None else None
    miss = explicit_miss
    if miss is None and input_tokens is not None and read is not None:
        # This legacy normalized field includes separately reported writes;
        # billing.py splits the write component before pricing it.
        miss = max(0, input_tokens - read)
    ratio = read / input_tokens if read is not None and input_tokens and read <= input_tokens else None
    result = {"input_tokens": input_tokens, "output_tokens": output, "reasoning_tokens": reasoning,
              "total_tokens": total, "cache_read_tokens": read, "cache_write_tokens": write,
              "cache_miss_tokens": miss, "cache_status": "reported" if read is not None else "unknown",
              "cache_ratio": ratio, "cost": None, "raw": usage,
              'output_semantics': output_semantics,
              "input_semantics": "anthropic_split" if native_anthropic else "total_input"}
    if profile:
        result["cost"] = estimate_cost(result, profile)
    return result


def estimate_cost(usage: dict[str, Any], profile: ModelProfile) -> float | None:
    input_tokens, output = usage.get("input_tokens"), usage.get("output_tokens")
    read, write = usage.get("cache_read_tokens"), usage.get("cache_write_tokens")
    if input_tokens is None or output is None or profile.input_price_per_million is None or profile.output_price_per_million is None:
        return None
    if read is None:
        return None
    if read and profile.cache_read_price_per_million is None:
        return None
    if write and profile.cache_write_price_per_million is None:
        return None
    write = write or 0
    fresh = max(0, input_tokens - read - write)
    return round((fresh * profile.input_price_per_million
                  + read * (profile.cache_read_price_per_million or 0)
                  + write * (profile.cache_write_price_per_million or 0)
                  + output * profile.output_price_per_million) / 1_000_000, 8)


def _response_content(content: Any, role: str) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        return [{"type": "output_text" if role == "assistant" else "input_text", "text": str(content or "")}]
    result = []
    for part in content:
        if part.get("type") == "image_url":
            image = part["image_url"]
            result.append({"type": "input_image", "image_url": image["url"], "detail": image.get("detail", "high")})
        else:
            result.append({"type": "output_text" if role == "assistant" else "input_text", "text": str(part.get("text", ""))})
    return result


def responses_reasoning_replay_enabled(profile: ModelProfile) -> bool:
    """An explicit provider option opts in; reasoning none keeps its wire body."""
    include = profile.extra_body.get('include')
    return (profile.api_style == 'responses'
            and bool(profile.reasoning_effort) and profile.reasoning_effort != 'none'
            and isinstance(include, list) and 'reasoning.encrypted_content' in include)


def response_input_items(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"role": item["role"], "content": _response_content(item["content"], item["role"])}
            for item in messages]


def build_payload(profile: ModelProfile, messages: list[dict[str, Any]], *, cache_key: str = "",
                  response_input: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"model": profile.model}
    if cache_key and profile.cache_key_enabled:
        payload["prompt_cache_key"] = cache_key
    if profile.api_style == "responses":
        payload["input"] = (copy.deepcopy(response_input) if response_input is not None
                            else response_input_items(messages))
        payload["max_output_tokens"] = profile.max_output_tokens
        if profile.reasoning_effort:
            payload["reasoning"] = {"effort": profile.reasoning_effort}
    else:
        payload["messages"] = messages
        payload[profile.output_token_field] = profile.max_output_tokens
        if profile.reasoning_effort and profile.reasoning_effort != "none":
            payload["reasoning_effort"] = profile.reasoning_effort
    compiled_fields = {'model', 'messages', 'input', 'max_tokens', 'max_completion_tokens',
                       'max_output_tokens', 'reasoning', 'reasoning_effort', 'prompt_cache_key'}
    payload.update({key: value for key, value in profile.extra_body.items() if key not in compiled_fields})
    if profile.api_style == 'chat_completions' and payload.get('stream'):
        payload.setdefault('stream_options', {'include_usage': True})
    return payload


def model_endpoint(profile: ModelProfile) -> str:
    url = profile.base_url.rstrip("/")
    suffix = "/responses" if profile.api_style == "responses" else "/chat/completions"
    return url if url.endswith(suffix) else url + suffix


def extract_text(data: dict[str, Any]) -> str:
    if isinstance(data.get("output_text"), str):
        return data["output_text"]
    choices = data.get("choices", ())
    if choices:
        content = choices[0].get("message", {}).get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(str(part.get("text", "")) for part in content if part.get("type") == "text")
    return "".join(str(part.get("text", ""))
                   for item in (data.get("output") or ()) if item.get("type") == "message"
                   for part in (item.get("content") or ()) if part.get("type") in {"output_text", "text"})


@dataclass(slots=True)
class ModelResult:
    text: str
    usage: dict[str, Any]
    raw: dict[str, Any] = field(default_factory=dict)
    account: str = "unknown"
    diagnostics: dict[str, Any] = field(default_factory=dict)
    response_output_complete: bool = False
    response_output_source: str = 'not_reported'


class ModelClient:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport
        self.ssl_context = model_ssl_context()

    async def generate(self, profile: ModelProfile, payload: dict[str, Any],
                       *, on_delta: Callable[[str], Any] | None = None) -> ModelResult:
        headers = {"Content-Type": "application/json"}
        api_key = profile.api_key or (os.environ.get(profile.api_key_env, "") if profile.api_key_env else "")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        started = time.monotonic()
        first_token_latency_ms = None
        diagnostics: dict[str, Any] = {}
        usage_data: dict[str, Any] = {}
        final: dict[str, Any] = {}
        response_output_complete = False
        response_output_source = 'not_reported'
        timeout = httpx.Timeout(profile.timeout_seconds, connect=min(10, profile.timeout_seconds))

        def observe_response(response: httpx.Response) -> None:
            diagnostics['http_status'] = response.status_code
            allowed = {name: _safe_detail(value, profile, payload)
                       for name, value in response.headers.items() if name in DIAGNOSTIC_HEADERS}
            if allowed:
                diagnostics['headers'] = allowed

        async def check_status(response: httpx.Response) -> None:
            observe_response(response)
            if not response.is_success:
                await response.aread()
                if len(response.content) <= 16_384:
                    try:
                        data = response.json()
                        if isinstance(data, dict):
                            _observe_diagnostics(diagnostics, data, profile, payload)
                            _reported_usage(data, usage_data)
                    except ValueError:
                        pass
                else:
                    diagnostics['error_body'] = 'omitted_body_over_16kib'
            response.raise_for_status()

        def check_event(data: dict[str, Any], *, streaming: bool) -> None:
            _observe_diagnostics(diagnostics, data, profile, payload)
            _reported_usage(data, usage_data)
            nested = data.get('response')
            body = nested if isinstance(nested, dict) else data
            if (data.get('error') or body.get('error')
                    or data.get('type') in {'error', 'response.failed', 'response.incomplete'}
                    or body.get('status') in {'failed', 'incomplete'}):
                label = '模型流未完成' if streaming else '模型响应未完成'
                reason = diagnostics.get('event_type') or body.get('status') or 'error'
                error = diagnostics.get('error', {})
                code = error.get('code')
                detail = error.get('message') or diagnostics.get(
                    'incomplete_details', {}).get('reason')
                raise ValueError(label + '：' + reason + (f' [{code}]' if code is not None else '')
                                 + (': ' + detail if detail else ''))

        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=timeout,
                                         verify=self.ssl_context) as client:
                if payload.get("stream"):
                    async with client.stream("POST", model_endpoint(profile), headers=headers, json=payload) as response:
                        await check_status(response)
                        pieces: list[str] = []
                        completed = False
                        responses_terminal = False
                        done_items: dict[int, dict[str, Any]] = {}
                        seen_items: set[int] = set()
                        async for line in response.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            value = line[5:].strip()
                            if value == "[DONE]":
                                completed = True
                                continue
                            if not value:
                                continue
                            chunk = json.loads(value)
                            if not isinstance(chunk, dict):
                                raise ValueError('模型流响应格式无效')
                            check_event(chunk, streaming=True)
                            if chunk.get('type') in {'response.output_item.added', 'response.output_item.done'}:
                                index, item = chunk.get('output_index'), chunk.get('item')
                                if type(index) is int and index >= 0 and isinstance(item, dict):
                                    seen_items.add(index)
                                    if chunk['type'] == 'response.output_item.done':
                                        done_items[index] = copy.deepcopy(item)
                            delta = ""
                            if chunk.get("type") == "response.output_text.delta":
                                delta = str(chunk.get("delta", ""))
                            elif chunk.get("choices"):
                                delta = str(chunk["choices"][0].get("delta", {}).get("content") or "")
                            if isinstance(chunk.get("usage"), dict):
                                final["usage"] = chunk["usage"]
                            if chunk.get("type") == "response.completed":
                                final = chunk.get("response", {})
                                if not isinstance(final, dict):
                                    raise ValueError('模型流响应格式无效')
                                responses_terminal = True
                                completed = True
                            if any(choice.get('finish_reason') for choice in chunk.get('choices', [])):
                                completed = True
                            if delta:
                                if first_token_latency_ms is None:
                                    first_token_latency_ms = round((time.monotonic() - started) * 1000, 2)
                                pieces.append(delta)
                                if on_delta:
                                    result = on_delta(delta)
                                    if inspect.isawaitable(result):
                                        await result
                        if not completed:
                            diagnostics['event_type'] = 'stream_disconnected'
                            raise ValueError('模型连接在完整回复前断开')
                        text = extract_text(final) or "".join(pieces)
                        # Preserve the existing visible-text path. Item events
                        # enrich only the local output archive after text is fixed.
                        if profile.api_style == 'responses':
                            if isinstance(final.get('output'), list) and final['output']:
                                response_output_complete = responses_terminal
                                response_output_source = 'completed_output'
                            elif done_items:
                                indices = sorted(done_items)
                                final = copy.deepcopy(final)
                                final['output'] = [done_items[index] for index in indices]
                                response_output_complete = (responses_terminal and set(indices) == seen_items
                                                            and indices == list(range(len(indices))))
                                response_output_source = 'output_item.done'
                            elif isinstance(final.get('output'), list):
                                response_output_complete = responses_terminal
                                response_output_source = 'completed_output'
                else:
                    # Observe headers before reading a non-SSE body as well:
                    # an interrupted body must not discard a received request ID.
                    async with client.stream('POST', model_endpoint(profile), headers=headers,
                                             json=payload) as response:
                        await check_status(response)
                        await response.aread()
                        final = response.json()
                        if not isinstance(final, dict):
                            raise ValueError('模型响应格式无效')
                        check_event(final, streaming=False)
                        response_output_complete = (profile.api_style == 'responses'
                                                    and final.get('status') == 'completed'
                                                    and isinstance(final.get('output'), list))
                        if profile.api_style == 'responses' and isinstance(final.get('output'), list):
                            response_output_source = 'json_output'
                        text = extract_text(final)
        except (httpx.HTTPError, ValueError) as exc:
            if isinstance(exc, httpx.RequestError):
                diagnostics['transport_error_type'] = type(exc).__name__
            summary = model_error_summary(exc, profile)
            # SSE errors have no httpx.Request on their ValueError, so apply the
            # identical payload-aware redaction before exposing the summary.
            if not isinstance(exc, httpx.HTTPError):
                summary = _safe_detail(summary, profile, payload)
            usage = self._usage(usage_data, profile, started, first_token_latency_ms,
                                failure=True)
            raise ModelRequestError(summary, diagnostics=diagnostics, usage=usage,
                                    account=diagnostics.get('headers', {}).get(
                                        'x-backend-account') or 'unknown') from None
        usage = self._usage(usage_data, profile, started, first_token_latency_ms)
        return ModelResult(text=text, usage=usage, raw=final,
                           account=diagnostics.get('headers', {}).get('x-backend-account') or 'unknown',
                           diagnostics=diagnostics, response_output_complete=response_output_complete,
                           response_output_source=response_output_source)

    @staticmethod
    def _usage(data: dict[str, Any], profile: ModelProfile, started: float,
               first_token_latency_ms: float | None, *, failure: bool = False) -> dict[str, Any]:
        usage = normalize_usage(data, profile) if data or not failure else {}
        # Keep a component ledger alongside the provider-normalized usage. It
        # is an estimate from the configured prices, not the provider bill.
        if data or not failure:
            usage["billing"] = UsageBreakdown.from_usage(usage, profile).to_dict()
        usage["latency_ms"] = round((time.monotonic() - started) * 1000, 2)
        usage['first_token_latency_ms'] = first_token_latency_ms
        return usage
