from __future__ import annotations

import argparse
import re
import shutil
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ASSIGNMENT_RE = re.compile(r"^\s*([A-Za-z0-9_]+)\s*=")
SECTION_RE = re.compile(r"^\s*#\s*(?:={3,}|[一二三四五六七八九十]+、)")

SECTIONS = (
    (
        "一、基础身份与启动方式",
        "修改本板块后需要重启 NoneBot。不要在 .env 中填写 QQ 密码、验证码或 Cookie。",
        (
            "BOT_TRANSPORT",
            "QQ_PLATFORM_TRANSPORT",
            "QQ_ACCOUNT_ID",
            "SNOWLUMA_DIR",
            "SNOWLUMA_WEBUI_PORT",
            "LAGRANGE_DIR",
            "BOT_COMMAND_PREFIX",
            "BOT_TIMEZONE",
            "BOT_ROLLUP_HOUR",
            "BOT_ROLLUP_MINUTE",
            "BOT_REQUIRE_MENTION",
            "LOG_LEVEL",
        ),
    ),
    (
        "二、权限与公告名单",
        "超级管理员和公告专用名单在此维护；名单成员不会取得其他超级管理员权限。",
        (
            "BOT_OPERATOR_IDS",
            "GLOBAL_ANNOUNCEMENT_OPERATOR_IDS",
        ),
    ),
    (
        "三、受管群与功能范围",
        "群范围必须属于 MANAGED_GROUP_IDS。可由机器人命令热更的范围仍以 SQLite 当前值优先。",
        (
            "MANAGED_GROUP_IDS",
            "DUPLICATE_GROUP_IDS",
            "GAME_GROUP_IDS",
            "GAME_API_GROUP_IDS",
            "HOURLY_ANNOUNCEMENT_GROUP_IDS",
        ),
    ),
    (
        "四、OneBot 与本地服务",
        "NoneBot 的 HTTP/WebSocket 服务和 OneBot 连接维护参数。",
        (
            "HOST",
            "PORT",
            "ONEBOT_ACCESS_TOKEN",
            "QQ_TRANSPORT_MAINTENANCE_ENABLED",
            "QQ_TRANSPORT_MAINTENANCE_INTERVAL_SECONDS",
            "BOT_ONEBOT_API_MIN_INTERVAL_SECONDS",
        ),
    ),
    (
        "五、数据、报告与头像",
        "本地 SQLite、图片报告与头像缓存路径和保留策略。",
        (
            "BOT_DB_PATH",
            "REPORT_OUTPUT_MODE",
            "REPORT_DIR",
            "REPORT_FONT_PATH",
            "REPORT_RETENTION_HOURS",
            "AVATAR_BASE_URL",
            "AVATAR_CACHE_DIR",
            "AVATAR_TIMEOUT",
            "AVATAR_CACHE_TTL",
            "AVATAR_REFRESH_INTERVAL_SECONDS",
            "AVATAR_REFRESH_COOLDOWN_SECONDS",
            "AVATAR_REFRESH_MAX_PER_CALL",
            "AVATAR_REFRESH_CONCURRENCY",
        ),
    ),
    (
        "六、基础响应与被动互动",
        "普通响应延迟、查重节流、随机表情和复读的开关、概率与候选列表。",
        (
            "BOT_RESPONSE_DELAY_MIN_SECONDS",
            "BOT_RESPONSE_DELAY_MAX_SECONDS",
            "BOT_COMMAND_RESPONSE_DELAY_MIN_SECONDS",
            "BOT_COMMAND_RESPONSE_DELAY_MAX_SECONDS",
            "DUPLICATE_SCAN_COOLDOWN_SECONDS",
            "BOT_MENTION_ACK_EMOJI_IDS",
            "BOT_RANDOM_REACTION_ENABLED",
            "BOT_RANDOM_REACTION_GROUP_IDS",
            "BOT_RANDOM_REACTION_PROBABILITY",
            "BOT_RANDOM_REACTION_COOLDOWN_SECONDS",
            "BOT_RANDOM_REACTION_EMOJI_IDS",
            "BOT_RANDOM_REPEAT_ENABLED",
            "BOT_RANDOM_REPEAT_PROBABILITY",
            "BOT_RANDOM_REPEAT_COOLDOWN_SECONDS",
            "BOT_RANDOM_REPEAT_MESSAGE_INTERVAL",
            "BOT_RANDOM_TRIPLE_REPEAT_ENABLED",
            "BOT_RANDOM_TRIPLE_REPEAT_PROBABILITY",
        ),
    ),
    (
        "七、小游戏与整点报时",
        "小游戏和整点报时的业务参数。",
        (
            "MINI_GAME_GUESS_CURSED_NUMBERS",
            "HOURLY_ANNOUNCEMENT_ENABLED",
            "HOURLY_ANNOUNCEMENT_START",
            "HOURLY_ANNOUNCEMENT_END",
            "HOURLY_ANNOUNCEMENT_MAX_ATTEMPTS",
        ),
    ),
    (
        "八、游戏接口与 A-SOUL 数据",
        "NTE/GsUID 本地接口、发言统计及 A-SOUL B 站播报设置。",
        (
            "GAME_API_ENABLED",
            "GSUID_ENABLED",
            "GSUID_CORE_DIR",
            "GSUID_CORE_HOST",
            "GSUID_CORE_PORT",
            "GSUID_CORE_WS_TOKEN",
            "GSUID_CORE_BOTID",
            "STATS_REALTIME_ENABLED",
            "A_COAST_PROFILE_ENABLED",
            "ASOUL_BILI_ENABLED",
            "ASOUL_BILI_GROUP_IDS",
            "ASOUL_BILI_PUSH_A_COAST",
            "ASOUL_BILI_A_COAST_GROUP_IDS",
            "ASOUL_BILI_POLL_INTERVAL_SECONDS",
            "ASOUL_BILI_PUSH_DYNAMIC",
            "ASOUL_BILI_PUSH_VIDEO",
            "ASOUL_BILI_PUSH_LIVE",
            "ASOUL_BILI_PUSH_COMMENT",
        ),
    ),
    (
        "九、糖糖聊天与主动互动",
        "TANGTANG_* 由模块热加载；接口密钥只保存在本机。",
        (
            "TANGTANG_ENABLED",
            "TANGTANG_MODE",
            "TANGTANG_CALL_KEYWORD",
            "TANGTANG_GROUP_IDS",
            "TANGTANG_IGNORE_PROBABILITY",
            "TANGTANG_CALL_IGNORE_PROBABILITY",
            "TANGTANG_REQUIRED_CALL_REPLY_GROUP_IDS",
            "TANGTANG_SOFT_BLACKLIST_IGNORE_PROBABILITY",
            "TANGTANG_C_PROBABILITY",
            "TANGTANG_API_URL",
            "TANGTANG_API_KEY",
            "TANGTANG_API_STYLE",
            "TANGTANG_MODEL",
            "TANGTANG_REASONING_EFFORT",
            "TANGTANG_TIMEOUT_SECONDS",
            "TANGTANG_MAX_INPUT_CHARS",
            "TANGTANG_MAX_OUTPUT_TOKENS",
            "TANGTANG_MAX_RESPONSE_CHARS",
            "TANGTANG_HISTORY_MESSAGES",
            "TANGTANG_HISTORY_CHARS",
            "TANGTANG_GROUP_CONTEXT_MESSAGES",
            "TANGTANG_TOOL_ENABLED",
            "TANGTANG_TOOL_LOOP_MAX",
            "TANGTANG_PROACTIVE_ENABLED",
            "TANGTANG_PROACTIVE_PROBABILITY",
            "TANGTANG_PROACTIVE_COOLDOWN_SECONDS",
            "TANGTANG_PROACTIVE_MESSAGE_INTERVAL",
        ),
    ),
    (
        "十、发言画像",
        "PROFILE_* 可独立于糖糖配置；缺省项会按模块规则继承糖糖设置。",
        (
            "PROFILE_ENABLED",
            "PROFILE_API_URL",
            "PROFILE_API_KEY",
            "PROFILE_API_STYLE",
            "PROFILE_MODEL",
            "PROFILE_REASONING_EFFORT",
            "PROFILE_TIMEOUT_SECONDS",
            "PROFILE_MAX_INPUT_CHARS",
            "PROFILE_MAX_OUTPUT_TOKENS",
            "PROFILE_MAX_RESPONSE_CHARS",
            "PROFILE_CHUNK_CHARS",
            "PROFILE_MAX_RECORDS_PER_RUN",
            "PROFILE_MAX_CONCURRENT",
            "PROFILE_RETRY_MAX_ATTEMPTS",
            "PROFILE_RETRY_BASE_SECONDS",
            "PROFILE_MERGE_CHUNK_CHARS",
            "PROFILE_EVIDENCE_CONCURRENCY",
            "PROFILE_EVIDENCE_REASONING_EFFORT",
            "PROFILE_EVIDENCE_MAX_CHARS",
            "PROFILE_EVIDENCE_MAX_TOKENS",
            "PROFILE_EVIDENCE_MAX_RESPONSE_CHARS",
            "PROFILE_FINAL_MAX_CHARS",
            "PROFILE_FINAL_MAX_TOKENS",
            "PROFILE_FINAL_MAX_RESPONSE_CHARS",
        ),
    ),
    (
        "十一、Codex 本地运维通知",
        "仅固定测试群、固定超级管理员和本机调用方可使用。",
        (
            "CODEX_COMPLETION_NOTIFY_ENABLED",
            "CODEX_COMPLETION_NOTIFY_GROUP_ID",
            "CODEX_COMPLETION_NOTIFY_SUPER_ADMIN_ID",
            "CODEX_COMPLETION_NOTIFY_TOKEN",
            "CODEX_WORKER_ENABLED",
            "CODEX_WORKER_COMMAND",
            "CODEX_WORKER_POLL_SECONDS",
            "CODEX_WORKER_TIMEOUT_SECONDS",
            "CODEX_WORKER_SANDBOX",
        ),
    ),
)


