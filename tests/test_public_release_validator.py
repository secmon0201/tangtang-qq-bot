from __future__ import annotations

from pathlib import Path

from scripts.validate_public_release import validate_public_files, validate_template


def test_public_template_rejects_private_values():
    assert validate_template({"BOT_OPERATOR_IDS": "123456789"}) == [
        "private template field must be empty: BOT_OPERATOR_IDS"
    ]


def test_public_scan_rejects_realistic_ids_paths_and_local_values(tmp_path: Path):
    source = tmp_path / "example.py"
    source.write_text(
        "user_id = " + "123" + "456789\npath = '" + "C:/" + "Users/private/QQ/files'\nhost = 'private.example'\n",
        encoding="utf-8",
    )

    errors = validate_public_files(
        tmp_path,
        ["example.py"],
        local_values={"PUBLIC_SHORT_HOST": "private.example"},
    )

    assert "non-synthetic QQ-like identifier: example.py" in errors
    assert "machine-specific local path: example.py" in errors
    assert "local private value from PUBLIC_SHORT_HOST: example.py" in errors


def test_public_scan_accepts_synthetic_test_identifiers(tmp_path: Path):
    source = tmp_path / "example.py"
    source.write_text("group_id = 910000101\noperator_id = 900000001\n", encoding="utf-8")

    assert validate_public_files(tmp_path, ["example.py"]) == []
