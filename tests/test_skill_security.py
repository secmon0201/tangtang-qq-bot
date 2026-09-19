"""Static security audit for the skill platform."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from scripts.validate_skill_security import validate


REGISTRY = Path("config/skill-registry.json")


def _write_registry(root: Path, payload: dict) -> None:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "skill-registry.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _write_plugins(root: Path, admin: str = "is_super_admin", router: str = "enabled_for_group") -> None:
    plugin_dir = root / "bot" / "plugins"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "skill_admin.py").write_text(admin, encoding="utf-8")
    (plugin_dir / "tangtang_chat.py").write_text(router, encoding="utf-8")


def test_live_registry_passes_security_audit():
    assert validate(Path.cwd()) == []


def test_missing_owner_is_rejected(tmp_path: Path):
    payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
    broken = copy.deepcopy(payload)
    broken["skills"][0]["owner"] = "someone"
    _write_registry(tmp_path, broken)
    _write_plugins(tmp_path)
    errors = validate(tmp_path)
    assert any("owner must be project" in error for error in errors)


def test_credential_like_command_is_rejected(tmp_path: Path):
    payload = {
        "schema_version": 1,
        "skills": [
            {
                "skill_id": "demo",
                "owner": "project",
                "plugin": "bot/plugins/demo.py",
                "commands": [{"command": "sk-abcdefghijklmnopqrstuvwxyz", "aliases": []}],
            }
        ],
    }
    _write_registry(tmp_path, payload)
    _write_plugins(tmp_path)
    errors = validate(tmp_path)
    assert any("credential" in error for error in errors)


def test_admin_gate_and_router_gate_are_required(tmp_path: Path):
    payload = json.loads(REGISTRY.read_text(encoding="utf-8"))
    _write_registry(tmp_path, payload)
    _write_plugins(tmp_path, admin="no gate", router="no gate")
    errors = validate(tmp_path)
    assert any("super-admin" in error for error in errors)
    assert any("control gate" in error for error in errors)
