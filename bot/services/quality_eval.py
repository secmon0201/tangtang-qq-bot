"""Golden-set quality evaluation and SLO aggregation for skill releases."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from bot.config import ROOT


DEFAULT_GOLDEN_SET = ROOT / "config" / "quality-golden-set.json"

RUBRIC = (
    "评审这段机器人回复。只输出 JSON，不要解释。"
    '键：{"natural":1-5,"factual":1-5,"no_fabrication":1-5,"persona":1-5,'
    '"reason":"一句话中文理由"}。'
    "natural 表示像真人群聊；factual 表示与输入和本地结果一致；"
    "no_fabrication 表示没有编造未执行的功能或不存在的事实；"
    "persona 表示符合设定人格。"
)


@dataclass(frozen=True, slots=True)
class GoldenCase:
    case_id: str
    kind: str
    input: str
    expect: Mapping[str, Any] = field(default_factory=dict)
    router_expected: bool = False


@dataclass(frozen=True, slots=True)
class CaseResult:
    case_id: str
    passed: bool
    failures: tuple[str, ...]
    reply: str = ""
    score: float = 0.0


def load_golden_set(path: Path | None = None) -> tuple[GoldenCase, ...]:
    payload = json.loads((path or DEFAULT_GOLDEN_SET).read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("cases"), list):
        raise ValueError("unsupported golden set schema")
    cases: list[GoldenCase] = []
    seen: set[str] = set()
    for entry in payload["cases"]:
        case_id = str(entry["case_id"])
        if case_id in seen:
            raise ValueError(f"duplicate golden case: {case_id}")
        seen.add(case_id)
        cases.append(
            GoldenCase(
                case_id=case_id,
                kind=str(entry.get("kind") or "chat"),
                input=str(entry.get("input") or ""),
                expect=dict(entry.get("expect") or {}),
                router_expected=bool(entry.get("router_expected", False)),
            )
        )
    return tuple(cases)


def score_case(case: GoldenCase, actual: Mapping[str, Any]) -> CaseResult:
    """Structural checks; semantic review is a separate model pass."""

    failures: list[str] = []
    expect = case.expect
    reply = str(actual.get("reply") or "")
    executed = {str(item) for item in actual.get("executed") or []}

    if "must_reply" in expect:
        replied = bool(reply.strip()) or bool(executed)
        if bool(expect["must_reply"]) != replied:
            failures.append("must_reply mismatch")
    if expect.get("skill_id"):
        if str(actual.get("skill_id") or "") != str(expect["skill_id"]):
            failures.append(f"skill_id mismatch: {actual.get('skill_id')!r}")
    if expect.get("expect_skill") is False and actual.get("skill_id"):
        failures.append(f"unexpected skill routing: {actual.get('skill_id')!r}")
    if expect.get("action"):
        if str(actual.get("action") or "") != str(expect["action"]):
            failures.append(f"action mismatch: {actual.get('action')!r}")
    if expect.get("args"):
        if str(actual.get("args") or "") != str(expect["args"]):
            failures.append(f"args mismatch: {actual.get('args')!r}")
    if expect.get("forbid_invented_action") and actual.get("invented_action"):
        failures.append("invented action")
    for phrase in expect.get("must_not_claim_executed") or []:
        if phrase and phrase in reply:
            failures.append(f"forbidden claim: {phrase}")
    maximum = int(expect.get("max_chars") or 0)
    if maximum and len(reply) > maximum:
        failures.append(f"reply too long: {len(reply)}")
    return CaseResult(
        case_id=case.case_id,
        passed=not failures,
        failures=tuple(failures),
        reply=reply,
        score=1.0 if not failures else 0.0,
    )


def evaluate_results(
    cases: tuple[GoldenCase, ...],
    results: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    per_case: list[CaseResult] = []
    for case in cases:
        per_case.append(score_case(case, results.get(case.case_id, {})))
    passed = sum(1 for item in per_case if item.passed)
    return {
        "total": len(per_case),
        "passed": passed,
        "failed": len(per_case) - passed,
        "pass_rate": round(passed / len(per_case), 4) if per_case else 0.0,
        "cases": [
            {
                "case_id": item.case_id,
                "passed": item.passed,
                "failures": list(item.failures),
                "reply": item.reply,
                "score": item.score,
            }
            for item in per_case
        ],
    }


def judge_prompt(case: GoldenCase, reply: str) -> str:
    return (
        f"{RUBRIC}\n\n"
        f"[输入] {case.input}\n"
        f"[实际回复] {reply}\n"
    )


def parse_judge(text: str) -> dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("judge did not return JSON")
    payload = json.loads(text[start:end + 1])
    if not isinstance(payload, dict):
        raise ValueError("judge payload must be an object")
    for key in ("natural", "factual", "no_fabrication", "persona"):
        value = payload.get(key)
        if not isinstance(value, (int, float)) or not 1 <= float(value) <= 5:
            raise ValueError(f"judge score out of range: {key}")
    return payload


def quality_summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate latency and outcome metrics into an SLO snapshot."""

    total = len(cases)
    timeouts = sum(1 for item in cases if str(item.get("status")) == "timeout")
    failures = sum(1 for item in cases if not item.get("ok"))
    latencies = sorted(
        float(item["latency_ms"]) for item in cases if item.get("latency_ms") is not None
    )

    def percentile(values: list[float], ratio: float) -> float | None:
        if not values:
            return None
        index = min(len(values) - 1, max(0, int(round((len(values) - 1) * ratio))))
        return values[index]

    return {
        "total": total,
        "failures": failures,
        "timeouts": timeouts,
        "failure_rate": round(failures / total, 4) if total else 0.0,
        "timeout_rate": round(timeouts / total, 4) if total else 0.0,
        "p50_latency_ms": percentile(latencies, 0.50),
        "p95_latency_ms": percentile(latencies, 0.95),
    }
