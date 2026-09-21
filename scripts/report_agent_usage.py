"""Aggregate privacy-safe Agent usage and cache telemetry from JSONL logs."""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports"
DEFAULT_USAGE_DIR = ROOT / "data" / "tangtang" / "usage"
REPORT_VERSION = "agent-usage-report-v1"
TERMINAL_EVENTS = frozenset({
    "reply", "proactive_reply", "silent", "feature", "error",
    "invalid_reply_structure", "voice_uncertain", "voice_cancelled", "voice_duplicate",
})


def parse_timestamp(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def load_records(paths: Iterable[Path]) -> tuple[list[dict[str, Any]], int]:
    records: list[dict[str, Any]] = []
    invalid = 0
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            invalid += 1
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except (TypeError, ValueError):
                invalid += 1
                continue
            if not isinstance(value, dict) or parse_timestamp(value.get("ts")) is None:
                invalid += 1
                continue
            records.append(value)
    return records, invalid


def _integer(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return max(0, parsed)


def _bounded_hash(value: Any) -> str:
    text = str(value or "")
    return (
        text
        if 16 <= len(text) <= 128
        and all(char.isalnum() or char in "-_" for char in text)
        else ""
    )


def _bounded_label(value: Any) -> str:
    text = str(value or "")
    return text if 1 <= len(text) <= 128 and all(char.isalnum() or char in "-_.:/" for char in text) else "unknown"


def select_requests(
    records: Iterable[Mapping[str, Any]],
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    traced: dict[str, dict[str, Any]] = {}
    for record in records:
        timestamp = parse_timestamp(record.get("ts"))
        if timestamp is None or (start is not None and timestamp < start) or (end is not None and timestamp >= end):
            continue
        if str(record.get("event") or "") not in TERMINAL_EVENTS:
            continue
        prompt_tokens = _integer(record.get("prompt_tokens"))
        if prompt_tokens is None or prompt_tokens <= 0:
            continue
        safe = dict(record)
        trace = _bounded_hash(record.get("request_trace"))
        if trace:
            traced[trace] = safe
        else:
            selected.append(safe)
    selected.extend(traced.values())
    selected.sort(key=lambda item: str(item.get("ts") or ""))
    return selected


def select_payload_builds(
    records: Iterable[Mapping[str, Any]],
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for record in records:
        timestamp = parse_timestamp(record.get("ts"))
        if timestamp is None or (start is not None and timestamp < start) or (end is not None and timestamp >= end):
            continue
        if str(record.get("event") or "") == "model_started":
            selected.append(dict(record))
    return selected


def _median(values: list[float | int]) -> float | None:
    return round(float(statistics.median(values)), 4) if values else None


def summarize(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(records)
    prompt_tokens = [_integer(row.get("prompt_tokens")) or 0 for row in rows]
    cache_claimed = [row for row in rows if row.get("cache_status") == "reported"]
    cache_reported: list[Mapping[str, Any]] = []
    ratios: list[float] = []
    cache_read_total = 0
    cache_write_total = 0
    cache_miss_total = 0
    cache_numeric_records = 0
    for row in cache_claimed:
        prompt = _integer(row.get("prompt_tokens"))
        cache_read = _integer(row.get("cache_read_tokens"))
        cache_write = _integer(row.get("cache_write_tokens"))
        cache_miss = _integer(row.get("cache_miss_tokens"))
        if prompt is None or cache_read is None or cache_write is None or cache_miss is None:
            continue
        cache_reported.append(row)
        cache_numeric_records += 1
        cache_read_total += cache_read
        cache_write_total += cache_write
        cache_miss_total += cache_miss
        if prompt > 0:
            ratios.append(cache_read / prompt)

    reported_prompt = sum(
        _integer(row.get("prompt_tokens")) or 0
        for row in cache_reported
        if _integer(row.get("cache_read_tokens")) is not None
    )
    model_counts = Counter(_bounded_label(row.get("model")) for row in rows)
    layout_counts = Counter(_bounded_label(row.get("layout_version")) for row in rows)
    tool_modes = Counter(_bounded_label(row.get("native_tool_mode")) for row in rows)
    static_hashes = sorted({value for row in rows if (value := _bounded_hash(row.get("static_prefix_hash")))})
    schema_hashes = sorted({value for row in rows if (value := _bounded_hash(row.get("tool_schema_hash")))})
    unsupported_count = len(rows) - len(cache_reported)
    return {
        "request_count": len(rows),
        "prompt_tokens_total": sum(prompt_tokens),
        "prompt_tokens_median": _median(prompt_tokens),
        "completion_tokens_total": sum(_integer(row.get("completion_tokens")) or 0 for row in rows),
        "latency_ms_median": _median([
            value for row in rows if (value := _integer(row.get("latency_ms"))) is not None
        ]),
        "cache": {
                "status": (
                    "empty" if not rows else
                    "reported" if unsupported_count == 0 else
                    "unsupported" if not cache_reported else
                    "mixed"
                ),
            "reported_records": len(cache_reported),
            "numeric_records": cache_numeric_records,
            "unsupported_records": unsupported_count,
            "cache_read_tokens_total": cache_read_total if cache_numeric_records else None,
            "cache_write_tokens_total": cache_write_total if cache_numeric_records else None,
            "non_cached_input_tokens_total": cache_miss_total if cache_numeric_records else None,
            "weighted_cache_ratio": (
                round(cache_read_total / reported_prompt, 4) if reported_prompt > 0 else None
            ),
            "median_cache_ratio": _median(ratios),
        },
        "model_calls_total": sum(_integer(row.get("model_calls")) or 0 for row in rows),
        "tool_rounds_total": sum(_integer(row.get("tool_rounds")) or 0 for row in rows),
        "models": dict(sorted(model_counts.items())),
        "layouts": dict(sorted(layout_counts.items())),
        "native_tool_modes": dict(sorted(tool_modes.items())),
        "static_prefix_hashes": static_hashes,
        "tool_schema_hashes": schema_hashes,
    }


def summarize_payload_builds(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(records)
    layouts = Counter(_bounded_label(row.get("layout_version")) for row in rows)
    tool_modes = Counter(_bounded_label(row.get("native_tool_mode")) for row in rows)
    changed = Counter(
        "changed" if row.get("shadow_payload_changed") is True else
        "unchanged" if row.get("shadow_payload_changed") is False else
        "not_shadow"
        for row in rows
    )
    return {
        "count": len(rows),
        "layouts": dict(sorted(layouts.items())),
        "native_tool_modes": dict(sorted(tool_modes.items())),
        "shadow_comparisons": dict(sorted(changed.items())),
        "static_prefix_hashes": sorted({
            value for row in rows if (value := _bounded_hash(row.get("static_prefix_hash")))
        }),
        "tool_schema_hashes": sorted({
            value for row in rows if (value := _bounded_hash(row.get("tool_schema_hash")))
        }),
        "native_tool_schema_hashes": sorted({
            value for row in rows
            if (value := _bounded_hash(row.get("native_tool_schema_hash")))
        }),
        "static_prefix_chars_median": _median([
            value for row in rows
            if (value := _integer(row.get("static_prefix_chars"))) is not None
        ]),
        "dynamic_status_chars_median": _median([
            value for row in rows
            if (value := _integer(row.get("dynamic_status_chars"))) is not None
        ]),
        "conversation_chars_median": _median([
            value for row in rows
            if (value := _integer(row.get("conversation_chars"))) is not None
        ]),
    }


def build_report(
    records: Iterable[Mapping[str, Any]],
    *,
    invalid_records: int = 0,
    baseline_start: datetime | None = None,
    baseline_end: datetime | None = None,
    candidate_start: datetime | None = None,
    candidate_end: datetime | None = None,
) -> dict[str, Any]:
    all_records = list(records)
    windows: dict[str, Any] = {}
    if any(value is not None for value in (baseline_start, baseline_end, candidate_start, candidate_end)):
        windows["baseline"] = summarize(select_requests(
            all_records, start=baseline_start, end=baseline_end
        ))
        windows["baseline"]["payload_builds"] = summarize_payload_builds(select_payload_builds(
            all_records, start=baseline_start, end=baseline_end
        ))
        windows["candidate"] = summarize(select_requests(
            all_records, start=candidate_start, end=candidate_end
        ))
        windows["candidate"]["payload_builds"] = summarize_payload_builds(select_payload_builds(
            all_records, start=candidate_start, end=candidate_end
        ))
    else:
        windows["all"] = summarize(select_requests(all_records))
        windows["all"]["payload_builds"] = summarize_payload_builds(
            select_payload_builds(all_records)
        )
    return {
        "report_version": REPORT_VERSION,
        "privacy": "aggregates_hashes_and_bounded_labels_only",
        "invalid_records": max(0, int(invalid_records)),
        "windows": windows,
    }


def _bound(raw: str) -> datetime | None:
    if not raw:
        return None
    parsed = parse_timestamp(raw)
    if parsed is None:
        raise ValueError(f"invalid ISO timestamp: {raw}")
    return parsed


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
    parser.add_argument("--usage-dir", type=Path, default=DEFAULT_USAGE_DIR)
    parser.add_argument("--baseline-start", default="")
    parser.add_argument("--baseline-end", default="")
    parser.add_argument("--candidate-start", default="")
    parser.add_argument("--candidate-end", default="")
    parser.add_argument("--output", type=Path, default=Path("reports/agent-usage.json"))
    args = parser.parse_args()
    try:
        baseline_start = _bound(args.baseline_start)
        baseline_end = _bound(args.baseline_end)
        candidate_start = _bound(args.candidate_start)
        candidate_end = _bound(args.candidate_end)
        if baseline_start and baseline_end and baseline_start >= baseline_end:
            raise ValueError("baseline start must be earlier than end")
        if candidate_start and candidate_end and candidate_start >= candidate_end:
            raise ValueError("candidate start must be earlier than end")
        output = _report_path(args.output)
    except ValueError as exc:
        print(f"Agent usage report failed: {exc}")
        return 2

    usage_dir = args.usage_dir.resolve()
    records, invalid = load_records(sorted(usage_dir.glob("*.jsonl")))
    report = build_report(
        records,
        invalid_records=invalid,
        baseline_start=baseline_start,
        baseline_end=baseline_end,
        candidate_start=candidate_start,
        candidate_end=candidate_end,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Agent usage report written: records={len(records)}, invalid={invalid}, report={output.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
