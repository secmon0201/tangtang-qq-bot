"""Skill audit ledger and failure classification."""
from __future__ import annotations

from pathlib import Path

import pytest

from bot.services.skill_audit import SkillAuditLedger, classify_failure


def test_classify_upstream_drift():
    assert classify_failure("missing git metadata")[0] == "upstream_drift"
    assert classify_failure("commit mismatch: actual=a expected=b")[0] == "upstream_drift"


def test_classify_parameter_and_capability():
    assert classify_failure("parameter out of range")[0] == "parameter_error"
    assert classify_failure("unsupported skill for this action")[0] == "capability_missing"
    assert classify_failure("skill capability missing")[0] == "capability_missing"


def test_classify_contract_and_model():
    assert classify_failure("invalid JSON contract")[0] == "contract_violation"
    assert classify_failure("model router returned nothing")[0] == "model_expression"


def test_record_and_resolve_roundtrip(tmp_path: Path):
    ledger = SkillAuditLedger(tmp_path / "audit.db", now=lambda: 100.0)
    entry = ledger.record(
        skill_id="commands",
        category="parameter_error",
        severity="warning",
        group_id=1001,
        user_id=2001,
        detail="bad scope",
    )
    assert entry.status == "open"
    assert ledger.get(entry.id).skill_id == "commands"
    assert ledger.resolve(entry.id, "corrected scope parser")
    resolved = ledger.get(entry.id)
    assert resolved.status == "resolved"
    assert resolved.resolution == "corrected scope parser"


def test_failure_recording_uses_classification(tmp_path: Path):
    ledger = SkillAuditLedger(tmp_path / "audit.db", now=lambda: 100.0)
    entry = ledger.record_failure(skill_id="asoul", error="missing git metadata")
    assert entry.category == "upstream_drift"
    assert entry.severity == "error"


def test_summary_and_export(tmp_path: Path):
    ledger = SkillAuditLedger(tmp_path / "audit.db", now=lambda: 100.0)
    first = ledger.record(
        skill_id="commands", category="parameter_error", severity="warning"
    )
    ledger.record(skill_id="asoul", category="upstream_drift", severity="error")
    ledger.resolve(first.id)

    summary = ledger.summary()
    assert summary["by_status"] == {"open": 1, "resolved": 1}
    assert summary["open_by_category"] == {"upstream_drift": 1}
    target = tmp_path / "export.json"
    assert ledger.export(target) == 2
    assert target.is_file()


def test_invalid_category_is_rejected(tmp_path: Path):
    ledger = SkillAuditLedger(tmp_path / "audit.db")
    with pytest.raises(ValueError):
        ledger.record(skill_id="x", category="not_real", severity="warning")
