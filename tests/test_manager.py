from pathlib import Path

from manager import (
    EnvFile,
    account_hints_from_command_line,
    extract_webui_token,
    napcat_launch_command,
    process_matches_account,
    redact_sensitive_log,
    webui_settings,
    parse_ids,
)


def test_parse_ids_deduplicates_and_enforces_group_limit():
    assert parse_ids("1, 1, 2") == (1, 2)
    try:
        parse_ids(",".join(str(value) for value in range(1, 12)), max_count=10)
    except ValueError as exc:
        assert "10" in str(exc)
    else:
        raise AssertionError("expected group limit error")


def test_env_file_preserves_comments_and_updates_values(tmp_path: Path):
    path = tmp_path / ".env"
    path.write_text("# keep\nPORT=8080\n", encoding="utf-8")
    env = EnvFile(path)
    env.set_many({"PORT": "9000", "NAPCAT_DIR": r"C:\Nap Cat"})
    content = path.read_text(encoding="utf-8")
    assert "# keep" in content
    assert "PORT=9000" in content
    assert 'NAPCAT_DIR="C:\\Nap Cat"' in content


def test_webui_token_is_detected_but_redacted_from_log():
    line = "NapCat WebUI started at http://127.0.0.1:6099/webui?token=demo-token"
    assert extract_webui_token(line) == "demo-token"
    safe = redact_sensitive_log(line)
    assert "demo-token" not in safe
    assert "token=<hidden>" in safe


def test_webui_settings_reads_local_config_without_logging_token(tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "webui.json").write_text(
        '{"port": 6100, "token": "config-token", "disableWebUI": false}',
        encoding="utf-8",
    )
    settings = webui_settings(tmp_path)
    assert settings["port"] == 6100
    assert settings["token"] == "config-token"
    assert settings["enabled"] is True


def test_existing_qq_is_only_verified_by_explicit_account_hint():
    assert account_hints_from_command_line("QQ.exe -q 2120682836") == ("2120682836",)
    assert account_hints_from_command_line("QQ.exe --type=renderer") == ()
    assert process_matches_account({"account_ids": ("2120682836",)}, "2120682836")
    assert not process_matches_account({"account_ids": ()}, "2120682836")


def test_napcat_quick_login_arguments_stay_inside_cmd_command():
    command = napcat_launch_command(Path(r"C:\Nap Cat\launcher.bat"), "2120682836")
    assert command == 'cmd.exe /d /c ""C:\\Nap Cat\\launcher.bat" -q 2120682836"'
