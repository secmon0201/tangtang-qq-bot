"""Read migrated Bilibili credentials and probe real read-only endpoints.

No QR login, poll cursor update, QQ send, or instance settings write occurs.
The report contains aggregate results, never credential values or account IDs.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import time

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tangtang_harness.business.asoul import ASoulService, CALENDAR_URL, MONITOR_KEY
from bilibili_api import user


class ReadOnlyBusiness:
    def __init__(self, path: Path):
        self.path = path

    def asoul_state(self, key: str, default=None):
        with sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
            row = conn.execute("SELECT state_value FROM asoul_plugin_state WHERE state_key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default


async def probe(root: Path) -> dict:
    service = ASoulService(ReadOnlyBusiness(root / "runtime" / "business.db"))
    credential = service._credential()
    with sqlite3.connect((root / "data" / "harness.db").resolve().as_uri() + "?mode=ro", uri=True) as conn:
        rows = conn.execute("SELECT key,value FROM settings WHERE key IN ('bili_target_uids','bili_comment_target_uids')").fetchall()
    configuration = {key: json.loads(value) for key, value in rows}
    monitored = service.db.asoul_state(MONITOR_KEY, {})
    configured = configuration.get("bili_target_uids", [])
    targets = tuple(str(item) for item in configured) or tuple(key for key in monitored if key.isdigit())
    results = {"checked_at": datetime.now(timezone.utc).isoformat(), "credential_present": credential is not None,
               "configured_target_count": len(configured), "imported_monitor_target_count": len(targets),
               "network": {}}

    async def collect(name: str, operation):
        start = time.perf_counter()
        try:
            value = await asyncio.wait_for(operation, timeout=30)
            results["network"][name] = {"status": "ok", "elapsed_seconds": round(time.perf_counter() - start, 3), **value}
        except Exception as exc:
            results["network"][name] = {"status": "failed", "error_type": type(exc).__name__,
                "api_code": getattr(exc, "code", None), "elapsed_seconds": round(time.perf_counter() - start, 3)}

    async def calendar():
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(CALENDAR_URL)
            response.raise_for_status()
        if "BEGIN:VCALENDAR" not in response.text:
            raise ValueError("calendar endpoint did not return ICS")
        raw = service._parse_ics(response.text)
        events = [event for record in raw if (event := service._event_from_ics(record))]
        return {"http_status": response.status_code, "bytes": len(response.content),
                "calendar_records": len(raw), "live_schedule_events": len(events),
                "future_events": sum(event["starts_at"] >= datetime.now(service.timezone) for event in events)}

    async def login():
        if credential is None:
            return {"authenticated": False, "reason": "no_migrated_credential"}
        return {"authenticated": bool(await credential.check_valid())}

    async def followings():
        if credential is None:
            return {"subscription_count": None, "reason": "no_migrated_credential"}
        profile = await user.get_self_info(credential)
        following = await user.User(int(profile["mid"]), credential=credential).get_followings(ps=20)
        return {"subscription_count": int(following.get("total", 0)),
                "returned_subscription_count": len(following.get("list", []))}

    async def live():
        if not targets:
            return {"requested_targets": 0, "returned_rooms": 0}
        states = await service.fetch_live_statuses(targets)
        return {"requested_targets": len(targets), "returned_rooms": len(states),
                "currently_live": sum(item["live"] == "1" for item in states.values())}

    async def videos():
        if not targets:
            return {"requested_targets": 0, "video_count": 0}
        items = await service.fetch_videos(targets[0])
        return {"requested_targets": 1, "video_count": len(items)}

    async def dynamics():
        if not targets:
            return {"requested_targets": 0, "dynamic_count": 0}
        items = await service.fetch_dynamics(targets[0])
        return {"requested_targets": 1, "dynamic_count": len(items)}

    async def public_dynamics():
        if not targets:
            return {"requested_targets": 0, "dynamic_count": 0}
        payload = await user.User(int(targets[0])).get_dynamics_new()
        return {"requested_targets": 1, "returned_items": len(payload.get('items') or []),
                "has_more": payload.get('has_more')}

    await asyncio.gather(collect("calendar", calendar()), collect("credential", login()),
                         collect("subscriptions", followings()), collect("live", live()),
                         collect("video", videos()), collect("dynamic", dynamics()),
                         collect("dynamic_without_login", public_dynamics()))
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    result = asyncio.run(probe(root))
    path = root / "reports" / "p8-bilibili.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
