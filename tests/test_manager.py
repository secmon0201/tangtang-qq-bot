from pathlib import Path

import pytest

from manager import (
    EnvFile,
    parse_ids,
    powershell_script_command,
    redact_sensitive_log,
    snowluma_webui_url,
)


def test_parse_ids_deduplicates_and_enforces_optional_limit():
    assert parse_ids("1, 1, 2") == (1, 2)
    with pytest.raises(ValueError, match="10"):
        parse_ids(",".join(str(value) for value in range(1, 12)), max_count=10)


def test_env_file_preserves_comments_and_updates_values(tmp_path: Path):
    path = tmp_path / ".env"
    path.write_text("# keep\nPORT=8080\n", encoding="utf-8")
    env = EnvFile(path)
    env.set_many({"PORT": "9000", "SNOWLUMA_DIR": r"C:\Snow Luma"})
    content = path.read_text(encoding="utf-8")
    assert "# keep" in content
    assert "PORT=9000" in content
    assert 'SNOWLUMA_DIR="C:\\Snow Luma"' in content


def test_sensitive_values_are_redacted_from_manager_output():
    line = "connected with access-token-value and api-key-value"
    safe = redact_sensitive_log(line, ("access-token-value", "api-key-value"))

    assert safe == "connected with <hidden> and <hidden>"


def test_snowluma_webui_url_is_loopback_only_and_validates_port():
    assert snowluma_webui_url(5099) == "http://127.0.0.1:5099"
    with pytest.raises(ValueError, match="1-65535"):
        snowluma_webui_url(0)


def test_powershell_script_command_keeps_path_and_arguments_separate(tmp_path: Path):
    script = tmp_path / "folder with spaces" / "status.ps1"
    command = powershell_script_command(script, "-Json")

    assert command[-2:] == [str(script), "-Json"]
    assert command[1:7] == [
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
    ]


def test_generic_manager_save_does_not_silently_select_snowluma():
    source = Path("manager.py").read_text(encoding="utf-8")
    save_config = source.split("    def save_config", 1)[1].split(
        "    def run_script", 1
    )[0]

    assert "QQ_PLATFORM_TRANSPORT" not in save_config


def test_manager_exposes_the_three_phase_snowluma_migration_scripts():
    source = Path("manager.py").read_text(encoding="utf-8")

    assert 'self.run_script("prepare_snowluma_migration.ps1")' in source
    assert 'self.run_script("begin_snowluma_cutover.ps1", "-StartCutover")' in source
    assert 'self.run_script("complete_snowluma_cutover.ps1")' in source


def test_manager_bundle_stays_a_thin_operator_shell_and_checks_build_failures():
    manager = Path("manager.py").read_text(encoding="utf-8")
    spec = Path("QQBotManager.spec").read_text(encoding="utf-8")
    build = Path("scripts/build_manager.ps1").read_text(encoding="utf-8")

    assert "executable_dir.parent" in manager
    assert "collect_submodules" not in spec
    assert "datas=[]" in spec
    assert "hiddenimports=[]" in spec
    assert build.count("$LASTEXITCODE -ne 0") >= 2
    assert "Test-Path -LiteralPath $output" in build
