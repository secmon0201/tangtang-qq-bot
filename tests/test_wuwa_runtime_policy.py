from __future__ import annotations

import json
from pathlib import Path

from scripts.configure_wuwa_runtime import disable_rover_reminder


ROOT = Path(__file__).resolve().parents[1]


def test_disable_rover_reminder_creates_defensive_runtime_configs(tmp_path):
    plugin_path, reminder_path = disable_rover_reminder(tmp_path / "GsUID.Core")

    plugin = json.loads(plugin_path.read_text(encoding="utf-8"))
    reminder = json.loads(reminder_path.read_text(encoding="utf-8"))
    assert plugin["enabled"] is False
    assert "ww" in plugin["force_prefix"]
    assert reminder["EnableStaminaPush"]["data"] is False


def test_disable_rover_reminder_preserves_unrelated_runtime_settings(tmp_path):
    core = tmp_path / "GsUID.Core"
    plugin_path = core / "data" / "plugins_configs" / "RoverReminder.json"
    reminder_path = core / "data" / "RoverReminder" / "config.json"
    plugin_path.parent.mkdir(parents=True)
    reminder_path.parent.mkdir(parents=True)
    plugin_path.write_text(
        json.dumps({"enabled": True, "force_prefix": ["custom"], "alias": ["rr"]}),
        encoding="utf-8",
    )
    reminder_path.write_text(
        json.dumps(
            {
                "EnableStaminaPush": {"type": "GsBoolConfig", "data": True},
                "StaminaPushThreshold": {"type": "GsIntConfig", "data": 180},
            }
        ),
        encoding="utf-8",
    )

    disable_rover_reminder(core)

    plugin = json.loads(plugin_path.read_text(encoding="utf-8"))
    reminder = json.loads(reminder_path.read_text(encoding="utf-8"))
    assert plugin["enabled"] is False
    assert plugin["alias"] == ["rr"]
    assert plugin["force_prefix"] == ["custom", "ww"]
    assert reminder["EnableStaminaPush"]["data"] is False
    assert reminder["StaminaPushThreshold"]["data"] == 180


def test_pinned_scoreecho_keeps_original_token_failure_feedback():
    source = (
        ROOT
        / "GsUID.Core"
        / "gsuid_core"
        / "plugins"
        / "ScoreEcho"
        / "ScoreEcho"
        / "scoreecho_score"
        / "__init__.py"
    ).read_text(encoding="utf-8")

    assert source.count("response.raise_for_status()") >= 2
    assert source.count("API 请求失败，服务器返回错误码") >= 2
    assert source.count('e.response.json().get("detail", "无详细信息")') >= 2
