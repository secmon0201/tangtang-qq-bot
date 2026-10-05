"""Proactively keep active group context caches warm without sending QQ messages."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.services.tangtang_cache_warmer import AgentCacheWarmer, is_warmup_window_active
from bot.services.tangtang_chat import TangtangService
from bot.services.tangtang_runtime import config_loader


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports"
REPORT_VERSION = "agent-cache-warmer-v1"
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Inspect candidate groups without network requests")
    parser.add_argument("--live", action="store_true", help="Send minimal warmup probes to eligible groups")
    parser.add_argument("--group-id", type=int, help="Warm up a specific group only")
    parser.add_argument("--output", type=Path, default=REPORT_DIR / "agent-cache-warmup.json", help="Path to write JSON report")
    args = parser.parse_args()

    config = config_loader.load()
    now = datetime.now(SHANGHAI_TZ)
    window_active = is_warmup_window_active(now)

    service = TangtangService(db=None)  # Minimal service facade
    warmer = AgentCacheWarmer(service)

    candidates = (args.group_id,) if args.group_id else warmer.get_candidate_groups(config, now)

    payloads = []
    for gid in candidates:
        envelope = warmer.build_warmup_envelope(gid, config)
        payloads.append({
            "group_id": gid,
            "cache_affinity_key": envelope.cache_affinity_key,
            "static_prefix_hash": envelope.static_prefix_hash,
            "layout_version": envelope.layout_version,
        })

    report = {
        "report_version": REPORT_VERSION,
        "timestamp": now.isoformat(),
        "window_active": window_active,
        "candidate_count": len(candidates),
        "mode": "live" if args.live else "dry-run",
        "candidates": payloads,
        "results": [],
    }

    if args.live:
        if not window_active:
            print(f"Warmup window inactive at {now.time()} (active: 09:00 - 23:30). Skipping.")
            return 0
        results = await warmer.warm_all_eligible(config, now=now)
        report["results"] = [
            {
                "group_id": r.group_id,
                "success": r.success,
                "status": r.status,
                "cache_read_tokens": r.cache_read_tokens,
                "non_cached_input_tokens": r.non_cached_input_tokens,
                "total_tokens": r.total_tokens,
                "latency_ms": r.latency_ms,
                "error": r.error,
            }
            for r in results
        ]
        print(f"Warmed {len(results)} groups successfully.")
    else:
        print(f"Dry run: {len(candidates)} groups eligible (window_active={window_active}).")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Report written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
