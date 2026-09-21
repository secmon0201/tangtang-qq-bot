"""Build or explicitly run a synthetic Agent prompt-cache benchmark."""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

import httpx

from bot.services.agent_context import ContextEnvelope
from bot.services.agent_tools import tool_schemas
from bot.services.tangtang_chat import TangtangProvider
from bot.services.tangtang_runtime import config_loader


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports"
REPORT_VERSION = "agent-cache-benchmark-v1"


def build_envelopes(*, calls: int, stable_chars: int) -> tuple[ContextEnvelope, ...]:
    calls = max(2, min(int(calls), 10))
    stable_chars = max(4_096, min(int(stable_chars), 100_000))
    unit = "stable synthetic cache benchmark line 0123456789\n"
    persona = ("[synthetic benchmark persona]\n" + unit * (stable_chars // len(unit) + 1))[
        :stable_chars
    ]
    tools = tool_schemas(include_actions=True)
    return tuple(
        ContextEnvelope.create(
            persona=persona,
            dynamic_status="synthetic_state=cache_benchmark",
            current_input=f"synthetic request sequence {index}; do not call tools; answer [silent]",
            tools=tools,
        )
        for index in range(calls)
    )


def offline_report(*, calls: int = 3, stable_chars: int = 16_000) -> dict[str, Any]:
    envelopes = build_envelopes(calls=calls, stable_chars=stable_chars)
    first = envelopes[0]
    return {
        "report_version": REPORT_VERSION,
        "mode": "offline",
        "status": "not_run",
        "privacy": "synthetic_counts_and_hashes_only",
        "network_requests": 0,
        "call_count": len(envelopes),
        "warmup_calls": 1,
        "measured_calls": len(envelopes) - 1,
        "tool_count": len(tool_schemas(include_actions=True)),
        "static_prefix_chars": first.layer_sizes()["static_prefix_chars"],
        "static_prefix_hash": first.static_prefix_hash,
        "tool_schema_hash": first.tool_schema_hash,
        "stable_prefix_across_calls": len({item.static_prefix_hash for item in envelopes}) == 1,
        "target_cache_ratio": 0.90,
        "measured_cache_ratio": None,
        "target_met": None,
    }


def _usage_row(index: int, usage: Mapping[str, Any], *, warmup: bool) -> dict[str, Any]:
    status = "reported" if usage.get("cache_status") == "reported" else "unsupported"
    prompt = max(0, int(usage.get("prompt_tokens") or 0))
    row: dict[str, Any] = {
        "sequence": index,
        "warmup": warmup,
        "prompt_tokens": prompt,
        "completion_tokens": max(0, int(usage.get("completion_tokens") or 0)),
        "latency_ms": max(0, int(usage.get("latency_ms") or 0)),
        "cache_status": status,
    }
    if status == "reported":
        cache_read = max(0, int(usage.get("cache_read_tokens") or 0))
        row.update(
            cache_read_tokens=cache_read,
            cache_write_tokens=max(0, int(usage.get("cache_write_tokens") or 0)),
            cache_miss_tokens=max(0, int(usage.get("cache_miss_tokens") or 0)),
            cache_ratio=round(cache_read / prompt, 4) if prompt else None,
        )
    return row


async def live_report(*, calls: int = 3, stable_chars: int = 16_000) -> dict[str, Any]:
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
    provider = TangtangProvider()
    tools = tool_schemas(include_actions=True)
    envelopes = build_envelopes(calls=calls, stable_chars=stable_chars)
    rows: list[dict[str, Any]] = []
    for index, envelope in enumerate(envelopes, 1):
        try:
            result = await provider.generate_agent(
                config,
                envelope.static_text,
                envelope.current_text,
                tools,
                (),
                (),
                envelope,
            )
        except httpx.HTTPError as exc:
            response = getattr(exc, "response", None)
            return {
                "report_version": REPORT_VERSION,
                "mode": "live",
                "status": "failed",
                "privacy": "synthetic_usage_counts_and_hashes_only",
                "network_requests": index,
                "api_style": config.api_style,
                "model": config.model,
                "call_count": len(envelopes),
                "completed_calls": len(rows),
                "warmup_calls": 1,
                "measured_calls": 0,
                "tool_count": len(tools),
                "static_prefix_chars": envelopes[0].layer_sizes()["static_prefix_chars"],
                "static_prefix_hash": envelopes[0].static_prefix_hash,
                "tool_schema_hash": envelopes[0].tool_schema_hash,
                "stable_prefix_across_calls": True,
                "target_cache_ratio": 0.90,
                "measured_cache_ratio": None,
                "target_met": None,
                "error_type": type(exc).__name__,
                "http_status": int(response.status_code) if response is not None else None,
                "calls": rows,
            }
        row = _usage_row(index, result.usage, warmup=index == 1)
        row["tool_call_count"] = len(result.tool_calls)
        rows.append(row)

    measured = rows[1:]
    ratios = [float(row["cache_ratio"]) for row in measured if row.get("cache_ratio") is not None]
    prompt_total = sum(int(row["prompt_tokens"]) for row in measured if row["cache_status"] == "reported")
    cache_total = sum(int(row.get("cache_read_tokens") or 0) for row in measured if row["cache_status"] == "reported")
    weighted = round(cache_total / prompt_total, 4) if prompt_total else None
    return {
        "report_version": REPORT_VERSION,
        "mode": "live",
        "status": "complete",
        "privacy": "synthetic_usage_counts_and_hashes_only",
        "network_requests": len(rows),
        "api_style": config.api_style,
        "model": config.model,
        "call_count": len(rows),
        "warmup_calls": 1,
        "measured_calls": len(measured),
        "tool_count": len(tools),
        "static_prefix_chars": envelopes[0].layer_sizes()["static_prefix_chars"],
        "static_prefix_hash": envelopes[0].static_prefix_hash,
        "tool_schema_hash": envelopes[0].tool_schema_hash,
        "stable_prefix_across_calls": len({item.static_prefix_hash for item in envelopes}) == 1,
        "target_cache_ratio": 0.90,
        "measured_cache_ratio": weighted,
        "median_cache_ratio": round(float(statistics.median(ratios)), 4) if ratios else None,
        "target_met": weighted is not None and weighted >= 0.90,
        "cache_status": (
            "reported" if measured and all(row["cache_status"] == "reported" for row in measured)
            else "unsupported" if measured and all(row["cache_status"] == "unsupported" for row in measured)
            else "mixed"
        ),
        "calls": rows,
    }


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
    parser.add_argument("--live", action="store_true", help="perform paid provider requests")
    parser.add_argument("--calls", type=int, default=3)
    parser.add_argument("--stable-chars", type=int, default=16_000)
    parser.add_argument("--output", type=Path, default=Path("reports/agent-cache-benchmark.json"))
    args = parser.parse_args()
    try:
        output = _report_path(args.output)
        report = (
            asyncio.run(live_report(calls=args.calls, stable_chars=args.stable_chars))
            if args.live
            else offline_report(calls=args.calls, stable_chars=args.stable_chars)
        )
    except (OSError, ValueError) as exc:
        print(f"Agent cache benchmark failed: {exc}")
        return 1
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ratio = report.get("measured_cache_ratio")
    print(
        f"Agent cache benchmark complete: mode={report['mode']}, "
        f"cache_ratio={ratio if ratio is not None else 'unsupported/not-run'}, "
        f"report={output.relative_to(ROOT)}"
    )
    return 0 if report.get("status", "complete") == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
