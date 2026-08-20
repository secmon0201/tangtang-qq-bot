from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx
from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parent.parent
KINDS = ("test-case", "completion", "notice")
KIND_LABELS = {"test-case": "测试用例", "completion": "完成通知", "notice": "通知"}
MAX_TITLE_LENGTH = 120
MAX_BODY_LENGTH = 24_000
MAX_IMAGE_COUNT = 9
MAX_IMAGE_BYTES = 20 * 1024 * 1024
ALLOWED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send a text or local-image notification to the configured QQ test group."
    )
    parser.add_argument("--kind", choices=KINDS, default="notice")
    parser.add_argument("--title", required=True)
    parser.add_argument("--body", default="")
    parser.add_argument("--image", action="append", type=Path, default=[])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.title.strip() or len(args.title.strip()) > MAX_TITLE_LENGTH:
        print(f"Title must contain 1 to {MAX_TITLE_LENGTH} characters.", file=sys.stderr)
        return 2
    heading_length = len(KIND_LABELS[args.kind]) + len(args.title.strip()) + 8
    if len(args.body.strip()) + heading_length > MAX_BODY_LENGTH:
        print(f"Body must contain at most {MAX_BODY_LENGTH} characters.", file=sys.stderr)
        return 2
    if len(args.image) > MAX_IMAGE_COUNT:
        print(f"At most {MAX_IMAGE_COUNT} images may be attached.", file=sys.stderr)
        return 2

    image_paths: list[str] = []
    for path in args.image:
        resolved = path.resolve()
        try:
            resolved.relative_to(ROOT.resolve())
        except ValueError:
            print("Images must be located inside the bot workspace.", file=sys.stderr)
            return 2
        if resolved.suffix.lower() not in ALLOWED_IMAGE_SUFFIXES:
            print("Images must be PNG, JPG, JPEG, GIF, or WebP files.", file=sys.stderr)
            return 2
        if not resolved.is_file():
            print(f"Image does not exist: {resolved}", file=sys.stderr)
            return 2
        if resolved.stat().st_size > MAX_IMAGE_BYTES:
            print("Each image must not exceed 20 MiB.", file=sys.stderr)
            return 2
        image_paths.append(str(resolved))

    if not args.body.strip() and not image_paths:
        print("Provide a message body or at least one image.", file=sys.stderr)
        return 2

    payload = {
        "kind": args.kind,
        "title": args.title,
        "body": args.body,
        "image_paths": image_paths,
    }
    if args.dry_run:
        print("Test-group notification is valid and ready to send.")
        return 0

    values = dotenv_values(ROOT / ".env")
    enabled = str(values.get("CODEX_COMPLETION_NOTIFY_ENABLED", "false")).strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        print("Test-group notifications are disabled.", file=sys.stderr)
        return 2
    token = str(
        values.get("CODEX_COMPLETION_NOTIFY_TOKEN") or values.get("ONEBOT_ACCESS_TOKEN") or ""
    ).strip()
    if not token:
        print("A test-group notification token is required.", file=sys.stderr)
        return 2
    host = str(values.get("HOST") or "127.0.0.1").strip()
    port = str(values.get("PORT") or "8080").strip()
    url = f"http://{host}:{port}/internal/codex/test-group-notice"
    try:
        response = httpx.post(
            url,
            headers={"X-Codex-Completion-Token": token},
            json=payload,
            timeout=20.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        print(f"Test-group notification failed: {exc}", file=sys.stderr)
        return 1
    print("Test-group notification delivered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
