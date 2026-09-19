"""Skill registry loading, action mapping and closed-world rejection."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bot.services.skills import (
    SkillRegistryError,
    parse_registry,
    registry_loader,
)


def test_live_registry_exposes_ranking_action():
    registry = registry_loader.load()
    skill = registry.action_map["ranking"]
    assert skill.skill_id == "commands"
    assert skill.feature_key == "speech_ranking"
    assert registry.feature_key_for_action("today_live") is None
    assert registry.feature_key_for_action("missing_action") is None


def test_registry_has_no_duplicate_local_actions():
    registry = registry_loader.load()
    actions = [
        action for skill in registry.skills for action in skill.local_actions
    ]
    assert len(actions) == len(set(actions))


def test_parse_registry_rejects_duplicate_skill_id():
    payload = {
        "schema_version": 1,
        "skills": [
            {"skill_id": "a", "version": "1", "name": "A", "kind": "skill",
             "plugin": "a.py", "owner": "project", "deprecated": False},
            {"skill_id": "a", "version": "1", "name": "A2", "kind": "skill",
             "plugin": "b.py", "owner": "project", "deprecated": False},
        ],
    }
    with pytest.raises(SkillRegistryError):
        parse_registry(json.dumps(payload))


def test_parse_registry_rejects_duplicate_local_action():
    payload = {
        "schema_version": 1,
        "skills": [
            {"skill_id": "a", "version": "1", "name": "A", "kind": "skill",
             "plugin": "a.py", "owner": "project", "deprecated": False,
             "local_actions": ["ranking"]},
            {"skill_id": "b", "version": "1", "name": "B", "kind": "skill",
             "plugin": "b.py", "owner": "project", "deprecated": False,
             "local_actions": ["ranking"]},
        ],
    }
    with pytest.raises(SkillRegistryError):
        parse_registry(json.dumps(payload))


def test_parse_registry_rejects_bad_kind():
    payload = {
        "schema_version": 1,
        "skills": [
            {"skill_id": "a", "version": "1", "name": "A", "kind": "wizard",
             "plugin": "a.py", "owner": "project", "deprecated": False},
        ],
    }
    with pytest.raises(SkillRegistryError):
        parse_registry(json.dumps(payload))


def test_registry_loader_reloads_after_change(tmp_path: Path):
    path = tmp_path / "registry.json"
    payload = {
        "schema_version": 1,
        "skills": [
            {"skill_id": "a", "version": "1", "name": "A", "kind": "skill",
             "plugin": "a.py", "owner": "project", "deprecated": False},
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    from bot.services.skills import SkillRegistryLoader

    loader = SkillRegistryLoader(path)
    assert loader.load().spec("a") is not None
    payload["skills"].append(
        {"skill_id": "b", "version": "1", "name": "B", "kind": "admin",
         "plugin": "b.py", "owner": "project", "deprecated": False}
    )
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert loader.load().spec("b") is not None