def key_for(block: list[str]) -> str | None:
    for line in block:
        match = ASSIGNMENT_RE.match(line)
        if match:
            return match.group(1).upper()
    return None


def clean_block(block: list[str]) -> list[str]:
    return [line for line in block if not SECTION_RE.match(line)]


def read_blocks(path: Path) -> tuple[list[str], dict[str, list[str]]]:
    preamble: list[str] = []
    current: list[str] = []
    pending: list[str] = []
    blocks: dict[str, list[str]] = {}
    started = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if ASSIGNMENT_RE.match(line):
            if current:
                key = key_for(current)
                if key:
                    blocks[key] = clean_block(current)
            current = [*pending, line]
            pending = []
            started = True
        elif started:
            pending.append(line)
        else:
            preamble.append(line)
    if current:
        current.extend(pending)
        key = key_for(current)
        if key:
            blocks[key] = clean_block(current)
    return [line for line in preamble if not SECTION_RE.match(line)], blocks


def default_global_announcement_block() -> list[str]:
    return [
        "# 全局公告专用名单：多个 QQ 号用英文逗号分隔。名单成员仅可使用全局公告和图片公告，不获得其他超级管理员权限。",
        "GLOBAL_ANNOUNCEMENT_OPERATOR_IDS=",
    ]


