from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(os.getenv("QQ_BOT_ROOT", Path(__file__).resolve().parent.parent))
RESOURCE_DIR = ROOT / "bot" / "resources"
load_dotenv(ROOT / ".env")


# A海岸是内置的首个私有集群；其他群由 SQLite 动态登记。
A_COAST_GROUP_IDS = (1128870029, 1077416717, 1083457871, 1090284567, 278824712)


def _csv_ints(value: str | None) -> tuple[int, ...]:
    if not value:
        return ()
    result: list[int] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        if not item.isdigit() or int(item) <= 0:
            raise ValueError(f"invalid QQ/group ID: {item!r}")
        result.append(int(item))
    return tuple(dict.fromkeys(result))


def _csv_game_numbers(value: str | None, name: str) -> tuple[int, ...]:
    if not value:
        return ()
    result: list[int] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        if not item.isdigit() or not 0 <= int(item) <= 999:
            raise ValueError(f"{name} must contain integers from 0 to 999 separated by commas")
        result.append(int(item))
    return tuple(dict.fromkeys(result))


def _csv_digit_strings(value: str | None, name: str) -> tuple[str, ...]:
    if not value:
        return ()
    result: list[str] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        if not item.isdigit() or int(item) <= 0:
            raise ValueError(f"{name} must contain positive numeric IDs separated by commas")
        result.append(item)
    return tuple(dict.fromkeys(result))


DEFAULT_MENTION_ACK_EMOJI_IDS = (
    "14", "20", "21", "43", "49", "66", "76", "78", "79", "99", "118", "124",
    "180", "199", "201", "228", "282", "294", "299", "315", "319", "320", "341", "346",
)


