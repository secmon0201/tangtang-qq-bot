from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx
from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parent.parent
MAX_TEXT_LENGTH = 1000


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send one image-form global announcement to every A-Coast group."
    )
    parser.add_argument("text", nargs="?", default=None)
    parser.add_argument(
        "--image",
        type=Path,
        default=None,
        help="Send one existing image from inside the bot workspace without re-rendering it.",
    )
    parser.add_argument(
        "--sticker",
        default=None,
        help="Optional member name whose sticker pack should decorate the poster.",
    )
    parser.add_argument(
        "--sticker-name",
        default=None,
        help="Optional exact sticker name inside the member pack (e.g. 哭哭).",
    )
    parser.add_argument(
        "--at-all",
        action="store_true",
        help="Mention all members when delivering (default is no mention).",
    )
    args = parser.parse_args()
    text = args.text.strip() if args.text else ""
    if bool(text) == bool(args.image):
        print("Provide exactly one announcement text or --image path.", file=sys.stderr)
        return 2
    if len(text) > MAX_TEXT_LENGTH:
        print(f"Announcement text must not exceed {MAX_TEXT_LENGTH} characters.", file=sys.stderr)
        return 2

    values = dotenv_values(ROOT / ".env")
    enabled = str(values.get("CODEX_COMPLETION_NOTIFY_ENABLED", "false")).strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        print("Global announcement notifications are disabled.", file=sys.stderr)
        return 2
    token = str(
        values.get("CODEX_COMPLETION_NOTIFY_TOKEN") or values.get("ONEBOT_ACCESS_TOKEN") or ""
    ).strip()
    if not token:
        print("A global announcement token is required.", file=sys.stderr)
        return 2
    host = str(values.get("HOST") or "127.0.0.1").strip()
    port = str(values.get("PORT") or "8080").strip()
    url = f"http://{host}:{port}/internal/codex/global-announcement"
    payload = {"image_path": str(args.image.resolve())} if args.image else {"text": text}
    if args.sticker and args.sticker.strip():
        payload["sticker"] = args.sticker.strip()
    if args.sticker_name and args.sticker_name.strip():
        payload["sticker_name"] = args.sticker_name.strip()
    if args.at_all:
        payload["at_all"] = True
    try:
        response = httpx.post(
            url,
            headers={"X-Codex-Completion-Token": token},
            json=payload,
            timeout=60.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        print(f"Global announcement failed: {exc}", file=sys.stderr)
        return 1
    payload = response.json()
    print(
        f"Global announcement delivered to {payload.get('sent', 0)} groups; "
        f"{payload.get('failed', 0)} failed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
