"""Secret redaction and skill role gates."""
from __future__ import annotations

from bot.services.skill_audit import SkillAuditLedger
from bot.services.skill_security import blocked_skills, redact, role_allows


def test_redact_strips_bearer_and_api_keys():
    text = "Authorization: Bearer abc123 token=xyz sk-abcdefghijklmnop"
    cleaned = redact(text)
    assert "abc123" not in cleaned
    assert "xyz" not in cleaned
    assert "sk-abcdefghijklmnop" not in cleaned
    assert "<redacted>" in cleaned


def test_redact_is_applied_to_the_audit_ledger(tmp_path):
    ledger = SkillAuditLedger(tmp_path / "audit.db")
    entry = ledger.record_failure(
        skill_id="commands",
        error="request failed: Bearer supersecretvalue",
    )
    assert "supersecretvalue" not in entry.detail
    assert "<redacted>" in entry.detail


def test_role_allows_matrix():
    assert role_allows("member")
    assert not role_allows("admin")
    assert role_allows("admin", is_admin=True)
    assert role_allows("admin", is_super_admin=True)
    assert not role_allows("super_admin", is_admin=True)
    assert role_allows("super_admin", is_super_admin=True)
    assert not role_allows("unknown")


def test_blocked_skills_reports_only_denied():
    skills = (("a", "member"), ("b", "admin"), ("c", "super_admin"))
    assert blocked_skills(skills) == ["b", "c"]
    assert blocked_skills(skills, is_admin=True) == ["c"]
    assert blocked_skills(skills, is_super_admin=True) == []
