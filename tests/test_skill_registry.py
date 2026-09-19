"""Protect the skill registry contract and its coverage audit."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from scripts.validate_skill_registry import (
    REGISTRY_PATH,
    counts,
    load_registry,
    validate,
)


def test_skill_registry_covers_the_full_command_surface():
    registry = load_registry(REGISTRY_PATH)
    assert validate(registry) == []
    assert counts(registry) == {
        "plugins": 27,
        "commands": 62,
        "aliases": 26,
        "rules": 14,
    }


def test_missing_command_is_rejected(tmp_path):
    registry = copy.deepcopy(load_registry(REGISTRY_PATH))
    registry["skills"][0]["commands"] = registry["skills"][0]["commands"][1:]
    errors = validate(registry)
    assert any("commands missing from registry" in error for error in errors)


def test_duplicate_command_is_rejected():
    registry = copy.deepcopy(load_registry(REGISTRY_PATH))
    first = registry["skills"][0]["commands"][0]["command"]
    registry["skills"][1]["commands"].append({"command": first, "aliases": []})
    errors = validate(registry)
    assert any("registered in both" in error for error in errors)


def test_unknown_plugin_is_rejected():
    registry = copy.deepcopy(load_registry(REGISTRY_PATH))
    registry["skills"].append(
        {
            "skill_id": "missing_plugin",
            "version": "1.0.0",
            "name": "不存在",
            "kind": "skill",
            "plugin": "bot/plugins/not_a_real_plugin.py",
            "commands": [],
            "rules": [],
            "plugin_rules": [],
            "owner": "project",
            "deprecated": False,
        }
    )
    errors = validate(registry)
    assert any("unknown plugin file" in error for error in errors)


def test_missing_plugin_entry_is_rejected():
    registry = copy.deepcopy(load_registry(REGISTRY_PATH))
    registry["skills"] = registry["skills"][1:]
    errors = validate(registry)
    assert any("plugins missing from skill registry" in error for error in errors)


def test_registry_file_is_valid_json():
    payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert isinstance(payload["skills"], list)
