"""Read or explicitly change the local speech gate without changing group choices."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bot.services.persona_store import PersonaStore


def enabled(path: Path) -> bool:
    if not path.exists():
        return True
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        row = conn.execute("SELECT value FROM options WHERE key='speech_enabled'").fetchone()
    return bool(json.loads(row[0])) if row else True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "enable", "disable"))
    args = parser.parse_args()
    path = ROOT / "data/personas/state.db"
    if args.action != "status":
        store = PersonaStore(path)
        value = args.action == "enable"
        if store.option("speech_enabled", True) != value:
            store.set_option("speech_enabled", value)
    print(json.dumps({"enabled": enabled(path)}))


if __name__ == "__main__":
    main()
