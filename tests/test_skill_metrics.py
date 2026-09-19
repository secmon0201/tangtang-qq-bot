"""Per-skill usage and cost accounting."""
from __future__ import annotations

from pathlib import Path

from bot.services.skill_metrics import SkillMetricsStore


def test_record_and_summary_roundtrip(tmp_path: Path):
    store = SkillMetricsStore(tmp_path / "metrics.db", now=lambda: 100.0)
    store.record(
        skill_id="commands",
        group_id=1001,
        user_id=2001,
        action="ranking",
        ok=True,
        latency_ms=120,
        prompt_tokens=10,
        completion_tokens=5,
        reasoning_tokens=2,
        cost=0.001,
        source="feature_router",
    )
    store.record(
        skill_id="asoul",
        group_id=1001,
        user_id=2002,
        action="week_live",
        ok=False,
        latency_ms=80,
    )
    summary = store.summary()
    assert summary["calls"] == 2
    assert summary["failures"] == 1
    assert summary["failure_rate"] == 0.5
    assert summary["tokens"] == 17
    assert summary["cost"] == 0.001
    assert {row["skill_id"] for row in summary["by_skill"]} == {"commands", "asoul"}
    assert summary["by_group"][0]["group_id"] == 1001


def test_summary_respects_since_filter(tmp_path: Path):
    clock = {"value": 100.0}
    store = SkillMetricsStore(tmp_path / "metrics.db", now=lambda: clock["value"])
    store.record(skill_id="commands")
    clock["value"] = 200.0
    store.record(skill_id="asoul")
    assert store.summary(since=150.0)["calls"] == 1
    assert store.summary()["calls"] == 2
