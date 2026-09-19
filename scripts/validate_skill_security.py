"""Static security audit for the skill registry and its runtime gates."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "config" / "skill-registry.json"
ADMIN_PLUGIN = ROOT / "bot" / "plugins" / "skill_admin.py"
ROUTER_PLUGIN = ROOT / "bot" / "plugins" / "tangtang_chat.py"
SKILL_ID = re.compile(r"^[a-z0-9_]+$")
SECRET_LIKE = re.compile(
    r"(sk-[A-Za-z0-9]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|-----BEGIN|"
    r"password\s*[:=]|token\s*[:=])",
    re.IGNORECASE,
)


def validate(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    registry_path = root / "config" / "skill-registry.json"
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    skills = payload.get("skills")
    if payload.get("schema_version") != 1 or not isinstance(skills, list):
        return ["unsupported skill registry schema"]

    admin_commands: set[str] = set()
    for entry in skills:
        skill_id = str(entry.get("skill_id") or "")
        if not SKILL_ID.match(skill_id):
            errors.append(f"invalid skill id: {skill_id!r}")
        if str(entry.get("owner") or "") != "project":
            errors.append(f"{skill_id}: owner must be project")
        if not str(entry.get("plugin") or "").startswith("bot/plugins/"):
            errors.append(f"{skill_id}: plugin must live under bot/plugins")
        for command in entry.get("commands") or []:
            name = str(command.get("command") or "")
            if SECRET_LIKE.search(name):
                errors.append(f"{skill_id}: command looks like a credential")
            if skill_id == "skill_admin":
                admin_commands.add(name)
            for alias in command.get("aliases") or []:
                if SECRET_LIKE.search(str(alias)):
                    errors.append(f"{skill_id}: alias looks like a credential")

    conflicts = sorted(
        name
        for entry in skills
        if entry.get("skill_id") != "skill_admin"
        for command in entry.get("commands") or []
        for name in [str(command.get("command") or "")]
        if name in admin_commands
    )
    if conflicts:
        errors.append(f"admin command conflict: {conflicts}")

    admin_source = (root / "bot" / "plugins" / "skill_admin.py").read_text(
        encoding="utf-8"
    )
    if "is_super_admin" not in admin_source:
        errors.append("skill_admin must gate on super-admin")
    router_source = (root / "bot" / "plugins" / "tangtang_chat.py").read_text(
        encoding="utf-8"
    )
    if "enabled_for_group" not in router_source:
        errors.append("router must check the per-skill control gate before execution")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    errors = validate(args.root)
    if errors:
        print("Skill security validation failed:")
        for error in errors:
            print("-", error)
        return 1
    print("Skill security validation passed: registry ownership, admin gating and controls")
    return 0


if __name__ == "__main__":
    sys.exit(main())
