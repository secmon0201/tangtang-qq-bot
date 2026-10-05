"""Isolated live-provider evidence; never changes the running instance or sends QQ."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import uuid
from dataclasses import replace
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx
from dotenv import load_dotenv

from tangtang_harness.config import DEFAULT_ROOT, load_config
from tangtang_harness.models import ModelClient, build_payload, model_ssl_context
from tangtang_harness.store import Store
from tangtang_harness.types import InboundEvent


PUBLIC_FIELDS = {'id', 'name', 'model', 'object', 'owned_by', 'context_length', 'context_window',
    'context_window_tokens', 'max_input_tokens', 'max_output_tokens', 'input_token_limit',
    'output_token_limit', 'inputTokenLimit', 'outputTokenLimit', 'pricing', 'usage_semantics',
    'tokenizer', 'capabilities', 'architecture', 'limits', 'supported_parameters', 'description'}
DOCS = {
    'openai': ('https://developers.openai.com/api/docs/models', 'gpt-5.6-luna'),
    'deepseek': ('https://api-docs.deepseek.com/quick_start/pricing', 'deepseek-v4.1-flash'),
    'gemini': ('https://ai.google.dev/gemini-api/docs/models', 'gemini-3.8-flash-high'),
}


def sanitized(value):
    if isinstance(value, dict):
        return {key: sanitized(item) for key, item in value.items()
                if not re.search(r'key|secret|token$|authorization|url|host|account', key, re.I)}
    if isinstance(value, list):
        return [sanitized(item) for item in value]
    if isinstance(value, str):
        return re.sub(r'https?://\S+', '[url]', value)[:1200]
    return value


def error_text(exc, profile):
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            data = exc.response.json()
            item = data.get('error', data)
            message = item.get('message', '') if isinstance(item, dict) else str(item)
        except (ValueError, AttributeError):
            message = ''
        secret = profile.api_key or os.environ.get(profile.api_key_env, '')
        if secret:
            message = message.replace(secret, '[secret]')
        return {'error_type': type(exc).__name__, 'http_status': exc.response.status_code,
                'message': re.sub(r'https?://\S+', '[url]', message)[:200]}
    return {'error_type': type(exc).__name__}


async def model_metadata(profile):
    base = profile.base_url.rstrip('/')
    for suffix in ('/chat/completions', '/responses'):
        if base.endswith(suffix):
            base = base[:-len(suffix)]
    api_key = profile.api_key or os.environ.get(profile.api_key_env, '')
    headers = {'Authorization': 'Bearer ' + api_key} if api_key else {}
    report = {'profile_id': profile.id, 'provider': profile.provider, 'model': profile.model}
    report['official_billing_endpoint'] = urlparse(base).hostname in {'api.deepseek.com', 'api.openai.com', 'generativelanguage.googleapis.com'}
    async with httpx.AsyncClient(timeout=20, verify=model_ssl_context()) as client:
        try:
            response = await client.get(base + '/models', headers=headers)
            response.raise_for_status()
            data = response.json()
            rows = data.get('data', data.get('models', [])) if isinstance(data, dict) else data
            row = next((item for item in rows if isinstance(item, dict)
                        and item.get('id', item.get('name', '')).removeprefix('models/') == profile.model), None)
            report.update({'http_status': response.status_code, 'listed_model_count': len(rows), 'exact_model_found': bool(row),
                           'metadata': sanitized({key: value for key, value in (row or {}).items() if key in PUBLIC_FIELDS})})
            if row is None or not any(key in row for key in ('context_window', 'context_length', 'input_token_limit', 'inputTokenLimit')):
                single = await client.get(base + '/models/' + quote(profile.model, safe=''), headers=headers)
                report['single_model_http_status'] = single.status_code
                if single.is_success:
                    report['single_model_metadata'] = sanitized({key: value for key, value in single.json().items() if key in PUBLIC_FIELDS})
            if profile.provider == 'antigravity':
                native_root = base[:-3] if base.endswith('/v1') else base
                native = await client.get(native_root + '/v1beta/models/' + quote(profile.model, safe=''), headers=headers)
                report['native_model_http_status'] = native.status_code
                if native.is_success:
                    report['native_model_metadata'] = sanitized({key: value for key, value in native.json().items() if key in PUBLIC_FIELDS})
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            report.update(error_text(exc, profile))
    return report


async def official_docs(name, url, exact):
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, verify=model_ssl_context()) as client:
        try:
            response = await client.get(url)
            text = re.sub(r'<[^>]*>', ' ', response.text)
            at = text.lower().find(exact.lower())
            return {'provider': name, 'source': url, 'http_status': response.status_code,
                    'exact_requested_model_found': at >= 0,
                    'matching_excerpt': re.sub(r'\s+', ' ', text[max(0, at - 100):at + 600]) if at >= 0 else ''}
        except httpx.HTTPError as exc:
            return {'provider': name, 'source': url, 'error_type': type(exc).__name__}


async def paid_cases(profile, store):
    # A synthetic prefix is intentionally long enough to expose ordinary cache thresholds.
    marker = uuid.uuid4().hex
    prefix = 'Synthetic cache verification ' + marker + '\n' + '\n'.join(
        f'Fact {index:03d}: blue triangles remain blue; this is synthetic benchmark material.' for index in range(110))
    original = [{'role': 'system', 'content': prefix}, {'role': 'user', 'content': 'Reply with OK only.'}]
    cases = [('cold', original), ('append', [*original, {'role': 'assistant', 'content': 'OK'},
             {'role': 'user', 'content': 'Again reply with OK only.'}]),
             ('prefix_changed', [{**original[0], 'content': 'Changed prefix.\n' + prefix}, original[1]]),
             ('compacted', [{'role': 'system', 'content': 'Synthetic summary: all facts concerned blue triangles.'}, original[1]]),
             ('manual_warm', original), ('after_manual_warm', original)]
    selected = replace(profile, max_output_tokens=128, timeout_seconds=60,
                       extra_body={**profile.extra_body, 'stream': False})
    results = []
    client = ModelClient()
    for name, messages in cases:
        event = InboundEvent(marker + ':' + name, 999, 101, None, 'Synthetic provider verification')
        payload = build_payload(selected, messages, cache_key='probe:' + profile.id + ':' + marker)
        request_id = store.add_request(event, selected, payload, purpose='verification:' + name)
        started = time.monotonic()
        try:
            response = await client.generate(selected, payload)
            store.finish_request(request_id, usage=response.usage, outcome='verified', messages=[response.text], account=response.account)
            results.append({'case': name, 'status': 'verified', 'request_id': request_id,
                            'response_model': response.raw.get('model'), 'response_chars': len(response.text),
                            'usage': response.usage})
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            detail = error_text(exc, profile)
            store.finish_request(request_id, outcome='failed', error=detail['error_type'])
            results.append({'case': name, 'status': 'failed', 'request_id': request_id,
                            'latency_ms': round((time.monotonic() - started) * 1000, 2), **detail})
            # One failed configured endpoint is enough evidence; do not keep spending on it.
            break
    return {'profile_id': profile.id, 'provider': profile.provider, 'model': profile.model,
            'configured_output_limit': profile.max_output_tokens, 'probe_output_limit': 128,
            'reasoning_effort': profile.reasoning_effort, 'cases': results}


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--paid', action='store_true')
    parser.add_argument('--profile', action='append', default=[])
    args = parser.parse_args()
    config = load_config(args.root)
    load_dotenv(args.root / '.env', override=False)
    profiles = [profile for profile in config.profiles if not args.profile or profile.id in args.profile]
    directory = args.root / 'runtime' / 'verification' / ('model-probe-' + uuid.uuid4().hex[:12])
    directory.mkdir(parents=True)
    metadata = await asyncio.gather(*(model_metadata(profile) for profile in profiles))
    docs = await asyncio.gather(*(official_docs(name, url, exact) for name, (url, exact) in DOCS.items()))
    report = {'metadata': metadata, 'official_docs': docs, 'paid': []}
    if args.paid:
        store = Store(directory)
        for profile in profiles:
            value = await paid_cases(profile, store)
            report['paid'].append(value)
            print(json.dumps({'profile_id': profile.id, 'completed_cases': len(value['cases'])}), flush=True)
    path = directory / 'report.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print('Report: ' + str(path.relative_to(args.root)))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    asyncio.run(main())
