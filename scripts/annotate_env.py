from __future__ import annotations

import argparse
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
EXAMPLE_PATH = ROOT / ".env.example"
KEY_RE = re.compile(r"^\s*(?P<key>[A-Za-z0-9_]+)\s*=")
SECTION_RE = re.compile(r"^#\s*[=#\-\s]*$|^#\s*\d+、")

STALE_MARKERS = (
    "AI_ENABLED",
    "AI_MEMORY",
    "AI_API",
    "AI_MODEL",
    "AI 群聊",
    "AI群聊",
    "唯一支持直接编辑 .env 后自动热加载",
    "TANGTANG_MEMORY_ENABLED",
)

# Curated descriptions for keys whose .env.example comment is missing/insufficient.
OVERRIDES = {
    "TANGTANG_GROUP_CONTEXT_MESSAGES": "每次呼叫注入提示词的最近群聊气氛条数（1-100，默认 30）",
    "TANGTANG_TOOL_LOOP_MAX": "Agent 工具调用最大循环轮数（1-8，默认 3）",
    "PROFILE_ENABLED": "发言画像 AI 总开关；缺省沿用 TANGTANG_ENABLED",
    "PROFILE_API_URL": "画像 API 地址；缺省沿用 TANGTANG_API_URL",
    "PROFILE_API_KEY": "画像 API 密钥；缺省沿用 TANGTANG_API_KEY（只保存在本机）",
    "PROFILE_API_STYLE": "画像接口风格：responses 或 chat_completions；缺省沿用 TANGTANG_API_STYLE",
    "PROFILE_MODEL": "画像模型；缺省沿用 TANGTANG_MODEL",
    "PROFILE_REASONING_EFFORT": "画像思考等级：none/low/high/max；缺省沿用 TANGTANG_REASONING_EFFORT",
    "PROFILE_TIMEOUT_SECONDS": "画像请求超时秒数（1-600）",
    "PROFILE_MAX_INPUT_CHARS": "画像单次输入字符上限（1000-64000）",
    "PROFILE_MAX_OUTPUT_TOKENS": "画像单次输出 token 硬上限（16-24000）",
    "PROFILE_MAX_RESPONSE_CHARS": "画像回复字符截断上限（40-24000）",
    "PROFILE_CHUNK_CHARS": "画像发言分块大小，每块字符数（1000-64000）",
    "PROFILE_MAX_RECORDS_PER_RUN": "画像单次处理的最大未消费发言条数（100-500000）",
    "PROFILE_MAX_CONCURRENT": "画像任务全局并发数（1-16，默认 3；超过部分排队等待）",
    "PROFILE_RETRY_MAX_ATTEMPTS": "画像请求失败重试次数上限（1-10，默认 3；仅对 429/5xx 退避重试）",
    "PROFILE_RETRY_BASE_SECONDS": "画像重试等待基数秒数（0-60，默认 2；指数退避，尊重 Retry-After）",
    "PROFILE_MERGE_CHUNK_CHARS": "画像证据合并专用分块大小（1000-64000，默认 12000）",
    "PROFILE_EVIDENCE_CONCURRENCY": "画像证据提取与合并阶段任务内并发路数（1-16，默认 5）",
    "PROFILE_EVIDENCE_REASONING_EFFORT": "画像证据/合并阶段思考等级：none/low/high/max（默认 low）",
    "PROFILE_EVIDENCE_MAX_CHARS": "画像证据/合并阶段的软性字数上限（100-12000）",
    "PROFILE_EVIDENCE_MAX_TOKENS": "画像证据/合并阶段的输出 token 硬上限（64-24000）",
    "PROFILE_EVIDENCE_MAX_RESPONSE_CHARS": "画像证据/合并阶段回复字符截断上限（200-24000）",
    "PROFILE_FINAL_MAX_CHARS": "最终画像软性字数上限（100-8000）",
    "PROFILE_FINAL_MAX_TOKENS": "最终画像输出 token 硬上限（64-24000）",
    "PROFILE_FINAL_MAX_RESPONSE_CHARS": "最终画像回复字符截断上限（200-24000）",
    "BOT_ROLLUP_MINUTE": "每日发言汇总运行分钟（0-59）",
    "BOT_RESPONSE_DELAY_MAX_SECONDS": "普通互动发送前随机延迟上限（秒，须不小于最小值）",
    "BOT_COMMAND_RESPONSE_DELAY_MAX_SECONDS": "# 指令和小游戏反馈发送前随机延迟上限（秒，须不小于最小值）",
    "PORT": "NoneBot 监听端口（OneBot 模式）",
    "CODEX_COMPLETION_NOTIFY_GROUP_ID": "Codex 任务完成通知群（须属于 MANAGED_GROUP_IDS）",
    "CODEX_COMPLETION_NOTIFY_SUPER_ADMIN_ID": "Codex 完成通知接收的超管 QQ（须属于 BOT_OPERATOR_IDS）",
    "CODEX_COMPLETION_NOTIFY_TOKEN": "Codex 完成通知鉴权令牌（或使用 ONEBOT_ACCESS_TOKEN）",
    "ASOUL_BILI_GROUP_IDS": "B站推送群的首次迁移种子（运行时以 SQLite 为准）",
    "GLOBAL_ANNOUNCEMENT_DEFAULT_CLUSTER": "内部公告接口默认使用的 SQLite 集群名称或别名",
    "WUWA_IMPORT_CLUSTER_NAME": "已停用的历史导入参数；项目不再写入上游鸣潮数据",
    "ASOUL_BILI_POLL_INTERVAL_SECONDS": "B站轮询间隔秒数（60-3600）",
    "ASOUL_BILI_PUSH_DYNAMIC": "是否推送 B站动态",
    "ASOUL_BILI_PUSH_LIVE": "是否推送 B站开播",
    "ASOUL_BILI_PUSH_COMMENT": "是否推送 B站评论（当前默认关闭）",
    "GSUID_CORE_PORT": "GsUID Core 本地监听端口",
    "GSUID_CORE_HOST": "GsUID Core 本地地址",
    "TANGTANG_GROUP_IDS": "糖糖生效群的迁移种子（群数量不设上限，运行时以数据库登记为准）",
    "TANGTANG_IGNORE_PROBABILITY": "呼叫后不回复的概率（0-1）",
    "TANGTANG_C_PROBABILITY": "C 模式闲聊接话概率（0-1）",
    "TANGTANG_API_URL": "糖糖 API 地址",
    "TANGTANG_API_KEY": "糖糖 API 密钥（只保存在本机，不写入日志）",
    "TANGTANG_API_STYLE": "糖糖接口风格：responses 或 chat_completions",
    "TANGTANG_MODEL": "糖糖使用的模型名",
    "HOURLY_ANNOUNCEMENT_GROUP_IDS": "整点报时初始群范围；运行时用 #整点报时 范围 热更并写回本键",
}


