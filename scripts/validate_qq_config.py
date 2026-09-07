from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


ID_PATTERN = re.compile(r"^\d+$")
FORBIDDEN_CREDENTIAL_KEYS = {
    "QQ_PASSWORD",
    "QQ_PASS",
    "QQ_LOGIN_PASSWORD",
    "QQ_CAPTCHA",
    "QQ_VERIFICATION_CODE",
}
RETIRED_KEYS = {
    "CODEX_WORKER_ENABLED",
    "CODEX_WORKER_COMMAND",
    "CODEX_WORKER_POLL_SECONDS",
    "CODEX_WORKER_TIMEOUT_SECONDS",
    "CODEX_WORKER_SANDBOX",
}


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def parse_ids(value: str, label: str, maximum: int | None = None) -> tuple[str, ...]:
    items = tuple(dict.fromkeys(item.strip() for item in value.split(",") if item.strip()))
    if any(not ID_PATTERN.fullmatch(item) or int(item) <= 0 for item in items):
        raise ValueError(f"{label} must contain positive integers separated by commas")
    if maximum is not None and len(items) > maximum:
        raise ValueError(f"{label} cannot contain more than {maximum} items")
    return items


def parse_bool(value: str, label: str, default: bool = False) -> bool:
    normalized = value.strip().lower() if value else ("true" if default else "false")
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{label} must be true or false")


def parse_clock(value: str, label: str) -> None:
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value.strip()):
        raise ValueError(f"{label} must use HH:MM in 24-hour time")


def parse_positive_delay_range(values: dict[str, str], minimum_key: str, maximum_key: str, minimum_default: str, maximum_default: str) -> None:
    try:
        minimum = float(values.get(minimum_key, minimum_default))
        maximum = float(values.get(maximum_key, maximum_default))
    except ValueError as exc:
        raise ValueError(f"{minimum_key}/{maximum_key} must be positive numbers") from exc
    if minimum <= 0 or maximum <= 0 or maximum < minimum:
        raise ValueError(f"{maximum_key} must be at least {minimum_key}, and both must be positive")


def validate_profile(values: dict[str, str]) -> None:
    enabled = parse_bool(
        values.get("PROFILE_ENABLED", values.get("TANGTANG_ENABLED", "false")),
        "PROFILE_ENABLED",
    )
    if not enabled:
        return
    for key, fallback in (
        ("PROFILE_API_URL", "TANGTANG_API_URL"),
        ("PROFILE_API_KEY", "TANGTANG_API_KEY"),
        ("PROFILE_MODEL", "TANGTANG_MODEL"),
    ):
        if not values.get(key, values.get(fallback, "")).strip():
            raise ValueError(f"{key} (or {fallback}) is required when PROFILE_ENABLED=true")
    style = values.get(
        "PROFILE_API_STYLE", values.get("TANGTANG_API_STYLE", "responses")
    ).strip().lower()
    if style not in {"responses", "chat_completions"}:
        raise ValueError("PROFILE_API_STYLE must be responses or chat_completions")
    reasoning_effort = values.get(
        "PROFILE_REASONING_EFFORT", values.get("TANGTANG_REASONING_EFFORT", "none")
    ).strip().lower()
    if reasoning_effort not in {"none", "low", "high", "max"}:
        raise ValueError("PROFILE_REASONING_EFFORT is invalid")
    evidence_reasoning = values.get(
        "PROFILE_EVIDENCE_REASONING_EFFORT", "low"
    ).strip().lower()
    if evidence_reasoning not in {"none", "low", "high", "max"}:
        raise ValueError("PROFILE_EVIDENCE_REASONING_EFFORT is invalid")
    for key, default, minimum, maximum in (
        ("PROFILE_TIMEOUT_SECONDS", "120", 1, 600),
        ("PROFILE_MAX_INPUT_CHARS", "20000", 1000, 64000),
        ("PROFILE_MAX_OUTPUT_TOKENS", "16000", 16, 24000),
        ("PROFILE_MAX_RESPONSE_CHARS", "16000", 40, 24000),
        ("PROFILE_CHUNK_CHARS", "16000", 1000, 64000),
        ("PROFILE_MAX_RECORDS_PER_RUN", "100000", 100, 500000),
        ("PROFILE_MAX_CONCURRENT", "3", 1, 16),
        ("PROFILE_RETRY_MAX_ATTEMPTS", "3", 1, 10),
        ("PROFILE_RETRY_BASE_SECONDS", "2", 0, 60),
        ("PROFILE_MERGE_CHUNK_CHARS", "12000", 1000, 64000),
        ("PROFILE_EVIDENCE_CONCURRENCY", "5", 1, 16),
        ("PROFILE_EVIDENCE_MAX_CHARS", "4000", 100, 12000),
        ("PROFILE_EVIDENCE_MAX_TOKENS", "8000", 64, 24000),
        ("PROFILE_EVIDENCE_MAX_RESPONSE_CHARS", "16000", 200, 24000),
        ("PROFILE_FINAL_MAX_CHARS", "4000", 100, 8000),
        ("PROFILE_FINAL_MAX_TOKENS", "8000", 64, 24000),
        ("PROFILE_FINAL_MAX_RESPONSE_CHARS", "16000", 200, 24000),
    ):
        try:
            value = int(values.get(key, default))
        except ValueError as exc:
            raise ValueError(f"{key} must be an integer") from exc
        if not minimum <= value <= maximum:
            raise ValueError(f"{key} must be between {minimum} and {maximum}")
    max_input = int(values.get("PROFILE_MAX_INPUT_CHARS", "20000"))
    chunk = int(values.get("PROFILE_CHUNK_CHARS", "16000"))
    merge_chunk = int(values.get("PROFILE_MERGE_CHUNK_CHARS", "12000"))
    if chunk + 1000 > max_input:
        raise ValueError(
            "PROFILE_CHUNK_CHARS must leave at least 1000 chars below "
            "PROFILE_MAX_INPUT_CHARS"
        )
    if merge_chunk + 1000 > max_input:
        raise ValueError(
            "PROFILE_MERGE_CHUNK_CHARS must leave at least 1000 chars below "
            "PROFILE_MAX_INPUT_CHARS"
        )


