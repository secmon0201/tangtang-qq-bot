import importlib.util
import sqlite3
from pathlib import Path


SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "pause_high_risk_automation.py"
SPEC = importlib.util.spec_from_file_location("pause_high_risk_automation", SCRIPT)
assert SPEC and SPEC.loader
pause_tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pause_tool)


def test_pause_and_restore_preserves_existing_automation_parameters(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    db_path = tmp_path / "bot.db"
    env_path.write_text(
        "BOT_DB_PATH=bot.db\n"
        "BOT_RANDOM_REACTION_ENABLED=true\n"
        "BOT_RANDOM_REPEAT_ENABLED=true\n"
        "BOT_RANDOM_TRIPLE_REPEAT_ENABLED=true,false\n"
        "TANGTANG_PROACTIVE_ENABLED=true\n",
        encoding="utf-8",
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE passive_settings (setting_key TEXT PRIMARY KEY, setting_value TEXT, updated_at TEXT)"
        )
        connection.execute(
            "CREATE TABLE passive_group_settings (group_id INTEGER, setting_key TEXT, "
            "setting_value TEXT, updated_at TEXT, PRIMARY KEY(group_id, setting_key))"
        )
        connection.executemany(
            "INSERT INTO passive_group_settings VALUES (?,?,?,?)",
            [(1, "triple_repeat_enabled", "true", "old"), (2, "triple_repeat_enabled", "false", "old")],
        )
    monkeypatch.setattr(pause_tool, "ROOT", tmp_path)
    monkeypatch.setattr(pause_tool, "ENV_PATH", env_path)
    monkeypatch.setattr(pause_tool, "SNAPSHOT_DIR", tmp_path / "snapshots")

    snapshot = pause_tool.pause()
    _, paused = pause_tool.read_env(env_path)
    assert paused["BOT_RANDOM_REACTION_ENABLED"] == "false"
    assert paused["BOT_RANDOM_REPEAT_ENABLED"] == "false"
    assert paused["BOT_RANDOM_TRIPLE_REPEAT_ENABLED"] == "false,false"
    assert paused["TANGTANG_PROACTIVE_ENABLED"] == "false"
    assert pause_tool.triple_repeat_states(db_path) == [
        {"group_id": 1, "value": "false"},
        {"group_id": 2, "value": "false"},
    ]
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT setting_value FROM passive_settings WHERE setting_key='automation_pause_active'"
        ).fetchone()[0] == "true"

    pause_tool.restore(snapshot)
    _, restored = pause_tool.read_env(env_path)
    assert restored["BOT_RANDOM_REACTION_ENABLED"] == "true"
    assert restored["BOT_RANDOM_TRIPLE_REPEAT_ENABLED"] == "true,false"
    assert restored["TANGTANG_PROACTIVE_ENABLED"] == "true"
    assert pause_tool.triple_repeat_states(db_path) == [
        {"group_id": 1, "value": "true"},
        {"group_id": 2, "value": "false"},
    ]
    with sqlite3.connect(db_path) as connection:
        assert connection.execute(
            "SELECT setting_value FROM passive_settings WHERE setting_key='automation_pause_active'"
        ).fetchone()[0] == "false"
