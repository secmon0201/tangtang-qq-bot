"""Skill release controls: enable, disable and group scoping."""
from __future__ import annotations

from pathlib import Path

from bot.services.skill_audit import SkillAuditLedger


def test_default_control_is_enabled_for_every_group(tmp_path: Path):
    ledger = SkillAuditLedger(tmp_path / "audit.db")
    assert ledger.enabled_for_group("commands", 1001)
    assert ledger.control("commands")["groups"] == ()


def test_disabled_skill_is_off_for_every_group(tmp_path: Path):
    ledger = SkillAuditLedger(tmp_path / "audit.db")
    ledger.set_control("commands", enabled=False, note="incident")
    assert not ledger.enabled_for_group("commands", 1001)
    assert not ledger.enabled_for_group("commands", 2002)


def test_group_scoped_rollout(tmp_path: Path):
    ledger = SkillAuditLedger(tmp_path / "audit.db")
    ledger.set_control("commands", enabled=True, groups=(1001, 1002), note="canary")
    assert ledger.enabled_for_group("commands", 1001)
    assert ledger.enabled_for_group("commands", 1002)
    assert not ledger.enabled_for_group("commands", 1003)


def test_control_update_replaces_previous_scope(tmp_path: Path):
    ledger = SkillAuditLedger(tmp_path / "audit.db")
    ledger.set_control("commands", enabled=True, groups=(1001,))
    ledger.set_control("commands", enabled=True, groups=(2002,))
    assert not ledger.enabled_for_group("commands", 1001)
    assert ledger.enabled_for_group("commands", 2002)
