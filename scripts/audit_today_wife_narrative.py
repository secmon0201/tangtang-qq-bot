"""Audit the declarative Today Wife narration library before it is deployed."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.services.today_wife_narrative import (  # noqa: E402
    EVENT_BEATS,
    STYLE_BANKS,
    compose_narrative,
    narrative_capacity,
    narrative_resource_report,
)
from bot.services.today_wife_content import THEME_PACKS  # noqa: E402


VALUES = {
    "actor": "小明",
    "target": "小夏",
    "left": "小白",
    "right": "小雨",
    "prop": "会发光的纸条",
    "mechanism": "匿名回信",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Today Wife narrative resources.")
    parser.add_argument("--samples", type=int, default=2048, help="Samples per style/event pair (default: 2048).")
    args = parser.parse_args()
    samples = max(100, int(args.samples))
    capacity = narrative_capacity()
    report = narrative_resource_report()
    print("Resources:")
    for path in report["paths"]:
        print(f"- {path}")
    print(f"Layers: {report['layers']}")
    print(f"Combined-library minimum theoretical combinations: {min(capacity.values()):,}")

    failures: list[str] = []
    for style in STYLE_BANKS:
        for kind in EVENT_BEATS:
            texts = [compose_narrative(style, kind, f"audit:{style}:{kind}:{index}", VALUES) for index in range(samples)]
            unique_ratio = len(set(texts)) / samples
            print(f"{style} / {kind}: {len(set(texts)):,}/{samples:,} unique ({unique_ratio:.1%}), capacity {capacity[f'{style}:{kind}']:,}")
            if unique_ratio < 0.98:
                failures.append(f"{style}/{kind} has only {unique_ratio:.1%} unique sampled text")
    print("Per-theme active content:")
    for pack in THEME_PACKS:
        active_capacity = narrative_capacity(theme_id=pack.id)
        minimum = min(value for key, value in active_capacity.items() if key.startswith(f"{pack.style}:"))
        print(f"{pack.id}: {minimum:,} minimum active combinations")
        if minimum < 1_000_000:
            failures.append(f"{pack.id} has fewer than 1,000,000 active combinations")
    if failures:
        print("FAILED")
        print("\n".join(f"- {failure}" for failure in failures))
        return 1
    print("PASS: templates, placeholders, capacity, and sampled uniqueness are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
