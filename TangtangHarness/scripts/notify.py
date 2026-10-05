"""Send engineering notices through the independent Harness notification API."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tangtang_harness.config import load_config
from tangtang_harness.notifications import CompletionPayload, TestGroupPayload
from tangtang_harness.store import Store


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    completion = commands.add_parser("completion", help="完成通知与折叠结果")
    completion.add_argument("message", nargs="?", default="Codex 已执行完成。")
    details = completion.add_mutually_exclusive_group()
    details.add_argument("--details")
    details.add_argument("--details-file", type=Path)
    notice = commands.add_parser("notice", help="文字或图片测试群通知")
    notice.add_argument("--kind", choices=("test-case", "completion", "notice"), default="notice")
    notice.add_argument("--title", required=True)
    body = notice.add_mutually_exclusive_group()
    body.add_argument("--body", default="")
    body.add_argument("--body-file", type=Path)
    notice.add_argument("--image", action="append", type=Path, default=[])
    members = commands.add_parser("members", help="查询固定测试群成员")
    for command in (completion, notice, members):
        command.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        if args.command == "completion":
            payload = CompletionPayload(message=args.message,
                details=args.details_file.read_text(encoding="utf-8") if args.details_file else args.details).model_dump()
            path = "completion"
        elif args.command == "notice":
            payload = TestGroupPayload(kind=args.kind, title=args.title,
                body=args.body_file.read_text(encoding="utf-8") if args.body_file else args.body,
                image_paths=[str(path.resolve()) for path in args.image]).model_dump()
            path = "test-group-notice"
        else:
            payload, path = {}, "test-group-members"
        if args.dry_run:
            print("Harness 通知参数已解析；未调用接口、模型或 QQ。")
            return 0
        config = load_config(root)
        store = Store(root)
        if not store.get_setting("notifications_enabled", False):
            print("Harness 工程通知已关闭。", file=sys.stderr)
            return 2
        env_name = store.get_setting("notification_token_env", "HARNESS_NOTIFICATION_TOKEN")
        token = os.environ.get(env_name) or dotenv_values(root / ".env").get(env_name) or ""
        if not token:
            print("Harness 工程通知 Token 尚未配置。", file=sys.stderr)
            return 2
        host = "127.0.0.1" if config.host == "0.0.0.0" else config.host
        response = httpx.post(f"http://{host}:{config.port}/internal/codex/{path}",
            headers={"X-Codex-Completion-Token": token}, json=payload, timeout=20.0)
        response.raise_for_status()
        print(json.dumps(response.json(), ensure_ascii=False))
        return 0
    except (OSError, ValueError, httpx.HTTPError) as exc:
        print(f"Harness 通知失败：{type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
