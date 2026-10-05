"""The full-domain command surface audit stays green and repeatable."""
from __future__ import annotations

from scripts.audit_command_surface import audit


def test_audit_reports_no_collisions_or_missing_entries():
    report = audit()
    assert report["ok"] is True
    assert report["collisions"] == {}
    assert report["missing_catalog"] == []
    assert report["missing_files"] == []
    assert report["plugins"] >= 26
    assert report["commands"] >= 80


def test_audit_detects_catalog_gaps(tmp_path):
    # Gutted catalog against the live plugin sources must fail.
    from scripts import audit_command_surface as module

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "全部#指令清单.md").write_text("空目录", encoding="utf-8")
    report = module.audit(tmp_path)
    assert report["ok"] is False
    assert report["missing_catalog"]


def test_audit_cli_exit_code(monkeypatch):
    import sys

    from scripts import audit_command_surface as module

    monkeypatch.setattr(sys, "argv", ["audit_command_surface.py"])
    assert module.main() == 0
