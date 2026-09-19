"""Read-only access to the skill registry manifest."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from bot.config import ROOT


REGISTRY_PATH = ROOT / "config" / "skill-registry.json"
SUPPORTED_KINDS = frozenset({"skill", "admin", "mixed", "infrastructure", "disabled"})


class SkillRegistryError(RuntimeError):
    """The skill registry is missing or malformed."""


@dataclass(frozen=True, slots=True)
class SkillSpec:
    skill_id: str
    version: str
    name: str
    kind: str
    plugin: str
    feature_key: str
    local_actions: tuple[str, ...]
    deprecated: bool


@dataclass(frozen=True, slots=True)
class SkillRegistry:
    skills: tuple[SkillSpec, ...]

    @property
    def action_map(self) -> dict[str, SkillSpec]:
        result: dict[str, SkillSpec] = {}
        for skill in self.skills:
            for action in skill.local_actions:
                if action in result:
                    raise SkillRegistryError(f"duplicate local action: {action}")
                result[action] = skill
        return result

    def spec(self, skill_id: str) -> SkillSpec | None:
        return next((skill for skill in self.skills if skill.skill_id == skill_id), None)

    def feature_key_for_action(self, action: str) -> str | None:
        skill = self.action_map.get(action)
        if not skill or not skill.feature_key:
            return None
        return skill.feature_key


class SkillRegistryLoader:
    """mtime-cached loader so the event path never parses JSON per message."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or REGISTRY_PATH
        self._signature: tuple[int, int] | None = None
        self._registry = SkillRegistry(())

    def load(self) -> SkillRegistry:
        try:
            stat = self.path.stat()
        except OSError as exc:
            raise SkillRegistryError(f"missing skill registry: {self.path}") from exc
        signature = (stat.st_mtime_ns, stat.st_size)
        if signature == self._signature:
            return self._registry
        self._registry = parse_registry(self.path.read_text(encoding="utf-8"))
        self._signature = signature
        return self._registry


def parse_registry(text: str) -> SkillRegistry:
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise SkillRegistryError("skill registry is not valid JSON") from exc
    if payload.get("schema_version") != 1 or not isinstance(payload.get("skills"), list):
        raise SkillRegistryError("skill registry schema is unsupported")
    skills: list[SkillSpec] = []
    seen: set[str] = set()
    for entry in payload["skills"]:
        if not isinstance(entry, dict):
            raise SkillRegistryError("skill entry must be an object")
        skill_id = str(entry.get("skill_id") or "")
        if not skill_id or skill_id in seen:
            raise SkillRegistryError(f"invalid or duplicate skill_id: {skill_id!r}")
        seen.add(skill_id)
        kind = str(entry.get("kind") or "")
        if kind not in SUPPORTED_KINDS:
            raise SkillRegistryError(f"{skill_id}: unsupported kind {kind!r}")
        actions = entry.get("local_actions") or []
        if not isinstance(actions, list) or any(not str(item) for item in actions):
            raise SkillRegistryError(f"{skill_id}: local_actions must be a string list")
        skills.append(
            SkillSpec(
                skill_id=skill_id,
                version=str(entry.get("version") or ""),
                name=str(entry.get("name") or ""),
                kind=kind,
                plugin=str(entry.get("plugin") or ""),
                feature_key=str(entry.get("feature_key") or ""),
                local_actions=tuple(str(item) for item in actions),
                deprecated=bool(entry.get("deprecated", False)),
            )
        )
    registry = SkillRegistry(tuple(skills))
    registry.action_map  # duplicate action check
    return registry


registry_loader = SkillRegistryLoader()


def local_action_skill(action: str) -> SkillSpec | None:
    return registry_loader.load().action_map.get(action)


def feature_key_for_action(action: str) -> str | None:
    skill = local_action_skill(action)
    return skill.feature_key if skill and skill.feature_key else None
