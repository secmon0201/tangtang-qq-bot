"""Render a local-only today-wife graph containing every current test-group member."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values

from bot.config import ROOT, settings
from bot.services.avatars import AvatarService
from bot.services.mini_game_reports import MiniGameReportRenderer
from bot.services.runtime import database
from bot.services.today_wife import TodayWifeService


OUTPUT_DIR = ROOT / "data" / "reports" / "today_wife_cases"
OUTPUT_PATH = OUTPUT_DIR / "09_测试群全员缘分.png"


def _member_endpoint() -> tuple[str, str]:
    values = dotenv_values(ROOT / ".env")
    enabled = str(values.get("CODEX_COMPLETION_NOTIFY_ENABLED", "false")).strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        raise RuntimeError("test-group notifications are disabled")
    token = str(
        values.get("CODEX_COMPLETION_NOTIFY_TOKEN") or values.get("ONEBOT_ACCESS_TOKEN") or ""
    ).strip()
    if not token:
        raise RuntimeError("a test-group notification token is required")
    host = str(values.get("HOST") or "127.0.0.1").strip()
    port = str(values.get("PORT") or "8080").strip()
    return f"http://{host}:{port}/internal/codex/test-group-members", token


def fetch_members() -> list[dict[str, int | str]]:
    url, token = _member_endpoint()
    try:
        response = httpx.post(url, headers={"X-Codex-Completion-Token": token}, timeout=20.0)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise RuntimeError(f"test-group member lookup failed: {exc}") from exc
    payload: Any = response.json()
    raw_members = payload.get("members") if isinstance(payload, dict) else None
    if not isinstance(raw_members, list):
        raise RuntimeError("test-group member lookup returned an invalid response")
    members: list[dict[str, int | str]] = []
    for item in raw_members:
        if not isinstance(item, dict):
            continue
        try:
            user_id = int(item.get("user_id") or 0)
        except (TypeError, ValueError):
            continue
        nickname = str(item.get("nickname") or "群友").strip()[:40] or "群友"
        if user_id > 0:
            members.append({"user_id": user_id, "nickname": nickname})
    if not members:
        raise RuntimeError("the test group has no renderable members")
    return members


def build_case_rows(members: list[dict[str, int | str]]) -> list[dict[str, int | str]]:
    """Give every member one deterministic edge with mutual and collision examples."""
    rows: list[dict[str, int | str]] = []
    for offset in range(0, len(members), 8):
        cluster = members[offset : offset + 8]
        if len(cluster) == 1:
            actor = cluster[0]
            rows.append({
                "actor_id": actor["user_id"], "actor_nickname": actor["nickname"],
                "target_id": actor["user_id"], "target_nickname": actor["nickname"],
            })
            continue
        for index, actor in enumerate(cluster):
            if index == 0:
                target = cluster[1]
            elif index == 1:
                target = cluster[0]
            elif index in {2, 6}:
                target = cluster[1 if index == 2 else 4 if len(cluster) > 4 else 0]
            else:
                target = cluster[index - 1]
            rows.append({
                "actor_id": actor["user_id"], "actor_nickname": actor["nickname"],
                "target_id": target["user_id"], "target_nickname": target["nickname"],
            })
    return rows


async def main() -> None:
    members = fetch_members()
    rows = build_case_rows(members)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    values = dotenv_values(ROOT / ".env")
    avatar_service = AvatarService(
        OUTPUT_DIR / "avatar_cache",
        str(values.get("AVATAR_BASE_URL") or "https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640"),
        timeout=float(values.get("AVATAR_TIMEOUT") or 5),
        cache_ttl=int(values.get("AVATAR_CACHE_TTL") or 604800),
    )
    avatars = await avatar_service.prefetch(members)
    renderer = MiniGameReportRenderer(OUTPUT_DIR, retention_hours=720)
    wife_service = TodayWifeService(database(), settings.timezone)
    group_id = int(settings.codex_completion_notify_group_id or 0)
    day = datetime.now().date().isoformat()
    path = renderer.render_group_today_wife(
        rows,
        avatars,
        day,
        wife_service.episode(group_id, day),
        wife_service.group_spotlight(rows, group_id, day),
    )
    path.replace(OUTPUT_PATH)
    print(f"members={len(members)} edges={len(rows)} image={OUTPUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    asyncio.run(main())
