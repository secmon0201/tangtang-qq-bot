from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from bot.db import Database
from bot.services.hourly_announcements import HourlyAnnouncementService
from bot.services.hourly_copy import HourlyCopyCatalog


ROOT = Path(__file__).parents[1]


def make_catalog() -> HourlyCopyCatalog:
    return HourlyCopyCatalog.load(
        ROOT / "bot" / "resources" / "zhijiang_hourly_copy.json",
        ROOT / "bot" / "resources" / "zhijiang_character_aliases.json",
    )


def test_segmented_catalog_has_more_than_5000_real_combinations():
    catalog = make_catalog()
    counts = catalog.combination_counts()

    assert set(counts) == {"morning", "daytime", "evening", "night"}
    assert counts["morning"] == 112 * 112 * 112
    assert sum(counts.values()) == 5_185_596
    assert all(count > 5000 for count in counts.values())
    assert sum(counts.values()) > 5_000_000
    assert all(
        len(catalog.periods[period].segments[segment].texts) >= 100
        for period in counts
        for segment in ("before", "middle", "after")
    )
    assert "漂泊者" in catalog.names_for(("other.mingchao",))


def test_runtime_composition_uses_alias_source_and_changes_with_random_choice():
    catalog = make_catalog()
    first = catalog.compose("morning", rng=random.Random(1))
    second = catalog.compose("morning", rng=random.Random(2))

    assert first.text != second.text
    assert first.fingerprint != second.fingerprint
    assert "{name}" not in first.text
    assert any(alias in first.text for alias in catalog.categories["asoul"] + catalog.categories["other"])
    assert any(alias in second.text for alias in catalog.categories["asoul"] + catalog.categories["other"])


def test_recent_copy_fingerprint_ignores_runtime_alias_choice():
    catalog = make_catalog()
    aliases = catalog.categories["asoul"] + catalog.categories["other"]

    class FixedChooser:
        def __init__(self, alias_index: int) -> None:
            self.alias_index = alias_index

        def choice(self, values):
            if values and values[0] in aliases:
                return values[self.alias_index % len(values)]
            return values[0]

    first = catalog.compose("morning", rng=FixedChooser(0))
    second = catalog.compose("morning", rng=FixedChooser(1))

    assert first.text != second.text
    assert first.fingerprint == second.fingerprint


def test_catalog_rejects_a_segment_below_the_100_fragment_floor(tmp_path):
    payload = json.loads((ROOT / "bot" / "resources" / "zhijiang_hourly_copy.json").read_text(encoding="utf-8-sig"))
    payload["periods"]["morning"]["segments"]["before"]["texts"] = payload["periods"]["morning"]["segments"]["before"]["texts"][:99]
    source = tmp_path / "hourly.json"
    source.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="at least 100 fragments"):
        HourlyCopyCatalog.load(source, ROOT / "bot" / "resources" / "zhijiang_character_aliases.json")


def test_hourly_service_rejects_duplicate_copy_texts(tmp_path):
    database = Database(tmp_path / "bot.db")
    database.configure_groups((1001,))

    with pytest.raises(ValueError, match="duplicate"):
        HourlyAnnouncementService(
            database,
            lambda: (1001,),
            texts=("相同文案", "相同文案"),
        )