def validate_asoul_bili(values: dict[str, str], managed_set: set[str]) -> None:
    groups = parse_ids(values.get("ASOUL_BILI_GROUP_IDS", ""), "ASOUL_BILI_GROUP_IDS")
    invalid = sorted(set(groups) - managed_set)
    if invalid:
        raise ValueError(f"ASOUL_BILI_GROUP_IDS contains groups outside MANAGED_GROUP_IDS: {invalid}")
    targets = parse_ids(values.get("ASOUL_BILI_TARGET_UIDS", "672328094,672342685,3537115310721181,3537115310721781,672353429,703007996,3493085336046382,3493082517474232"), "ASOUL_BILI_TARGET_UIDS")
    comment_targets = parse_ids(values.get("ASOUL_BILI_COMMENT_TARGET_UIDS", "672328094,672342685,3537115310721181,3537115310721781,672353429"), "ASOUL_BILI_COMMENT_TARGET_UIDS")
    invalid_comment_targets = sorted(set(comment_targets) - set(targets))
    if invalid_comment_targets:
        raise ValueError(
            "ASOUL_BILI_COMMENT_TARGET_UIDS must be a subset of "
            f"ASOUL_BILI_TARGET_UIDS: {invalid_comment_targets}"
        )
    for key, default in (
        ("ASOUL_BILI_ENABLED", "false"),
        ("ASOUL_BILI_PUSH_DYNAMIC", "true"),
        ("ASOUL_BILI_PUSH_VIDEO", "true"),
        ("ASOUL_BILI_PUSH_LIVE", "true"),
        ("ASOUL_BILI_PUSH_COMMENT", "false"),
        ("ASOUL_BILI_RENDER_CARDS", "true"),
    ):
        parse_bool(values.get(key, default), key)
    try:
        interval = int(values.get("ASOUL_BILI_POLL_INTERVAL_SECONDS", "300"))
    except ValueError as exc:
        raise ValueError("ASOUL_BILI_POLL_INTERVAL_SECONDS must be an integer between 60 and 3600") from exc
    if not 60 <= interval <= 3600:
        raise ValueError("ASOUL_BILI_POLL_INTERVAL_SECONDS must be between 60 and 3600")


