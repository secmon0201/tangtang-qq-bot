from __future__ import annotations

import argparse
import random
from pathlib import Path

from bot.services.hourly_copy import HourlyCopyCatalog, MIN_FRAGMENTS_PER_SEGMENT


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "bot" / "resources" / "zhijiang_hourly_copy.json"
ALIAS_PATH = ROOT / "bot" / "resources" / "zhijiang_character_aliases.json"
PERIOD_KEYS = ("morning", "daytime", "evening", "night")
MIN_TOTAL_COMBINATIONS = 5000


def load_and_validate(source_path: Path, alias_path: Path) -> HourlyCopyCatalog:
    catalog = HourlyCopyCatalog.load(source_path, alias_path)
    for period_key in PERIOD_KEYS:
        period = catalog.periods[period_key]
        for segment_key, segment in period.segments.items():
            if len(segment.texts) < MIN_FRAGMENTS_PER_SEGMENT:
                raise ValueError(
                    f"{period_key}.{segment_key} needs at least {MIN_FRAGMENTS_PER_SEGMENT} fragments"
                )
    counts = catalog.combination_counts()
    if sum(counts.values()) < MIN_TOTAL_COMBINATIONS:
        raise ValueError(
            f"hourly copy source provides only {sum(counts.values())} combinations; "
            f"need at least {MIN_TOTAL_COMBINATIONS}"
        )
    return catalog


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate and sample the segmented runtime hourly copy catalog"
    )
    parser.add_argument("--source", type=Path, default=SOURCE_PATH)
    parser.add_argument("--aliases", type=Path, default=ALIAS_PATH)
    parser.add_argument("--sample", type=int, default=0, help="print N deterministic sample messages")
    parser.add_argument("--seed", type=int, default=20260801)
    args = parser.parse_args()

    catalog = load_and_validate(args.source, args.aliases)
    counts = catalog.combination_counts()
    fragment_counts = {
        period_key: {
            segment_key: len(segment.texts)
            for segment_key, segment in catalog.periods[period_key].segments.items()
        }
        for period_key in PERIOD_KEYS
    }
    total = sum(counts.values())
    print(
        "hourly copy catalog valid: "
        f"period_combinations={counts}; total_combinations={total}; fragments={fragment_counts}; "
        f"alias_categories={list(catalog.categories)}"
    )

    if args.sample < 0:
        raise ValueError("--sample must be non-negative")
    if args.sample:
        rng = random.Random(args.seed)
        for index in range(args.sample):
            period_key = PERIOD_KEYS[index % len(PERIOD_KEYS)]
            sample = catalog.compose(period_key, rng=rng)
            print(f"[{period_key}] {sample.text}")


if __name__ == "__main__":
    main()
