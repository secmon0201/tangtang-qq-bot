"""Inspect/change fixed proactive strategies without changing feature switches."""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bot.config import ROOT, settings
from bot.services.proactive_policy import STRATEGIES
from bot.services.proactive_store import ProactiveStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", choices=STRATEGIES)
    parser.add_argument("--groups", nargs="+", type=int)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--default", action="store_true", help="also select default for future managed groups")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    with sqlite3.connect((ROOT / "data" / "bot.db").as_uri() + "?mode=ro", uri=True) as c:
        managed = {int(r[0]) for r in c.execute("SELECT group_id FROM managed_groups WHERE enabled=1")}
        enabled = dict(c.execute("SELECT group_id,configured_enabled FROM group_features WHERE feature_key='proactive_chat'"))
    targets = managed if args.all or not args.groups else set(args.groups)
    if not targets <= managed or (args.apply and not args.strategy):
        parser.error("Select managed groups and an explicit strategy before --apply")
    store = ProactiveStore(ROOT / "data" / "tangtang" / "proactive.db", settings.timezone)
    for group_id in sorted(targets):
        if args.apply:
            store.set_policy(group_id, args.strategy, time.time())
        print(store.status(group_id, time.time()), f"switch={enabled.get(group_id, 'unset')}")
    if args.strategy and not args.apply:
        print(f"Preview only: {len(targets)} groups -> {args.strategy}")
    if args.default and args.apply:
        store.set_policy(0, args.strategy, time.time())


if __name__ == "__main__":
    main()
