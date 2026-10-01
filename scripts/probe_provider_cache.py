"""Run a synthetic, provider-neutral cache-cohort probe without QQ traffic."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

import httpx

from bot.services.agent_context import ContextEnvelope
from bot.services.tangtang_chat import TangtangProvider
from bot.services.tangtang_runtime import config_loader


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports"
REPORT_VERSION = "provider-cache-probe-v1"


class CacheProbeProvider(TangtangProvider):
    """Add an explicitly requested cache-affinity field to synthetic probes only."""

    def __init__(self, *, prompt_cache_key: bool = False) -> None:
        self._prompt_cache_key = bool(prompt_cache_key)

    def _with_probe_fields(
        self, payload: dict[str, Any], envelope: ContextEnvelope | None
    ) -> dict[str, Any]:
        if self._prompt_cache_key and envelope is not None:
            payload["prompt_cache_key"] = f"cache-probe-{envelope.static_prefix_hash}"
        return payload

    def _responses_payload(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        payload = super()._responses_payload(*args, **kwargs)
        return self._with_probe_fields(payload, kwargs.get("envelope"))

    def _chat_payload(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        payload = super()._chat_payload(*args, **kwargs)
        return self._with_probe_fields(payload, kwargs.get("envelope"))


def _persona(cohort: str, stable_chars: int) -> str:
    seed = f"[synthetic cache probe cohort={cohort}]\n"
    return (seed + "cache-stable synthetic line 0123456789\n" * stable_chars)[:stable_chars]


def offline_report(
    *,
    stable_chars: int = 16_000,
    ttl_seconds: int = 60,
    prompt_cache_key: bool = False,
) -> dict[str, Any]:
    stable_chars = max(4_096, min(int(stable_chars), 100_000))
    first = ContextEnvelope.create(
        persona=_persona("same", stable_chars),
        current_input="synthetic cold request",
    )
    second = ContextEnvelope.create(
        persona=_persona("same", stable_chars),
        conversation_items=(
            first.canonical_semantic_items()[-1],
            {"type": "message", "role": "assistant", "content": "synthetic answer one"},
        ),
        current_input="synthetic strict append",
    )
    different = ContextEnvelope.create(
        persona=_persona("different", stable_chars),
        current_input="synthetic different cohort",
    )
    return {
        "report_version": REPORT_VERSION,
        "mode": "offline",
        "status": "not_run",
        "privacy": "synthetic_counts_hashes_and_usage_only",
        "network_requests": 0,
        "vendor_fields_sent": bool(prompt_cache_key),
        "vendor_fields": ["prompt_cache_key"] if prompt_cache_key else [],
        "configured_ttl_seconds": max(0, int(ttl_seconds)),
        "scenarios": {
            "cold": {"static_prefix_hash": first.static_prefix_hash},
            "strict_append": {
                "static_prefix_hash": second.static_prefix_hash,
                "strict_prefix": second.canonical_semantic_items()[:2] == first.canonical_semantic_items(),
            },
            "ttl_append": {"static_prefix_hash": second.static_prefix_hash},
            "different_cohort": {"static_prefix_hash": different.static_prefix_hash},
        },
    }


def _usage(usage: Mapping[str, Any]) -> dict[str, Any]:
    prompt = max(0, int(usage.get("prompt_tokens") or 0))
    cache_read = usage.get("cache_read_tokens")
    reported = usage.get("cache_status") == "reported" and cache_read is not None
    return {
        "prompt_tokens": prompt,
        "latency_ms": max(0, int(usage.get("latency_ms") or 0)),
        "cache_status": "reported" if reported else "unsupported",
        "cache_read_tokens": max(0, int(cache_read or 0)) if reported else None,
        "cache_ratio": round(int(cache_read) / prompt, 4) if reported and prompt else None,
    }


async def live_report(
    *,
    stable_chars: int = 16_000,
    ttl_seconds: int = 60,
    prompt_cache_key: bool = False,
) -> dict[str, Any]:
    report = offline_report(
        stable_chars=stable_chars,
        ttl_seconds=ttl_seconds,
        prompt_cache_key=prompt_cache_key,
    )
    config = config_loader.load()
    if not config.enabled:
        raise ValueError(f"Tangtang provider is unavailable: {config.disabled_reason}")
    config = replace(
        config,
        reasoning_effort="low",
        max_output_tokens=min(max(config.max_output_tokens, 16), 64),
        max_response_chars=min(max(config.max_response_chars, 40), 256),
        timeout_seconds=min(max(config.timeout_seconds, 1), 45),
    )
    provider = CacheProbeProvider(prompt_cache_key=prompt_cache_key)
    persona = _persona("same", max(4_096, min(int(stable_chars), 100_000)))
    first = ContextEnvelope.create(persona=persona, current_input="synthetic cold request")
    rows: dict[str, dict[str, Any]] = {}

    async def request(name: str, envelope: ContextEnvelope) -> str:
        result = await provider.generate_agent(
            config, envelope.static_text, envelope.current_text, (), (), (), envelope
        )
        rows[name] = _usage(result.usage)
        return result.text

    try:
        answer_one = await request("cold", first)
        second = ContextEnvelope.create(
            persona=persona,
            conversation_items=(
                first.canonical_semantic_items()[-1],
                {"type": "message", "role": "assistant", "content": answer_one},
            ),
            current_input="synthetic strict append",
        )
        answer_two = await request("strict_append", second)
        if ttl_seconds > 0:
            await asyncio.sleep(min(int(ttl_seconds), 900))
        third = ContextEnvelope.create(
            persona=persona,
            conversation_items=(
                *second.canonical_semantic_items()[1:],
                {"type": "message", "role": "assistant", "content": answer_two},
            ),
            current_input="synthetic ttl append",
        )
        await request("ttl_append", third)
        different = ContextEnvelope.create(
            persona=_persona("different", max(4_096, min(int(stable_chars), 100_000))),
            current_input="synthetic different cohort",
        )
        await request("different_cohort", different)
    except httpx.HTTPError as exc:
        response = getattr(exc, "response", None)
        report.update(
            mode="live",
            status="failed",
            network_requests=len(rows) + 1,
            completed_scenarios=rows,
            error_type=type(exc).__name__,
            http_status=int(response.status_code) if response is not None else None,
        )
        return report

    report.update(
        mode="live",
        status="complete",
        network_requests=4,
        completed_scenarios=rows,
        capability={
            "generic_payload_only": not prompt_cache_key,
            "cache_reporting_available": all(
                row["cache_status"] == "reported" for row in rows.values()
            ),
            "prompt_cache_key_enabled": bool(prompt_cache_key),
            "vendor_affinity_enabled": False,
        },
    )
    return report


def _report_path(raw: Path) -> Path:
    path = raw if raw.is_absolute() else ROOT / raw
    resolved = path.resolve()
    try:
        resolved.relative_to(REPORT_DIR.resolve())
    except ValueError as exc:
        raise ValueError("output must be inside reports/") from exc
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="perform four paid synthetic requests")
    parser.add_argument("--stable-chars", type=int, default=16_000)
    parser.add_argument("--ttl-seconds", type=int, default=60)
    parser.add_argument(
        "--prompt-cache-key",
        action="store_true",
        help="send a synthetic cohort-derived prompt_cache_key for capability testing",
    )
    parser.add_argument("--output", type=Path, default=Path("reports/provider-cache-probe.json"))
    args = parser.parse_args()
    try:
        output = _report_path(args.output)
        report = asyncio.run(live_report(
            stable_chars=args.stable_chars,
            ttl_seconds=args.ttl_seconds,
            prompt_cache_key=args.prompt_cache_key,
        )) if args.live else offline_report(
            stable_chars=args.stable_chars,
            ttl_seconds=args.ttl_seconds,
            prompt_cache_key=args.prompt_cache_key,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError) as exc:
        print(f"Provider cache probe failed: {exc}")
        return 1
    print(f"Provider cache probe complete: mode={report['mode']}, report={output.relative_to(ROOT)}")
    return 0 if report.get("status") in {"complete", "not_run"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