def example_comments() -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    pending: list[str] = []
    for raw in EXAMPLE_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if not line.strip():
            pending = []
            continue
        if line.lstrip().startswith("#"):
            comment = line.lstrip()[1:].strip()
            if comment and not SECTION_RE.match(line):
                pending.append(comment)
            continue
        match = KEY_RE.match(line)
        if match:
            comments = list(pending)
            if comments:
                result[match.group("key").upper()] = comments
            pending = []
    return result


def build_comment_map() -> dict[str, list[str]]:
    result = example_comments()
    for key, comment in OVERRIDES.items():
        result[key] = [comment]
    return result


def is_stale_comment(text: str) -> bool:
    return any(marker in text for marker in STALE_MARKERS)


def main() -> int:
    parser = argparse.ArgumentParser(description="Annotate .env keys with comments")
    parser.add_argument("--apply", action="store_true", help="write the annotated .env")
    args = parser.parse_args()

    comment_map = build_comment_map()
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    output: list[str] = []
    missing: list[str] = []
    added: list[str] = []
    removed: list[str] = []
    previous_was_comment = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            if is_stale_comment(line):
                removed.append(line.lstrip("#").strip())
                previous_was_comment = False
                continue
            output.append(line)
            previous_was_comment = True
            continue
        match = KEY_RE.match(line)
        if match:
            key = match.group("key").upper()
            if not previous_was_comment:
                comments = comment_map.get(key)
                if comments:
                    output.extend(f"# {comment}" for comment in comments)
                    added.append(key)
                else:
                    missing.append(key)
            output.append(line)
            previous_was_comment = False
            continue
        output.append(line)
        previous_was_comment = False

    print("keys with comments added:", ", ".join(added) or "(none)")
    print("stale comments removed:", ", ".join(removed) or "(none)")
    print("keys still missing comments:", ", ".join(missing) or "(none)")

    if not args.apply:
        print("dry-run only; use --apply to write")
        return 0
    if missing:
        print("aborting: fill missing comments first")
        return 1

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = ROOT / "data" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ENV_PATH, backup_dir / f".env.annotate-{stamp}.bak")
    fd, temp_name = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=str(ROOT))
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
        for item in output:
            fh.write(item.rstrip("\r\n") + "\n")
    os.replace(temp_name, ENV_PATH)
    print(f"annotated .env written; backup at {backup_dir / f'.env.annotate-{stamp}.bak'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
