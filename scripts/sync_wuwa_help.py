"""Regenerate compact and full Wuthering Waves help catalogs."""

from __future__ import annotations

import json
from pathlib import Path

from bot.services.wuwa_help_catalog import (
    build_compact_catalog,
    build_full_catalog,
    load_upstream_sources,
)


ROOT = Path(__file__).resolve().parent.parent
COMPACT_PATH = ROOT / "bot" / "resources" / "wuwa_help.json"
FULL_PATH = ROOT / "bot" / "resources" / "wuwa_help_full.json"


def _write(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    base, extensions = load_upstream_sources(ROOT)
    compact = build_compact_catalog(base)
    full = build_full_catalog(base, extensions)
    _write(COMPACT_PATH, compact)
    _write(FULL_PATH, full)
    compact_count = sum(len(section["data"]) for section in compact.values())
    full_count = sum(len(section["data"]) for section in full.values())
    print(f"wrote {COMPACT_PATH} ({compact_count} entries)")
    print(f"wrote {FULL_PATH} ({full_count} entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
