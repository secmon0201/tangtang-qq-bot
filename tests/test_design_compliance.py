"""The documented design stays machine-verifiable."""
from __future__ import annotations

from scripts.validate_design_compliance import build_checks, main


def test_design_compliance_passes_on_live_repo():
    checks = build_checks()
    assert len(checks) >= 18
    failed = [check.check_id for check in checks if not check.passed]
    assert failed == []


def test_design_compliance_cli(monkeypatch):
    import sys

    monkeypatch.setattr(sys, "argv", ["validate_design_compliance.py"])
    assert main() == 0


def test_design_compliance_covers_every_plan_section():
    prefixes = {check.check_id[0] for check in build_checks()}
    assert prefixes == {"A", "B", "C", "D", "E"}
