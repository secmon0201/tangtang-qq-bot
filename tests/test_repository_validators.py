from __future__ import annotations

from pathlib import Path

from scripts.validate_architecture import (
    dependency_cycles,
    duplicate_function_bodies,
    validate_architecture,
)
from scripts.validate_docs import validate_docs
from scripts.validate_repository import validate_candidates, validate_history_paths


def write_module(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_current_architecture_passes_dependency_rules():
    root = Path(__file__).resolve().parents[1]
    assert validate_architecture(root) == []


def test_current_documentation_has_no_broken_or_retired_entries():
    root = Path(__file__).resolve().parents[1]
    assert validate_docs(root) == []


def test_architecture_validator_reports_plugin_imports_and_cycles(tmp_path):
    write_module(tmp_path, "bot/__init__.py", "")
    write_module(tmp_path, "bot/plugins/__init__.py", "")
    write_module(tmp_path, "bot/plugins/first.py", "from bot.plugins import second\n")
    write_module(tmp_path, "bot/plugins/second.py", "from bot.plugins import first\n")

    errors = validate_architecture(tmp_path)

    assert any("plugin-to-plugin import" in error for error in errors)
    assert any("dependency cycle" in error for error in errors)


def test_cycle_detector_accepts_acyclic_graph_and_reports_cycle():
    assert dependency_cycles({"a": {"b"}, "b": set()}) == []
    assert dependency_cycles({"a": {"b"}, "b": {"a"}}) == [("a", "b")]


def test_duplicate_implementation_detector_ignores_small_helpers_and_flags_large_copies(
    tmp_path,
):
    write_module(tmp_path, "bot/__init__.py", "")
    copied = """def calculate(value):
    first = value + 1
    second = first + 1
    third = second + 1
    fourth = third + 1
    fifth = fourth + 1
    sixth = fifth + 1
    seventh = sixth + 1
    eighth = seventh + 1
    ninth = eighth + 1
    tenth = ninth + 1
    return tenth
"""
    write_module(tmp_path, "bot/services/first.py", copied)
    write_module(tmp_path, "bot/services/second.py", copied)
    write_module(tmp_path, "bot/services/small.py", "def identity(value):\n    return value\n")

    duplicates = duplicate_function_bodies(tmp_path / "bot")

    assert duplicates == [
        (
            ("bot.services.first", "calculate", 1),
            ("bot.services.second", "calculate", 1),
        )
    ]


def test_repository_validator_rejects_runtime_data_and_credentials(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "bot.db").write_bytes(b"db")
    (tmp_path / "token.txt").write_text("ghp_" + "a" * 36, encoding="utf-8")

    errors = validate_candidates(tmp_path, ["data/bot.db", "token.txt"])

    assert "forbidden path: data/bot.db" in errors
    assert "credential-like content: token.txt" in errors


def test_repository_validator_rejects_forbidden_paths_from_reachable_history():
    errors = validate_history_paths(
        [
            "bot/services/current.py",
            "data/removed.db",
            "deploy/linux/bootstrap.sh",
            "logs/old.log",
            ".env",
            "linux_vm_start.bat",
        ]
    )

    assert errors == [
        "historical forbidden path: data/removed.db",
        "historical forbidden path: deploy/linux/bootstrap.sh",
        "historical forbidden path: logs/old.log",
        "historical forbidden path: .env",
        "historical forbidden path: linux_vm_start.bat",
    ]
