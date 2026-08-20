"""Snapshot and pause automatic QQ interactions without losing their settings.

Only non-secret automation keys are captured.  Restore returns those keys and
the per-group triple-repeat enablement to their exact pre-pause values.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
SNAPSHOT_DIR = ROOT / "data" / "config_snapshots"
ENV_KEYS = (
    "BOT_RANDOM_REACTION_ENABLED",
    "BOT_RANDOM_REPEAT_ENABLED",
    "BOT_RANDOM_TRIPLE_REPEAT_ENABLED",
    "TANGTANG_PROACTIVE_ENABLED",
)


def read_env(path: Path) -> tuple[list[str], dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    values: dict[str, str] = {}
    for line in lines:
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key.strip().upper()] = value.strip()
    return lines, values


def write_env_values(path: Path, updates: dict[str, str]) -> None:
    lines, _ = read_env(path)
    remaining = dict(updates)
    result: list[str] = []
    for line in lines:
        if "=" in line and not line.lstrip().startswith("#"):
            key = line.split("=", 1)[0].strip().upper()
            if key in remaining:
                result.append(f"{key}={remaining.pop(key)}")
                continue
        result.append(line)
    result.extend(f"{key}={value}" for key, value in remaining.items())
    path.write_text("\n".join(result) + "\n", encoding="utf-8")


def database_path(values: dict[str, str]) -> Path:
    value = Path(values.get("BOT_DB_PATH", "data/bot.db"))
    return value if value.is_absolute() else ROOT / value


def triple_repeat_states(path: Path) -> list[dict[str, str | int]]:
    if not path.exists():
        return []
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT group_id,setting_value FROM passive_group_settings "
            "WHERE setting_key='triple_repeat_enabled' ORDER BY group_id"
        ).fetchall()
    return [{"group_id": int(group_id), "value": str(value)} for group_id, value in rows]


def set_triple_repeat_states(path: Path, states: list[dict[str, str | int]], value: str | None) -> None:
    if not path.exists():
        return
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    with sqlite3.connect(path) as connection:
        if value is not None:
            connection.execute(
                "UPDATE passive_group_settings SET setting_value=?,updated_at=? "
                "WHERE setting_key='triple_repeat_enabled'",
                (value, timestamp),
            )
        else:
            connection.executemany(
                "UPDATE passive_group_settings SET setting_value=?,updated_at=? "
                "WHERE group_id=? AND setting_key='triple_repeat_enabled'",
                [(str(item["value"]), timestamp, int(item["group_id"])) for item in states],
            )


def set_runtime_pause(path: Path, active: bool) -> None:
    if not path.exists():
        return
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO passive_settings(setting_key,setting_value,updated_at) VALUES (?,?,?) "
            "ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value, "
            "updated_at=excluded.updated_at",
            ("automation_pause_active", str(active).lower(), timestamp),
        )


def pause() -> Path:
    _, values = read_env(ENV_PATH)
    db_path = database_path(values)
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "kind": "qq_automation_pause",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "env": {key: values.get(key, "<unset>") for key in ENV_KEYS},
        "triple_repeat_states": triple_repeat_states(db_path),
    }
    path = SNAPSHOT_DIR / (
        "qq_automation_pause_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".json"
    )
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    updates = {key: "false" for key in ENV_KEYS}
    triple_values = [item.strip() for item in values.get("BOT_RANDOM_TRIPLE_REPEAT_ENABLED", "").split(",")]
    if len(triple_values) > 1:
        updates["BOT_RANDOM_TRIPLE_REPEAT_ENABLED"] = ",".join("false" for _ in triple_values)
    write_env_values(ENV_PATH, updates)
    set_triple_repeat_states(db_path, snapshot["triple_repeat_states"], "false")
    set_runtime_pause(db_path, True)
    return path


def restore(path: Path) -> None:
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    if snapshot.get("kind") != "qq_automation_pause":
        raise ValueError("not a QQ automation pause snapshot")
    env = snapshot.get("env")
    if not isinstance(env, dict):
        raise ValueError("snapshot env section is invalid")
    updates = {key: str(value) for key, value in env.items() if value != "<unset>"}
    write_env_values(ENV_PATH, updates)
    _, values = read_env(ENV_PATH)
    states = snapshot.get("triple_repeat_states")
    if not isinstance(states, list):
        raise ValueError("snapshot triple-repeat section is invalid")
    set_triple_repeat_states(database_path(values), states, None)
    set_runtime_pause(database_path(values), False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restore", type=Path, help="restore one previously generated snapshot")
    args = parser.parse_args()
    if args.restore:
        restore(args.restore)
        print(f"restored QQ automation settings from {args.restore}")
    else:
        path = pause()
        print(f"paused high-risk QQ automation; snapshot={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