def organize(path: Path, backup: bool) -> tuple[int, list[str]]:
    preamble, blocks = read_blocks(path)
    blocks.setdefault("GLOBAL_ANNOUNCEMENT_OPERATOR_IDS", default_global_announcement_block())
    output = [line for line in preamble if line.strip()]
    used: set[str] = set()
    for title, description, keys in SECTIONS:
        selected = [key for key in keys if key in blocks]
        if not selected:
            continue
        output.extend(("", "# " + "=" * 76, f"# {title}", "# " + "=" * 76, "# " + description))
        for key in selected:
            output.extend(blocks[key])
            if output[-1] != "":
                output.append("")
            used.add(key)
    remaining = sorted(key for key in blocks if key not in used)
    if remaining:
        output.extend(("# " + "=" * 76, "# 十二、未分类兼容参数", "# " + "=" * 76))
        for key in remaining:
            output.extend(blocks[key])
            if output[-1] != "":
                output.append("")
    text = "\n".join(output).rstrip() + "\n"
    if backup:
        backup_dir = ROOT / "data" / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(path, backup_dir / f".env.organize-{stamp}.bak")
    path.write_text(text, encoding="utf-8", newline="\n")
    return len(used), remaining


def main() -> int:
    parser = argparse.ArgumentParser(description="Organize a local bot .env file without changing values.")
    parser.add_argument("--env", type=Path, default=ROOT / ".env")
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()
    path = args.env.resolve()
    if not path.is_file():
        raise SystemExit(f"Environment file does not exist: {path}")
    organized, remaining = organize(path, backup=not args.no_backup)
    print(f"Organized {organized} environment keys.")
    if remaining:
        print("Unclassified keys preserved: " + ", ".join(remaining))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
