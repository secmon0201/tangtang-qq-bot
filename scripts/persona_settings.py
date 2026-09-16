"""Local operator settings for persona enhancement budgets and random speech."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bot.services.persona_store import PersonaStore

LIMITS = {"speech_probability": (0, 1), "speech_cooldown": (0, 86400),
          "speech_group_limit": (0, 1000), "speech_global_limit": (0, 10000),
          "background_group_limit": (0, 100), "background_global_limit": (0, 1000)}
DEFAULTS = dict(speech_probability=0.10, speech_cooldown=600, speech_group_limit=12,
                speech_global_limit=60, background_group_limit=2, background_global_limit=12)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--set", nargs=2, metavar=("KEY", "VALUE"))
    args = parser.parse_args()
    store = PersonaStore(ROOT / "data/personas/state.db")
    if args.set:
        key, raw = args.set
        if key not in LIMITS:
            parser.error("unknown setting; permitted keys: " + ", ".join(LIMITS))
        value = float(raw) if key == "speech_probability" else int(raw)
        low, high = LIMITS[key]
        if not low <= value <= high:
            parser.error(f"value must be between {low} and {high}")
        store.set_option(key, value)
    print(json.dumps({k: store.option(k, v) for k, v in DEFAULTS.items()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