def _csv_emoji_ids(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    result: list[str] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", item):
            raise ValueError(f"invalid emoji ID: {item!r}")
        result.append(item)
    return tuple(dict.fromkeys(result))


def _clock_minutes(value: str, name: str) -> int:
    match = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", value.strip())
    if not match:
        raise ValueError(f"{name} must use HH:MM in 24-hour time")
    return int(match.group(1)) * 60 + int(match.group(2))


def managed_group_order(group_ids: Iterable[int], managed_group_ids: Iterable[int]) -> tuple[int, ...]:
    """Order a group subset by MANAGED_GROUP_IDS position.

    Groups not listed in MANAGED_GROUP_IDS keep a stable relative order and are
    appended after every managed group.
    """
    order = {int(group_id): index for index, group_id in enumerate(managed_group_ids)}
    return tuple(
        sorted(
            {int(group_id) for group_id in group_ids},
            key=lambda group_id: order.get(group_id, len(order)),
        )
    )


@dataclass(frozen=True, slots=True)
class Settings:
    transport: str
    qq_platform_transport: str
    managed_group_ids: tuple[int, ...]
    duplicate_group_ids: tuple[int, ...]
    game_group_ids: tuple[int, ...]
    game_api_group_ids: tuple[int, ...]
    operator_ids: frozenset[int]
    global_announcement_operator_ids: frozenset[int]
    command_prefix: str
    timezone: str
    rollup_hour: int
    rollup_minute: int
    db_path: Path
    host: str
    port: int
    official_port: int
    official_app_id: str | None
    official_token: str | None
    official_app_secret: str | None
    official_sandbox: bool
    onebot_access_token: str | None
    qq_transport_maintenance_enabled: bool
    qq_transport_maintenance_interval_seconds: int
    codex_completion_notify_enabled: bool
    codex_completion_notify_group_id: int | None
    codex_completion_notify_super_admin_id: int | None
    codex_completion_notify_token: str | None
    report_output_mode: str
    report_dir: Path
    report_font_path: Path | None
    report_retention_hours: int
    avatar_base_url: str
    avatar_cache_dir: Path
    avatar_timeout: float
    avatar_cache_ttl: int
    avatar_refresh_interval: int
    avatar_refresh_cooldown: int
    avatar_refresh_max_per_call: int
    avatar_refresh_concurrency: int
    gsuid_enabled: bool
    game_api_enabled: bool
    gsuid_core_dir: Path
    gsuid_core_host: str
    gsuid_core_port: int
    gsuid_core_ws_token: str | None
    gsuid_core_botid: str
    stats_realtime_enabled: bool
    a_coast_profile_enabled: bool
    require_mention: bool
    response_delay_min_seconds: float
    response_delay_max_seconds: float
    command_response_delay_min_seconds: float
    command_response_delay_max_seconds: float
    onebot_api_min_interval_seconds: float
    duplicate_scan_cooldown_seconds: int
    mention_ack_emoji_ids: tuple[str, ...]
    random_reaction_enabled: bool
    random_reaction_group_ids: tuple[int, ...]
    random_reaction_probability: float
    random_reaction_cooldown_seconds: int
    random_reaction_emoji_ids: tuple[str, ...]
    random_repeat_enabled: bool
    random_repeat_probability: float
    random_repeat_probability_by_group: dict[int, float]
    random_repeat_cooldown_seconds: int
    random_repeat_cooldown_seconds_by_group: dict[int, int]
    random_repeat_message_interval: int
    random_repeat_message_interval_by_group: dict[int, int]
    random_triple_repeat_probability: float
    random_triple_repeat_enabled_by_group: dict[int, bool]
    random_triple_repeat_probability_by_group: dict[int, float]
    feedback_notification_interval_seconds: int
    hourly_announcement_enabled: bool
    hourly_announcement_group_ids: tuple[int, ...]
    hourly_announcement_start_minute: int
    hourly_announcement_end_minute: int
    hourly_announcement_max_attempts: int
    guess_cursed_numbers: tuple[int, ...]
    zhijiang_live_guard_enabled: bool
    zhijiang_schedule_url: str
    zhijiang_schedule_refresh_minutes: int
    zhijiang_live_pause_minutes: int
    zhijiang_schedule_lookahead_days: int
    zhijiang_schedule_timeout: float
    asoul_bili_enabled: bool
    asoul_bili_poll_interval_seconds: int
    asoul_bili_group_ids: tuple[int, ...]
    asoul_bili_push_a_coast: bool
    asoul_bili_a_coast_group_ids: tuple[int, ...]
    asoul_bili_target_uids: tuple[str, ...]
    asoul_bili_comment_target_uids: tuple[str, ...]
    asoul_bili_push_dynamic: bool
    asoul_bili_push_video: bool
    asoul_bili_push_live: bool
    asoul_bili_push_comment: bool
    asoul_bili_render_cards: bool

    @classmethod
    def from_env(cls) -> "Settings":
        transport = os.getenv("BOT_TRANSPORT", "onebot").strip().lower()
        if transport not in {"onebot", "qq_openapi"}:
            raise ValueError("BOT_TRANSPORT must be onebot or qq_openapi")
        qq_platform_transport = os.getenv("QQ_PLATFORM_TRANSPORT", "snowluma").strip().lower()
        if qq_platform_transport not in {"snowluma", "lagrange"}:
            raise ValueError("QQ_PLATFORM_TRANSPORT must be snowluma or lagrange")
        groups = _csv_ints(os.getenv("MANAGED_GROUP_IDS"))

        def feature_groups(name: str) -> tuple[int, ...]:
            raw = os.getenv(name)
            selected = groups if raw is None else _csv_ints(raw)
            invalid = sorted(set(selected) - set(groups))
            if invalid:
                raise ValueError(f"{name} contains groups outside MANAGED_GROUP_IDS: {invalid}")
            return selected

        duplicate_groups = feature_groups("DUPLICATE_GROUP_IDS")
        game_groups = feature_groups("GAME_GROUP_IDS")
        game_api_groups = feature_groups("GAME_API_GROUP_IDS")
        hourly_raw = os.getenv("HOURLY_ANNOUNCEMENT_GROUP_IDS", "")
        hourly_groups = _csv_ints(hourly_raw)
        invalid_hourly_groups = sorted(set(hourly_groups) - set(groups))
        if invalid_hourly_groups:
            raise ValueError(
                "HOURLY_ANNOUNCEMENT_GROUP_IDS contains groups outside MANAGED_GROUP_IDS: "
                f"{invalid_hourly_groups}"
            )
        asoul_bili_group_ids = _csv_ints(os.getenv("ASOUL_BILI_GROUP_IDS", ""))
        invalid_asoul_bili_groups = sorted(set(asoul_bili_group_ids) - set(groups))
        if invalid_asoul_bili_groups:
            raise ValueError(
                "ASOUL_BILI_GROUP_IDS contains groups outside MANAGED_GROUP_IDS: "
                f"{invalid_asoul_bili_groups}"
            )
        asoul_bili_a_coast_group_ids = _csv_ints(
            os.getenv("ASOUL_BILI_A_COAST_GROUP_IDS", ",".join(map(str, A_COAST_GROUP_IDS)))
        )
        operators = frozenset(_csv_ints(os.getenv("BOT_OPERATOR_IDS")))
        global_announcement_operators = frozenset(
            _csv_ints(os.getenv("GLOBAL_ANNOUNCEMENT_OPERATOR_IDS"))
        )
        prefix = os.getenv("BOT_COMMAND_PREFIX", "#").strip()
        if prefix != "#":
            raise ValueError("BOT_COMMAND_PREFIX must be #")

        db_value = os.getenv("BOT_DB_PATH", "data/bot.db")
        db_path = Path(db_value)
        if not db_path.is_absolute():
            db_path = ROOT / db_path

        report_mode = os.getenv("REPORT_OUTPUT_MODE", "local_image").strip().lower()
        if report_mode not in {"local_image", "text"}:
            raise ValueError("REPORT_OUTPUT_MODE must be local_image or text")
        report_value = os.getenv("REPORT_DIR", "data/reports")
        report_dir = Path(report_value)
        if not report_dir.is_absolute():
            report_dir = ROOT / report_dir
        font_value = os.getenv("REPORT_FONT_PATH", "").strip()
        report_font_path = Path(font_value) if font_value else None
        if report_font_path is not None and not report_font_path.is_absolute():
            report_font_path = ROOT / report_font_path
        avatar_cache_value = os.getenv("AVATAR_CACHE_DIR", "data/avatar_cache")
        avatar_cache_dir = Path(avatar_cache_value)
        if not avatar_cache_dir.is_absolute():
            avatar_cache_dir = ROOT / avatar_cache_dir
        gsuid_core_value = os.getenv("GSUID_CORE_DIR", "GsUID.Core")
        gsuid_core_dir = Path(gsuid_core_value)
        if not gsuid_core_dir.is_absolute():
            gsuid_core_dir = ROOT / gsuid_core_dir

        def boolean(name: str, default: bool) -> bool:
            value = os.getenv(name, str(default)).strip().lower()
            if value in {"1", "true", "yes", "on"}:
                return True
            if value in {"0", "false", "no", "off"}:
                return False
            raise ValueError(f"{name} must be true or false")

        asoul_bili_push_a_coast = boolean("ASOUL_BILI_PUSH_A_COAST", True)
        if asoul_bili_push_a_coast:
            missing_a_coast_bili_groups = sorted(set(asoul_bili_a_coast_group_ids) - set(groups))
            if missing_a_coast_bili_groups:
                raise ValueError(
                    "ASOUL_BILI_A_COAST_GROUP_IDS contains groups outside MANAGED_GROUP_IDS: "
                    f"{missing_a_coast_bili_groups}"
                )

        def integer(name: str, default: int, minimum: int, maximum: int) -> int:
            value = int(os.getenv(name, str(default)))
            if not minimum <= value <= maximum:
                raise ValueError(f"{name} must be between {minimum} and {maximum}")
            return value

        def optional_qq_id(name: str) -> int | None:
            raw = os.getenv(name, "").strip()
            if not raw:
                return None
            if not raw.isdigit() or int(raw) <= 0:
                raise ValueError(f"{name} must be a positive QQ/group ID")
            return int(raw)

        def float_value(name: str, default: float) -> float:
            value = float(os.getenv(name, str(default)))
            if value <= 0:
                raise ValueError(f"{name} must be positive")
            return value

        zhijiang_schedule_url = os.getenv(
            "ZHIJIANG_SCHEDULE_URL",
            "https://raw.githubusercontent.com/Evelynall/ASoul-Data/main/base-schedules.json",
        ).strip()
        if not zhijiang_schedule_url.startswith(("https://", "http://")):
            raise ValueError("ZHIJIANG_SCHEDULE_URL must be an http(s) URL")
        response_delay_min_seconds = float_value("BOT_RESPONSE_DELAY_MIN_SECONDS", 2)
        response_delay_max_seconds = float_value("BOT_RESPONSE_DELAY_MAX_SECONDS", 5)
        if response_delay_max_seconds < response_delay_min_seconds:
            raise ValueError("BOT_RESPONSE_DELAY_MAX_SECONDS must be at least BOT_RESPONSE_DELAY_MIN_SECONDS")
        command_response_delay_min_seconds = float_value(
            "BOT_COMMAND_RESPONSE_DELAY_MIN_SECONDS", 1
        )
        command_response_delay_max_seconds = float_value(
            "BOT_COMMAND_RESPONSE_DELAY_MAX_SECONDS", 2
        )
        if command_response_delay_max_seconds < command_response_delay_min_seconds:
            raise ValueError(
                "BOT_COMMAND_RESPONSE_DELAY_MAX_SECONDS must be at least "
                "BOT_COMMAND_RESPONSE_DELAY_MIN_SECONDS"
            )
        hourly_start_minute = _clock_minutes(
            os.getenv("HOURLY_ANNOUNCEMENT_START", "00:00"),
            "HOURLY_ANNOUNCEMENT_START",
        )
        hourly_end_minute = _clock_minutes(
            os.getenv("HOURLY_ANNOUNCEMENT_END", "23:00"),
            "HOURLY_ANNOUNCEMENT_END",
        )
        mention_ack_emoji_ids = _csv_emoji_ids(
            os.getenv("BOT_MENTION_ACK_EMOJI_IDS", ",".join(DEFAULT_MENTION_ACK_EMOJI_IDS))
        )
        if not mention_ack_emoji_ids:
            raise ValueError("BOT_MENTION_ACK_EMOJI_IDS must contain at least one emoji ID")
        random_reaction_groups = _csv_ints(os.getenv("BOT_RANDOM_REACTION_GROUP_IDS"))
        invalid_random_groups = sorted(set(random_reaction_groups) - set(groups))
        if invalid_random_groups:
            raise ValueError(
                "BOT_RANDOM_REACTION_GROUP_IDS contains groups outside MANAGED_GROUP_IDS: "
                f"{invalid_random_groups}"
            )
        random_reaction_probability = float(os.getenv("BOT_RANDOM_REACTION_PROBABILITY", "0.15"))
        if not 0 <= random_reaction_probability <= 1:
            raise ValueError("BOT_RANDOM_REACTION_PROBABILITY must be between 0 and 1")
        random_reaction_emoji_ids = _csv_emoji_ids(
            os.getenv("BOT_RANDOM_REACTION_EMOJI_IDS", ",".join(DEFAULT_MENTION_ACK_EMOJI_IDS))
        )
        random_reaction_enabled = boolean("BOT_RANDOM_REACTION_ENABLED", False)
        if random_reaction_enabled and not random_reaction_groups:
            raise ValueError("BOT_RANDOM_REACTION_GROUP_IDS is required when random reactions are enabled")
        if random_reaction_enabled and not random_reaction_emoji_ids:
            raise ValueError("BOT_RANDOM_REACTION_EMOJI_IDS must contain at least one emoji ID")
        random_repeat_enabled = boolean("BOT_RANDOM_REPEAT_ENABLED", True)

        def group_values(name: str, default: object, parser) -> dict[int, object]:
            raw = os.getenv(name)
            if not random_reaction_groups:
                return {}
            if raw is None:
                return {group_id: default for group_id in random_reaction_groups}
            items = [item.strip() for item in raw.split(",")]
            if len(items) != len(random_reaction_groups) or any(not item for item in items):
                raise ValueError(
                    f"{name} must provide exactly one value for each "
                    "BOT_RANDOM_REACTION_GROUP_IDS entry"
                )
            return {
                group_id: parser(item)
                for group_id, item in zip(random_reaction_groups, items)
            }

        def group_float(name: str, default: float, minimum: float, maximum: float) -> dict[int, float]:
            def parse(item: str) -> float:
                value = float(item)
                if not minimum <= value <= maximum:
                    raise ValueError(f"{name} must be between {minimum} and {maximum}")
                return value

            return group_values(name, default, parse)  # type: ignore[return-value]

        def group_integer(name: str, default: int, minimum: int, maximum: int) -> dict[int, int]:
            def parse(item: str) -> int:
                value = int(item)
                if not minimum <= value <= maximum:
                    raise ValueError(f"{name} must be between {minimum} and {maximum}")
                return value

            return group_values(name, default, parse)  # type: ignore[return-value]

        def group_boolean(name: str, default: bool) -> dict[int, bool]:
            def parse(item: str) -> bool:
                value = item.lower()
                if value in {"1", "true", "yes", "on"}:
                    return True
                if value in {"0", "false", "no", "off"}:
                    return False
                raise ValueError(f"{name} must contain true or false values")

            return group_values(name, default, parse)  # type: ignore[return-value]

        random_repeat_probability_by_group = group_float(
            "BOT_RANDOM_REPEAT_PROBABILITY", 0.10, 0.0, 0.10
        )
        random_repeat_cooldown_seconds_by_group = group_integer(
            "BOT_RANDOM_REPEAT_COOLDOWN_SECONDS", 900, 0, 86400
        )
        random_repeat_message_interval_by_group = group_integer(
            "BOT_RANDOM_REPEAT_MESSAGE_INTERVAL", 50, 0, 10000
        )
        random_triple_repeat_enabled_by_group = group_boolean(
            "BOT_RANDOM_TRIPLE_REPEAT_ENABLED", False
        )
        random_triple_repeat_probability_by_group = group_float(
            "BOT_RANDOM_TRIPLE_REPEAT_PROBABILITY", 0.30, 0.0, 1.0
        )
        if random_reaction_groups:
            first_group_id = random_reaction_groups[0]
            random_repeat_probability = random_repeat_probability_by_group[first_group_id]
            random_repeat_cooldown_seconds = random_repeat_cooldown_seconds_by_group[first_group_id]
            random_repeat_message_interval = random_repeat_message_interval_by_group[first_group_id]
            random_triple_repeat_probability = random_triple_repeat_probability_by_group[first_group_id]
        else:
            random_repeat_probability = float(
                os.getenv("BOT_RANDOM_REPEAT_PROBABILITY", "0.10").split(",")[0]
            )
            if not 0 <= random_repeat_probability <= 0.10:
                raise ValueError("BOT_RANDOM_REPEAT_PROBABILITY must be between 0 and 0.1")
            cooldown_raw = os.getenv("BOT_RANDOM_REPEAT_COOLDOWN_SECONDS", "900").split(",")[0]
            random_repeat_cooldown_seconds = int(cooldown_raw)
            if not 0 <= random_repeat_cooldown_seconds <= 86400:
                raise ValueError("BOT_RANDOM_REPEAT_COOLDOWN_SECONDS must be between 0 and 86400")
            interval_raw = os.getenv("BOT_RANDOM_REPEAT_MESSAGE_INTERVAL", "50").split(",")[0]
            random_repeat_message_interval = int(interval_raw)
            if not 0 <= random_repeat_message_interval <= 10000:
                raise ValueError("BOT_RANDOM_REPEAT_MESSAGE_INTERVAL must be between 0 and 10000")
            random_triple_repeat_probability = float(
                os.getenv("BOT_RANDOM_TRIPLE_REPEAT_PROBABILITY", "0.30").split(",")[0]
            )
            if not 0 <= random_triple_repeat_probability <= 1:
                raise ValueError("BOT_RANDOM_TRIPLE_REPEAT_PROBABILITY must be between 0 and 1")
        asoul_bili_target_uids = _csv_digit_strings(
            os.getenv(
                "ASOUL_BILI_TARGET_UIDS",
                "672328094,672342685,3537115310721181,3537115310721781,672353429,703007996,3493085336046382,3493082517474232",
            ),
            "ASOUL_BILI_TARGET_UIDS",
        )
        asoul_bili_comment_target_uids = _csv_digit_strings(
            os.getenv(
                "ASOUL_BILI_COMMENT_TARGET_UIDS",
                "672328094,672342685,3537115310721181,3537115310721781,672353429",
            ),
            "ASOUL_BILI_COMMENT_TARGET_UIDS",
        )
        invalid_comment_targets = sorted(
            set(asoul_bili_comment_target_uids) - set(asoul_bili_target_uids)
        )
        if invalid_comment_targets:
            raise ValueError(
                "ASOUL_BILI_COMMENT_TARGET_UIDS must be a subset of "
                f"ASOUL_BILI_TARGET_UIDS: {invalid_comment_targets}"
            )

        official_app_id = os.getenv("QQ_OPENAPI_APP_ID", "").strip() or None
        official_token = os.getenv("QQ_OPENAPI_TOKEN", "").strip() or None
        official_app_secret = os.getenv("QQ_OPENAPI_APP_SECRET", "").strip() or None
        official_sandbox = boolean("QQ_OPENAPI_SANDBOX", True)
        codex_completion_notify_enabled = boolean("CODEX_COMPLETION_NOTIFY_ENABLED", False)
        codex_completion_notify_group_id = optional_qq_id("CODEX_COMPLETION_NOTIFY_GROUP_ID")
        codex_completion_notify_super_admin_id = optional_qq_id(
            "CODEX_COMPLETION_NOTIFY_SUPER_ADMIN_ID"
        )
        codex_completion_notify_token = (
            os.getenv("CODEX_COMPLETION_NOTIFY_TOKEN", "").strip()
            or os.getenv("ONEBOT_ACCESS_TOKEN", "").strip()
            or None
        )
        if codex_completion_notify_enabled:
            if transport != "onebot":
                raise ValueError("CODEX_COMPLETION_NOTIFY_ENABLED requires BOT_TRANSPORT=onebot")
            if os.getenv("HOST", "127.0.0.1").strip().lower() not in {
                "127.0.0.1",
                "localhost",
                "::1",
            }:
                raise ValueError("CODEX_COMPLETION_NOTIFY_ENABLED requires a localhost HOST")
            if codex_completion_notify_group_id is None:
                raise ValueError("CODEX_COMPLETION_NOTIFY_GROUP_ID is required when notifications are enabled")
            if codex_completion_notify_group_id not in groups:
                raise ValueError(
                    "CODEX_COMPLETION_NOTIFY_GROUP_ID must belong to MANAGED_GROUP_IDS"
                )
            if codex_completion_notify_super_admin_id is None:
                raise ValueError(
                    "CODEX_COMPLETION_NOTIFY_SUPER_ADMIN_ID is required when notifications are enabled"
                )
            if codex_completion_notify_super_admin_id not in operators:
                raise ValueError(
                    "CODEX_COMPLETION_NOTIFY_SUPER_ADMIN_ID must belong to BOT_OPERATOR_IDS"
                )
            if codex_completion_notify_token is None:
                raise ValueError(
                    "CODEX_COMPLETION_NOTIFY_TOKEN or ONEBOT_ACCESS_TOKEN is required when notifications are enabled"
                )

        require_mention = boolean("BOT_REQUIRE_MENTION", False)
        if require_mention:
            raise ValueError("BOT_REQUIRE_MENTION must be false; commands use the # prefix")
        if transport == "qq_openapi":
            missing = [
                name
                for name, value in (
                    ("QQ_OPENAPI_APP_ID", official_app_id),
                    ("QQ_OPENAPI_TOKEN", official_token),
                    ("QQ_OPENAPI_APP_SECRET", official_app_secret),
                )
                if not value
            ]
            if missing:
                raise ValueError(
                    "qq_openapi mode requires " + ", ".join(missing)
                )
            if not str(official_app_id).isdigit():
                raise ValueError("QQ_OPENAPI_APP_ID must be numeric")

        cursed_guess_numbers = _csv_game_numbers(
            os.getenv("MINI_GAME_GUESS_CURSED_NUMBERS", "510,731,444,33"),
            "MINI_GAME_GUESS_CURSED_NUMBERS",
        )
        if len(cursed_guess_numbers) >= 1000:
            raise ValueError("MINI_GAME_GUESS_CURSED_NUMBERS cannot block every possible answer")

        return cls(
            transport=transport,
            qq_platform_transport=qq_platform_transport,
            managed_group_ids=groups,
            duplicate_group_ids=duplicate_groups,
            game_group_ids=game_groups,
            game_api_group_ids=game_api_groups,
            operator_ids=operators,
            global_announcement_operator_ids=global_announcement_operators,
            command_prefix=prefix,
            timezone=os.getenv("BOT_TIMEZONE", "Asia/Shanghai"),
            rollup_hour=integer("BOT_ROLLUP_HOUR", 1, 0, 23),
            rollup_minute=integer("BOT_ROLLUP_MINUTE", 5, 0, 59),
            db_path=db_path,
            host=os.getenv("HOST", "127.0.0.1"),
            port=integer("PORT", 8080, 1, 65535),
            official_port=integer("QQ_OPENAPI_PORT", 8081, 1, 65535),
            official_app_id=official_app_id,
            official_token=official_token,
            official_app_secret=official_app_secret,
            official_sandbox=official_sandbox,
            onebot_access_token=os.getenv("ONEBOT_ACCESS_TOKEN") or None,
            qq_transport_maintenance_enabled=boolean("QQ_TRANSPORT_MAINTENANCE_ENABLED", True),
            qq_transport_maintenance_interval_seconds=integer(
                "QQ_TRANSPORT_MAINTENANCE_INTERVAL_SECONDS",
                30,
                10,
                300,
            ),
            codex_completion_notify_enabled=codex_completion_notify_enabled,
            codex_completion_notify_group_id=codex_completion_notify_group_id,
            codex_completion_notify_super_admin_id=codex_completion_notify_super_admin_id,
            codex_completion_notify_token=codex_completion_notify_token,
            report_output_mode=report_mode,
            report_dir=report_dir,
            report_font_path=report_font_path,
            report_retention_hours=integer("REPORT_RETENTION_HOURS", 24, 1, 168),
            avatar_base_url=os.getenv(
                "AVATAR_BASE_URL",
                "https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640",
            ).strip(),
            avatar_cache_dir=avatar_cache_dir,
            avatar_timeout=float_value("AVATAR_TIMEOUT", 5),
            avatar_cache_ttl=integer("AVATAR_CACHE_TTL", 604800, 1, 2592000),
            avatar_refresh_interval=integer("AVATAR_REFRESH_INTERVAL_SECONDS", 3600, 60, 2592000),
            avatar_refresh_cooldown=integer("AVATAR_REFRESH_COOLDOWN_SECONDS", 900, 30, 86400),
            avatar_refresh_max_per_call=integer("AVATAR_REFRESH_MAX_PER_CALL", 24, 1, 256),
            avatar_refresh_concurrency=integer("AVATAR_REFRESH_CONCURRENCY", 2, 1, 8),
            gsuid_enabled=boolean("GSUID_ENABLED", False),
            game_api_enabled=boolean("GAME_API_ENABLED", True),
            gsuid_core_dir=gsuid_core_dir,
            gsuid_core_host=os.getenv("gsuid_core_host", "127.0.0.1"),
            gsuid_core_port=integer("gsuid_core_port", 8765, 1, 65535),
            gsuid_core_ws_token=os.getenv("gsuid_core_ws_token") or None,
            gsuid_core_botid=os.getenv("gsuid_core_botid", "QQLocalDataBot"),
            stats_realtime_enabled=boolean("STATS_REALTIME_ENABLED", True),
            a_coast_profile_enabled=boolean("A_COAST_PROFILE_ENABLED", True),
            require_mention=require_mention,
            response_delay_min_seconds=response_delay_min_seconds,
            response_delay_max_seconds=response_delay_max_seconds,
            command_response_delay_min_seconds=command_response_delay_min_seconds,
            command_response_delay_max_seconds=command_response_delay_max_seconds,
            onebot_api_min_interval_seconds=float_value("BOT_ONEBOT_API_MIN_INTERVAL_SECONDS", 0.5),
            duplicate_scan_cooldown_seconds=integer("DUPLICATE_SCAN_COOLDOWN_SECONDS", 300, 30, 3600),
            mention_ack_emoji_ids=mention_ack_emoji_ids,
            random_reaction_enabled=random_reaction_enabled,
            random_reaction_group_ids=random_reaction_groups,
            random_reaction_probability=random_reaction_probability,
            random_reaction_cooldown_seconds=integer(
                "BOT_RANDOM_REACTION_COOLDOWN_SECONDS", 180, 0, 3600
            ),
            random_reaction_emoji_ids=random_reaction_emoji_ids,
            random_repeat_enabled=random_repeat_enabled,
            random_repeat_probability=random_repeat_probability,
            random_repeat_probability_by_group=random_repeat_probability_by_group,
            random_repeat_cooldown_seconds=random_repeat_cooldown_seconds,
            random_repeat_cooldown_seconds_by_group=random_repeat_cooldown_seconds_by_group,
            random_repeat_message_interval=random_repeat_message_interval,
            random_repeat_message_interval_by_group=random_repeat_message_interval_by_group,
            random_triple_repeat_probability=random_triple_repeat_probability,
            random_triple_repeat_enabled_by_group=random_triple_repeat_enabled_by_group,
            random_triple_repeat_probability_by_group=random_triple_repeat_probability_by_group,
            feedback_notification_interval_seconds=integer(
                "FEEDBACK_NOTIFICATION_INTERVAL_SECONDS", 600, 60, 86400
            ),
            hourly_announcement_enabled=boolean("HOURLY_ANNOUNCEMENT_ENABLED", False),
            hourly_announcement_group_ids=hourly_groups,
            hourly_announcement_start_minute=hourly_start_minute,
            hourly_announcement_end_minute=hourly_end_minute,
            hourly_announcement_max_attempts=integer(
                "HOURLY_ANNOUNCEMENT_MAX_ATTEMPTS", 3, 1, 5
            ),
            guess_cursed_numbers=cursed_guess_numbers,
            zhijiang_live_guard_enabled=boolean("ZHIJIANG_LIVE_GUARD_ENABLED", True),
            zhijiang_schedule_url=zhijiang_schedule_url,
            zhijiang_schedule_refresh_minutes=integer(
                "ZHIJIANG_SCHEDULE_REFRESH_MINUTES", 5, 1, 1440
            ),
            zhijiang_live_pause_minutes=integer("ZHIJIANG_LIVE_PAUSE_MINUTES", 60, 1, 720),
            zhijiang_schedule_lookahead_days=integer(
                "ZHIJIANG_SCHEDULE_LOOKAHEAD_DAYS", 7, 1, 14
            ),
            zhijiang_schedule_timeout=float_value("ZHIJIANG_SCHEDULE_TIMEOUT", 15),
            asoul_bili_enabled=boolean("ASOUL_BILI_ENABLED", False),
            asoul_bili_poll_interval_seconds=integer(
                "ASOUL_BILI_POLL_INTERVAL_SECONDS", 300, 60, 3600
            ),
            asoul_bili_group_ids=asoul_bili_group_ids,
            asoul_bili_push_a_coast=asoul_bili_push_a_coast,
            asoul_bili_a_coast_group_ids=asoul_bili_a_coast_group_ids,
            asoul_bili_target_uids=asoul_bili_target_uids,
            asoul_bili_comment_target_uids=asoul_bili_comment_target_uids,
            asoul_bili_push_dynamic=boolean("ASOUL_BILI_PUSH_DYNAMIC", True),
            asoul_bili_push_video=boolean("ASOUL_BILI_PUSH_VIDEO", True),
            asoul_bili_push_live=boolean("ASOUL_BILI_PUSH_LIVE", True),
            asoul_bili_push_comment=boolean("ASOUL_BILI_PUSH_COMMENT", False),
            asoul_bili_render_cards=boolean("ASOUL_BILI_RENDER_CARDS", True),
        )

    @property
    def asoul_bili_effective_group_ids(self) -> tuple[int, ...]:
        groups = self.asoul_bili_group_ids
        if self.asoul_bili_push_a_coast:
            groups += self.asoul_bili_a_coast_group_ids
        return tuple(dict.fromkeys(groups))

    def managed_order(self, group_ids: Iterable[int]) -> tuple[int, ...]:
        """Order a group subset by their position in MANAGED_GROUP_IDS."""
        return managed_group_order(group_ids, self.managed_group_ids)


settings = Settings.from_env()