def validate(path: Path) -> tuple[int, int]:
    values = read_env(path)
    forbidden = sorted(key for key in values if key.upper() in FORBIDDEN_CREDENTIAL_KEYS)
    if forbidden:
        raise ValueError(f"remove login credentials from .env: {', '.join(forbidden)}")
    retired = sorted(key for key in values if key.upper() in RETIRED_KEYS)
    if retired:
        raise ValueError(f"remove retired QQ Codex worker settings from .env: {', '.join(retired)}")

    transport = values.get("BOT_TRANSPORT", "onebot").strip().lower()
    if transport not in {"onebot", "qq_openapi"}:
        raise ValueError("BOT_TRANSPORT must be onebot or qq_openapi")
    qq_platform_transport = values.get("QQ_PLATFORM_TRANSPORT", "snowluma").strip().lower()
    if qq_platform_transport not in {"snowluma", "lagrange"}:
        raise ValueError("QQ_PLATFORM_TRANSPORT must be snowluma or lagrange")
    if qq_platform_transport == "snowluma":
        if not values.get("SNOWLUMA_DIR", "SnowLuma").strip():
            raise ValueError("SNOWLUMA_DIR cannot be empty when QQ_PLATFORM_TRANSPORT=snowluma")
        account_value = values.get("QQ_ACCOUNT_ID", "")
    else:
        account_value = values.get("QQ_ACCOUNT_ID", "")
    if qq_platform_transport == "lagrange" and not values.get("LAGRANGE_DIR", "Lagrange.OneBot").strip():
        raise ValueError("LAGRANGE_DIR cannot be empty when QQ_PLATFORM_TRANSPORT=lagrange")

    groups = parse_ids(values.get("MANAGED_GROUP_IDS", ""), "MANAGED_GROUP_IDS")
    managed_set = set(groups)
    if values.get("BOT_COMMAND_PREFIX", "#").strip() != "#":
        raise ValueError("BOT_COMMAND_PREFIX must be #")
    if parse_bool(values.get("BOT_REQUIRE_MENTION", "false"), "BOT_REQUIRE_MENTION"):
        raise ValueError("BOT_REQUIRE_MENTION must be false; commands use the # prefix")
    parse_positive_delay_range(
        values,
        "BOT_RESPONSE_DELAY_MIN_SECONDS",
        "BOT_RESPONSE_DELAY_MAX_SECONDS",
        "2",
        "5",
    )
    parse_positive_delay_range(
        values,
        "BOT_COMMAND_RESPONSE_DELAY_MIN_SECONDS",
        "BOT_COMMAND_RESPONSE_DELAY_MAX_SECONDS",
        "1",
        "2",
    )
    try:
        if float(values.get("BOT_ONEBOT_API_MIN_INTERVAL_SECONDS", "0.5")) <= 0:
            raise ValueError
    except ValueError as exc:
        raise ValueError("BOT_ONEBOT_API_MIN_INTERVAL_SECONDS must be a positive number") from exc
    validate_profile(values)
    validate_asoul_bili(values, managed_set)
    for feature_name in (
        "DUPLICATE_GROUP_IDS",
        "GAME_GROUP_IDS",
        "GAME_API_GROUP_IDS",
        "HOURLY_ANNOUNCEMENT_GROUP_IDS",
    ):
        default_groups = "" if feature_name == "HOURLY_ANNOUNCEMENT_GROUP_IDS" else ",".join(groups)
        feature_groups = parse_ids(values.get(feature_name, default_groups), feature_name)
        invalid = sorted(set(feature_groups) - managed_set)
        if invalid:
            raise ValueError(f"{feature_name} contains groups outside MANAGED_GROUP_IDS: {invalid}")
    operators = parse_ids(values.get("BOT_OPERATOR_IDS", ""), "BOT_OPERATOR_IDS")
    parse_ids(
        values.get("GLOBAL_ANNOUNCEMENT_OPERATOR_IDS", ""),
        "GLOBAL_ANNOUNCEMENT_OPERATOR_IDS",
    )
    account_ids = parse_ids(account_value, "QQ_ACCOUNT_ID", maximum=1)
    if transport == "onebot" and len(account_ids) != 1:
        raise ValueError("QQ_ACCOUNT_ID must contain exactly one account in OneBot mode")
    parse_bool(values.get("QQ_TRANSPORT_MAINTENANCE_ENABLED", "true"), "QQ_TRANSPORT_MAINTENANCE_ENABLED")
    try:
        maintenance_interval = int(values.get("QQ_TRANSPORT_MAINTENANCE_INTERVAL_SECONDS", "30"))
    except ValueError as exc:
        raise ValueError(
            "QQ_TRANSPORT_MAINTENANCE_INTERVAL_SECONDS must be an integer between 10 and 300"
        ) from exc
    if not 10 <= maintenance_interval <= 300:
        raise ValueError(
            "QQ_TRANSPORT_MAINTENANCE_INTERVAL_SECONDS must be between 10 and 300"
        )
    parse_bool(values.get("A_COAST_PROFILE_ENABLED", "true"), "A_COAST_PROFILE_ENABLED")
    parse_bool(values.get("GAME_API_ENABLED", "true"), "GAME_API_ENABLED")
    parse_bool(values.get("GSUID_ENABLED", "false"), "GSUID_ENABLED")

    try:
        port = int(values.get("PORT", "8080"))
        snowluma_webui_port = int(values.get("SNOWLUMA_WEBUI_PORT", "5099"))
        official_port = int(values.get("QQ_OPENAPI_PORT", "8081"))
        hour = int(values.get("BOT_ROLLUP_HOUR", "1"))
        minute = int(values.get("BOT_ROLLUP_MINUTE", "5"))
    except ValueError as exc:
        raise ValueError(
            "PORT, SNOWLUMA_WEBUI_PORT, QQ_OPENAPI_PORT and rollup time must be integers"
        ) from exc
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be between 1 and 65535")
    if not 1 <= official_port <= 65535:
        raise ValueError("QQ_OPENAPI_PORT must be between 1 and 65535")
    if not 1 <= snowluma_webui_port <= 65535:
        raise ValueError("SNOWLUMA_WEBUI_PORT must be between 1 and 65535")
    if qq_platform_transport == "snowluma" and snowluma_webui_port == port:
        raise ValueError("SNOWLUMA_WEBUI_PORT must differ from PORT")
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("BOT_ROLLUP_HOUR/MINUTE is outside the valid time range")
    parse_bool(values.get("HOURLY_ANNOUNCEMENT_ENABLED", "false"), "HOURLY_ANNOUNCEMENT_ENABLED")
    parse_clock(values.get("HOURLY_ANNOUNCEMENT_START", "00:00"), "HOURLY_ANNOUNCEMENT_START")
    parse_clock(values.get("HOURLY_ANNOUNCEMENT_END", "23:00"), "HOURLY_ANNOUNCEMENT_END")
    try:
        hourly_attempts = int(values.get("HOURLY_ANNOUNCEMENT_MAX_ATTEMPTS", "3"))
    except ValueError as exc:
        raise ValueError("HOURLY_ANNOUNCEMENT_MAX_ATTEMPTS must be an integer between 1 and 5") from exc
    if not 1 <= hourly_attempts <= 5:
        raise ValueError("HOURLY_ANNOUNCEMENT_MAX_ATTEMPTS must be between 1 and 5")
    if not operators:
        raise ValueError("BOT_OPERATOR_IDS must contain at least one operator")
    if parse_bool(
        values.get("CODEX_COMPLETION_NOTIFY_ENABLED", "false"),
        "CODEX_COMPLETION_NOTIFY_ENABLED",
    ):
        if values.get("HOST", "127.0.0.1").strip().lower() not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ValueError("CODEX_COMPLETION_NOTIFY_ENABLED requires a localhost HOST")
        completion_group = parse_ids(
            values.get("CODEX_COMPLETION_NOTIFY_GROUP_ID", ""),
            "CODEX_COMPLETION_NOTIFY_GROUP_ID",
            maximum=1,
        )
        completion_admin = parse_ids(
            values.get("CODEX_COMPLETION_NOTIFY_SUPER_ADMIN_ID", ""),
            "CODEX_COMPLETION_NOTIFY_SUPER_ADMIN_ID",
            maximum=1,
        )
        if len(completion_group) != 1 or completion_group[0] not in managed_set:
            raise ValueError("CODEX_COMPLETION_NOTIFY_GROUP_ID must belong to MANAGED_GROUP_IDS")
        if len(completion_admin) != 1 or completion_admin[0] not in set(operators):
            raise ValueError(
                "CODEX_COMPLETION_NOTIFY_SUPER_ADMIN_ID must belong to BOT_OPERATOR_IDS"
            )
        if not (
            values.get("CODEX_COMPLETION_NOTIFY_TOKEN", "").strip()
            or values.get("ONEBOT_ACCESS_TOKEN", "").strip()
        ):
            raise ValueError(
                "CODEX_COMPLETION_NOTIFY_TOKEN or ONEBOT_ACCESS_TOKEN is required when notifications are enabled"
            )
    if transport == "qq_openapi":
        missing = [
            key
            for key in ("QQ_OPENAPI_APP_ID", "QQ_OPENAPI_TOKEN", "QQ_OPENAPI_APP_SECRET")
            if not values.get(key, "").strip()
        ]
        if missing:
            raise ValueError("qq_openapi mode requires " + ", ".join(missing))
        if not ID_PATTERN.fullmatch(values["QQ_OPENAPI_APP_ID"].strip()):
            raise ValueError("QQ_OPENAPI_APP_ID must be numeric")
    return len(groups), len(operators)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate local QQ bot configuration without contacting QQ")
    parser.add_argument("--env", type=Path, default=Path(".env"))
    args = parser.parse_args()
    try:
        groups, operators = validate(args.env)
    except (OSError, ValueError) as exc:
        print(f"configuration invalid: {exc}", file=sys.stderr)
        return 1
    print(f"configuration valid: groups={groups}, operators={operators}, max_groups=unlimited")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
