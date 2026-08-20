from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx
from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MESSAGE = "Codex 已执行完成。"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Notify the configured QQ super admin that a Codex task is complete."
    )
    parser.add_argument("message", nargs="?", default=DEFAULT_MESSAGE)
    details_group = parser.add_mutually_exclusive_group()
    details_group.add_argument(
        "--details",
        help="Final user-facing result to place in a folded QQ forward message.",
    )
    details_group.add_argument(
        "--details-file",
        type=Path,
        help="UTF-8 file containing the final user-facing result.",
    )
    args = parser.parse_args()

    values = dotenv_values(ROOT / ".env")
    enabled = str(values.get("CODEX_COMPLETION_NOTIFY_ENABLED", "false")).strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        print("Codex completion notification is disabled.", file=sys.stderr)
        return 2
    token = str(
        values.get("CODEX_COMPLETION_NOTIFY_TOKEN") or values.get("ONEBOT_ACCESS_TOKEN") or ""
    ).strip()
    if not token:
        print("A Codex completion notification token is required.", file=sys.stderr)
        return 2
    host = str(values.get("HOST") or "127.0.0.1").strip()
    port = str(values.get("PORT") or "8080").strip()
    url = f"http://{host}:{port}/internal/codex/completion"
    try:
        details = (
            args.details_file.read_text(encoding="utf-8")
            if args.details_file is not None
            else args.details
        )
    except OSError as exc:
        print(f"Codex completion details could not be read: {exc}", file=sys.stderr)
        return 2
    payload = {"message": args.message}
    if details is not None:
        payload["details"] = details
    try:
        response = httpx.post(
            url,
            headers={"X-Codex-Completion-Token": token},
            json=payload,
            timeout=20.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        print(f"Codex completion notification failed: {exc}", file=sys.stderr)
        return 1
    print("Codex completion notification delivered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
