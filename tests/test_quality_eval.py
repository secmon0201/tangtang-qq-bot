"""Golden-set scoring, judge parsing and SLO aggregation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bot.services.quality_eval import (
    GoldenCase,
    evaluate_results,
    load_golden_set,
    parse_judge,
    judge_prompt,
    quality_summary,
    score_case,
)


def test_golden_set_loads_and_has_unique_cases():
    cases = load_golden_set()
    assert len(cases) >= 8
    ids = [case.case_id for case in cases]
    assert len(ids) == len(set(ids))
    assert any(case.router_expected for case in cases)


def test_duplicate_case_id_is_rejected(tmp_path: Path):
    path = tmp_path / "golden.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "cases": [
                    {"case_id": "a", "input": "x"},
                    {"case_id": "a", "input": "y"},
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_golden_set(path)


def test_score_case_checks_reply_and_forbidden_claims():
    case = GoldenCase(
        case_id="x",
        kind="skill",
        input="帮我爬取相册",
        expect={
            "must_reply": True,
            "must_not_claim_executed": ["已经开发"],
            "max_chars": 20,
        },
    )
    passed = score_case(case, {"reply": "没有这个功能。"})
    assert passed.passed
    failed = score_case(case, {"reply": "已经开发好了，这就爬取。"})
    assert not failed.passed
    assert any("forbidden claim" in item for item in failed.failures)


def test_score_case_checks_skill_routing_and_args():
    case = GoldenCase(
        case_id="ranking",
        kind="skill",
        input="看今天的发言排行",
        expect={"skill_id": "commands", "action": "ranking", "args": "日"},
    )
    good = score_case(
        case,
        {"skill_id": "commands", "action": "ranking", "args": "日"},
    )
    assert good.passed
    bad = score_case(case, {"skill_id": "commands", "action": "ranking", "args": "月"})
    assert not bad.passed
    assert any("args mismatch" in item for item in bad.failures)


def test_score_case_rejects_unexpected_routing():
    case = GoldenCase(
        case_id="chat",
        kind="call",
        input="你今天心情怎么样",
        expect={"expect_skill": False},
    )
    assert score_case(case, {"skill_id": "", "reply": "挺好的呀"}).passed
    assert not score_case(case, {"skill_id": "commands", "reply": "挺好的呀"}).passed


def test_evaluate_results_aggregates_pass_rate():
    cases = (
        GoldenCase("a", "skill", "x", {"must_reply": True}),
        GoldenCase("b", "skill", "y", {"must_reply": True}),
    )
    report = evaluate_results(cases, {"a": {"reply": "ok"}, "b": {}})
    assert report["total"] == 2
    assert report["passed"] == 1
    assert report["pass_rate"] == 0.5


def test_judge_parsing_requires_score_range():
    payload = {
        "natural": 5,
        "factual": 4,
        "no_fabrication": 5,
        "persona": 4,
        "reason": "自然",
    }
    assert parse_judge(json.dumps(payload, ensure_ascii=False))["natural"] == 5
    payload["natural"] = 9
    with pytest.raises(ValueError):
        parse_judge(json.dumps(payload, ensure_ascii=False))
    assert "只输出 JSON" in judge_prompt(
        GoldenCase("a", "chat", "x"), "reply"
    )


def test_quality_summary_computes_rates_and_percentiles():
    metrics = [
        {"ok": True, "status": "ok", "latency_ms": 100},
        {"ok": True, "status": "ok", "latency_ms": 200},
        {"ok": False, "status": "timeout", "latency_ms": None},
        {"ok": True, "status": "ok", "latency_ms": 300},
    ]
    summary = quality_summary(metrics)
    assert summary["failure_rate"] == 0.25
    assert summary["timeout_rate"] == 0.25
    assert summary["p50_latency_ms"] in {200.0, 300.0}
    assert summary["p95_latency_ms"] == 300.0
