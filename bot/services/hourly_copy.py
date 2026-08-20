from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


SEGMENT_KEYS = ("before", "middle", "after")
MIN_FRAGMENTS_PER_SEGMENT = 100
PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")
FORBIDDEN_ALIAS_TERMS = frozenset({"矮一姑", "魔丸", "墓岛人", "岛民", "坏女人"})


@dataclass(frozen=True, slots=True)
class HourlyCopySegment:
    key: str
    label: str
    texts: tuple[str, ...]
    name_categories: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HourlyCopyPeriod:
    key: str
    label: str
    start: str
    end: str
    segments: Mapping[str, HourlyCopySegment]


@dataclass(frozen=True, slots=True)
class GeneratedHourlyCopy:
    text: str
    fingerprint: int


class HourlyCopyCatalog:
    """Load segmented copy and compose a fresh message at delivery time."""

    def __init__(
        self,
        periods: Mapping[str, HourlyCopyPeriod],
        categories: Mapping[str, tuple[str, ...]],
        source_path: Path,
        alias_path: Path,
    ) -> None:
        self.periods = dict(periods)
        self.categories = dict(categories)
        self.source_path = source_path
        self.alias_path = alias_path
        self._rng = random.SystemRandom()

    @classmethod
    def load(cls, source_path: Path, alias_path: Path) -> "HourlyCopyCatalog":
        payload = json.loads(source_path.read_text(encoding="utf-8-sig"))
        alias_payload = json.loads(alias_path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict) or payload.get("schema") != "segmented-runtime-v1":
            raise ValueError("hourly copy source must use segmented-runtime-v1")
        if not isinstance(payload.get("target_combinations"), int) or payload["target_combinations"] < 5000:
            raise ValueError("hourly copy source must declare at least 5000 target combinations")
        if not isinstance(alias_payload, dict):
            raise ValueError("hourly alias source must be a JSON object")

        categories = cls._load_categories(alias_payload)
        raw_periods = payload.get("periods")
        if not isinstance(raw_periods, dict):
            raise ValueError("hourly copy source must contain periods")
        expected_periods = ("morning", "daytime", "evening", "night")
        if set(raw_periods) != set(expected_periods):
            raise ValueError(f"hourly copy periods must be {expected_periods}")

        periods: dict[str, HourlyCopyPeriod] = {}
        for period_key in expected_periods:
            raw_period = raw_periods[period_key]
            if not isinstance(raw_period, dict):
                raise ValueError(f"hourly copy period {period_key} must be an object")
            raw_segments = raw_period.get("segments")
            if not isinstance(raw_segments, dict) or set(raw_segments) != set(SEGMENT_KEYS):
                raise ValueError(f"hourly copy period {period_key} must contain before/middle/after")
            segments: dict[str, HourlyCopySegment] = {}
            has_name_placeholder = False
            for segment_key in SEGMENT_KEYS:
                raw_segment = raw_segments[segment_key]
                if not isinstance(raw_segment, dict):
                    raise ValueError(f"hourly copy segment {period_key}.{segment_key} must be an object")
                raw_texts = raw_segment.get("texts")
                raw_categories = raw_segment.get("name_categories")
                if not isinstance(raw_texts, list) or not raw_texts:
                    raise ValueError(f"hourly copy segment {period_key}.{segment_key} has no texts")
                if len(raw_texts) < MIN_FRAGMENTS_PER_SEGMENT:
                    raise ValueError(
                        f"hourly copy segment {period_key}.{segment_key} needs at least "
                        f"{MIN_FRAGMENTS_PER_SEGMENT} fragments"
                    )
                if not isinstance(raw_categories, list) or not raw_categories:
                    raise ValueError(
                        f"hourly copy segment {period_key}.{segment_key} has no name categories"
                    )
                texts = tuple(cls._validate_fragment(text, period_key, segment_key) for text in raw_texts)
                has_name_placeholder = has_name_placeholder or any("{name}" in text for text in texts)
                if len(set(texts)) != len(texts):
                    raise ValueError(f"duplicate hourly copy fragments in {period_key}.{segment_key}")
                category_keys = tuple(str(value).strip() for value in raw_categories if str(value).strip())
                if any(category not in categories for category in category_keys):
                    raise ValueError(
                        f"unknown name category in {period_key}.{segment_key}: {category_keys}"
                    )
                segments[segment_key] = HourlyCopySegment(
                    key=segment_key,
                    label=str(raw_segment.get("label") or segment_key),
                    texts=texts,
                    name_categories=category_keys,
                )
            if not has_name_placeholder:
                raise ValueError(f"hourly copy period {period_key} has no {{name}} placeholder")
            periods[period_key] = HourlyCopyPeriod(
                key=period_key,
                label=str(raw_period.get("label") or period_key),
                start=str(raw_period.get("start") or "00:00"),
                end=str(raw_period.get("end") or "23:59"),
                segments=segments,
            )
        return cls(periods, categories, source_path, alias_path)

    @staticmethod
    def _validate_fragment(value: Any, period_key: str, segment_key: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"empty hourly copy fragment in {period_key}.{segment_key}")
        text = value.strip()
        unknown = set(PLACEHOLDER_RE.findall(text)) - {"name"}
        if unknown:
            raise ValueError(f"unknown placeholders in {period_key}.{segment_key}: {sorted(unknown)}")
        return text

    @classmethod
    def _load_categories(cls, payload: Mapping[str, Any]) -> dict[str, tuple[str, ...]]:
        raw_categories = payload.get("categories")
        if not isinstance(raw_categories, dict) or not raw_categories:
            raise ValueError("hourly alias source must contain categories")
        categories: dict[str, tuple[str, ...]] = {}
        all_aliases: set[str] = set()
        for category_key, raw_category in raw_categories.items():
            if not isinstance(raw_category, dict):
                raise ValueError(f"hourly alias category must be an object: {category_key}")
            raw_members = raw_category.get("members")
            if not isinstance(raw_members, dict) or not raw_members:
                raise ValueError(f"hourly alias category has no members: {category_key}")
            category_aliases: list[str] = []
            member_categories: dict[str, tuple[str, ...]] = {}
            for member_key, raw_member in raw_members.items():
                if not isinstance(raw_member, dict):
                    raise ValueError(f"hourly alias member must be an object: {category_key}.{member_key}")
                raw_aliases = raw_member.get("aliases")
                if not isinstance(raw_aliases, list) or not raw_aliases:
                    raise ValueError(f"hourly alias member has no aliases: {category_key}.{member_key}")
                aliases = [str(value).strip() for value in raw_aliases if str(value).strip()]
                if len(aliases) != len(set(aliases)):
                    raise ValueError(f"duplicate aliases in {category_key}.{member_key}")
                forbidden = sorted(set(aliases) & FORBIDDEN_ALIAS_TERMS)
                if forbidden:
                    raise ValueError(f"forbidden aliases in {category_key}.{member_key}: {forbidden}")
                category_aliases.extend(aliases)
                member_categories[f"{category_key}.{member_key}"] = tuple(aliases)
            if len(category_aliases) != len(set(category_aliases)):
                raise ValueError(f"duplicate aliases in category {category_key}")
            if all_aliases & set(category_aliases):
                overlap = sorted(all_aliases & set(category_aliases))
                raise ValueError(f"aliases must belong to one category only: {overlap}")
            all_aliases.update(category_aliases)
            categories[str(category_key)] = tuple(category_aliases)
            categories.update(member_categories)
        return categories

    def names_for(self, category_keys: Iterable[str]) -> tuple[str, ...]:
        names: list[str] = []
        for category_key in category_keys:
            names.extend(self.categories[str(category_key)])
        if not names:
            raise ValueError("hourly copy segment has no available aliases")
        return tuple(names)

    def combination_count(self, period_key: str) -> int:
        """Count distinct source-copy combinations, excluding variable aliases."""
        period = self.periods[period_key]
        total = 1
        for segment in period.segments.values():
            total *= len(segment.texts)
        return total

    def combination_counts(self) -> dict[str, int]:
        return {key: self.combination_count(key) for key in self.periods}

    @staticmethod
    def fingerprint(text: str) -> int:
        # SQLite INTEGER is signed 64-bit; keep the digest inside that range.
        return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big") & ((1 << 63) - 1)

    def compose(
        self,
        period_key: str,
        blocked_fingerprints: Iterable[int] = (),
        rng: random.Random | random.SystemRandom | None = None,
        attempts: int = 80,
    ) -> GeneratedHourlyCopy:
        period = self.periods[period_key]
        chooser = rng or self._rng
        blocked = {int(value) for value in blocked_fingerprints}
        candidate = ""
        fingerprint = 0
        for _ in range(max(1, int(attempts))):
            source_parts: list[str] = []
            rendered_parts: list[str] = []
            for segment_key in SEGMENT_KEYS:
                segment = period.segments[segment_key]
                fragment = chooser.choice(segment.texts)
                source_parts.append(fragment)
                if "{name}" in fragment:
                    fragment = fragment.replace(
                        "{name}", chooser.choice(self.names_for(segment.name_categories))
                    )
                rendered_parts.append(fragment)
            candidate = "".join(rendered_parts).strip()
            # Names are runtime variables; recent-copy avoidance must compare
            # the selected source fragments rather than the rendered alias.
            fingerprint = self.fingerprint("\x1f".join(source_parts))
            if fingerprint not in blocked:
                return GeneratedHourlyCopy(candidate, fingerprint)
        return GeneratedHourlyCopy(candidate, fingerprint)
