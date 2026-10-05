"""Read legacy Core subscription routes into Harness without writing upstream."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tangtang_harness.store import Store


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    source = (args.source or root.parent / "GsUID.Core" / "data" / "GsData.db").resolve()
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = [dict(row) for row in connection.execute("SELECT * FROM subscribe WHERE bot_id='onebot'")]
    routes, identities = [], set()
    for row in rows:
        if row.get("WS_BOT_ID"):
            identities.add(row["WS_BOT_ID"])
        topic = row["task_name"]
        feature = "nte" if "NTE" in topic.upper() else "ww" if "鸣潮" in topic or "WW" in topic.upper() else None
        if feature and row.get("bot_self_id") and row.get("msg_id"):
            routes.append({"event_id": str(row["msg_id"]), "self_id": int(row["bot_self_id"]),
                           "user_id": int(row["user_id"]),
                           "group_id": int(row["group_id"]) if row.get("group_id") else None,
                           "text": "#" + feature, "sender": {"nickname": "游戏订阅"},
                           "timestamp": 0, "segments": []})
    env = root.parent / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8-sig").splitlines():
            key, _, value = line.partition("=")
            if key.strip().casefold() == "gsuid_core_botid":
                identity = value.strip().strip("\"'")
                if identity:
                    identities.add(identity)
    settings_path = root / "config" / "settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    core = settings.setdefault("extra", {}).setdefault("core", {})
    identities.discard(core.get("identity", "TangtangHarness"))
    core["receive_identities"] = sorted(set(core.get("receive_identities", [])) | identities)
    if args.apply:
        Store(root).set_setting("core_subscription_sources", routes)
        settings_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"applied": args.apply, "subscription_sources": len(routes),
                      "receive_connections": len(core["receive_identities"]),
                      "upstream_open_mode": "read_only"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
