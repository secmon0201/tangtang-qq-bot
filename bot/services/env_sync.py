from __future__ import annotations

import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any

from nonebot import logger

from bot.config import ROOT, settings


ENV_PATH = ROOT / ".env"

# Runtime settings that have a direct 1:1 .env key and are changed by commands.
FEATURE_SCOPE_ENV_KEYS = {
    "duplicate": "DUPLICATE_GROUP_IDS",
    "game": "GAME_GROUP_IDS",
    "game_api": "GAME_API_GROUP_IDS",
    "passive": "BOT_RANDOM_REACTION_GROUP_IDS",
    "hourly": "HOURLY_ANNOUNCEMENT_GROUP_IDS",
}

TEMPLATE_ENV_KEYS = {
    "reaction_probability": "BOT_RANDOM_REACTION_PROBABILITY",
    "reaction_cooldown_seconds": "BOT_RANDOM_REACTION_COOLDOWN_SECONDS",
    "repeat_probability": "BOT_RANDOM_REPEAT_PROBABILITY",
    "repeat_cooldown_seconds": "BOT_RANDOM_REPEAT_COOLDOWN_SECONDS",
    "repeat_message_interval": "BOT_RANDOM_REPEAT_MESSAGE_INTERVAL",
    "triple_repeat_enabled": "BOT_RANDOM_TRIPLE_REPEAT_ENABLED",
    "triple_repeat_probability": "BOT_RANDOM_TRIPLE_REPEAT_PROBABILITY",
}

PROACTIVE_ENV_KEYS = {
    "proactive_enabled": "TANGTANG_PROACTIVE_ENABLED",
    "proactive_probability": "TANGTANG_PROACTIVE_PROBABILITY",
    "proactive_cooldown_seconds": "TANGTANG_PROACTIVE_COOLDOWN_SECONDS",
    "proactive_message_interval": "TANGTANG_PROACTIVE_MESSAGE_INTERVAL",
}

_ALLOWED_KEYS = frozenset(
    (
        *FEATURE_SCOPE_ENV_KEYS.values(),
        *TEMPLATE_ENV_KEYS.values(),
        *PROACTIVE_ENV_KEYS.values(),
        "GAME_API_ENABLED",
        "TANGTANG_MODEL_ACTIVE_PROFILE",
    )
)
_KEY_RE = re.compile(r"^\s*(?P<key>[A-Za-z0-9_]+)\s*=(?P<value>.*)$")
_lock = threading.Lock()


def _write_lines(path: Path, lines: list[str]) -> None:
    """Atomically replace a text file with UTF-8 (no BOM) content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            for line in lines:
                fh.write(line.rstrip("\r\n") + "\n")
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def update_env_value(key: str, value: str, *, path: Path | None = None) -> bool:
    """Replace one .env value in place; appends when the key is missing."""

    key = key.strip().upper()
    if key not in _ALLOWED_KEYS:
        raise ValueError(f"env key is not writable through commands: {key}")
    env_path = ENV_PATH if path is None else Path(path)
    with _lock:
        if not env_path.exists():
            _write_lines(env_path, [f"{key}={value}"])
            return True
        lines = env_path.read_text(encoding="utf-8").splitlines()
        changed = False
        for index, line in enumerate(lines):
            match = _KEY_RE.match(line)
            if match and match.group("key").upper() == key:
                lines[index] = f"{key}={value}"
                changed = True
                break
        if not changed:
            lines.append(f"{key}={value}")
        _write_lines(env_path, lines)
    logger.info("Env synced: {}={}", key, value)
    return True


def read_env_value(key: str) -> str | None:
    key = key.strip().upper()
    if not ENV_PATH.exists():
        return None
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        match = _KEY_RE.match(line)
        if match and match.group("key").upper() == key:
            return match.group("value").strip()
    return None


def env_mapping_for_feature(feature: str) -> str | None:
    return FEATURE_SCOPE_ENV_KEYS.get(feature)


def sync_feature_scope(feature: str, group_ids: Any) -> None:
    env_key = env_mapping_for_feature(feature)
    if env_key is None:
        return
    value = ",".join(str(int(group_id)) for group_id in sorted(group_ids))
    try:
        update_env_value(env_key, value)
    except (OSError, ValueError) as exc:
        logger.warning("Env sync skipped for {}: {}", env_key, exc)


def sync_game_api_enabled(enabled: bool) -> None:
    """Mirror the game-interface hot switch into .env without needless writes."""
    value = str(bool(enabled)).lower()
    try:
        if read_env_value("GAME_API_ENABLED") == value:
            return
        update_env_value("GAME_API_ENABLED", value)
    except (OSError, ValueError) as exc:
        logger.warning("Env sync skipped for GAME_API_ENABLED: {}", exc)


def sync_passive_group_value(setting_key: str, group_id: int, value: str) -> None:
    """Replace one per-group passive setting while preserving the configured order."""

    env_key = TEMPLATE_ENV_KEYS.get(setting_key)
    if env_key is None:
        return
    group_order = settings.random_reaction_group_ids
    try:
        index = group_order.index(int(group_id))
    except ValueError as exc:
        raise ValueError("group is outside BOT_RANDOM_REACTION_GROUP_IDS") from exc
    raw = read_env_value(env_key)
    items = [] if raw is None else [item.strip() for item in raw.split(",")]
    if len(items) != len(group_order) or any(not item for item in items):
        raise ValueError(
            f"{env_key} must provide exactly one value for each "
            "BOT_RANDOM_REACTION_GROUP_IDS entry"
        )
    items[index] = str(value)
    update_env_value(env_key, ",".join(items))
