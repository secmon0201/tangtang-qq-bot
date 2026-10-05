"""Usage and cost projections used by the Harness console and window policy.

This module deliberately keeps provider-reported values separate from local
price estimates.  A missing usage field remains unknown; it is never treated
as a zero merely to make a ratio or amount renderable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class UsageBreakdown:
    input_tokens: int | None
    cache_read_tokens: int | None
    cache_write_tokens: int | None
    cache_miss_tokens: int | None
    output_tokens: int | None
    cache_ratio: float | None
    hit_cost: float | None
    miss_cost: float | None
    write_cost: float | None
    output_cost: float | None
    total_cost: float | None
    currency: str | None
    cost_status: str

    @classmethod
    def from_usage(cls, usage: dict[str, Any] | None, profile: Any | None = None) -> "UsageBreakdown":
        value = usage or {}
        input_tokens = _int_or_none(value.get("input_tokens"))
        read = _int_or_none(value.get("cache_read_tokens"))
        write = _int_or_none(value.get("cache_write_tokens"))
        miss = _int_or_none(value.get("cache_miss_tokens"))
        output = _int_or_none(value.get("output_tokens"))
        ratio = value.get("cache_ratio")
        if not isinstance(ratio, (int, float)):
            ratio = read / input_tokens if read is not None and input_tokens and 0 <= read <= input_tokens else None

        prices = {
            "input": _price(profile, "input_price_per_million"),
            "read": _price(profile, "cache_read_price_per_million"),
            "write": _price(profile, "cache_write_price_per_million"),
            "output": _price(profile, "output_price_per_million"),
        }
        # input_tokens is the normalized total input for Harness adapters.  Do
        # not add cache_read/cache_write to it a second time.
        # ``cache_miss_tokens`` in the legacy adapter includes a separately
        # reported write.  Prefer the total/read/write split when present so
        # the component ledger never charges a write twice.
        fresh = (max(0, input_tokens - read - write)
                 if input_tokens is not None and read is not None and write is not None
                 else max(0, miss - write) if miss is not None and write is not None
                 else miss)
        hit_cost = _per_million(read, prices["read"])
        miss_cost = _per_million(fresh, prices["input"])
        write_cost = _per_million(write, prices["write"])
        output_cost = _per_million(output, prices["output"])
        components = (hit_cost, miss_cost, write_cost, output_cost)
        total = sum(components) if all(item is not None for item in components) else None
        if total is not None:
            status = "estimated"
        elif any(item is not None for item in components):
            status = "partial"
        else:
            status = "unknown"
        return cls(input_tokens, read, write, fresh, output, ratio,
                   hit_cost, miss_cost, write_cost, output_cost, total,
                   value.get("currency") if isinstance(value.get("currency"), str) else None,
                   status)

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "cache_miss_tokens": self.cache_miss_tokens,
            "output_tokens": self.output_tokens,
            "cache_ratio": self.cache_ratio,
            "hit_cost": self.hit_cost,
            "miss_cost": self.miss_cost,
            "write_cost": self.write_cost,
            "output_cost": self.output_cost,
            "total_cost": self.total_cost,
            "currency": self.currency,
            "cost_status": self.cost_status,
        }


def _int_or_none(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _price(profile: Any | None, name: str) -> float | None:
    value = getattr(profile, name, None) if profile is not None else None
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _per_million(tokens: int | None, price: float | None) -> float | None:
    if tokens is None or price is None:
        return None
    return round(tokens * price / 1_000_000, 8)


def aggregate_breakdowns(rows: list[dict[str, Any]], profile_lookup=None) -> dict[str, Any]:
    """Aggregate request usage without combining unknown values with zeros."""
    fields = ("input_tokens", "cache_read_tokens", "cache_write_tokens", "cache_miss_tokens", "output_tokens")
    result: dict[str, Any] = {field: None for field in fields}
    result.update({"request_count": len(rows), "known": {}, "cache_ratio": None,
                   "hit_cost": None, "miss_cost": None, "write_cost": None,
                   "output_cost": None, "total_cost": None, "cost_status": "unknown"})
    for field in fields:
        values = [row.get(field) for row in rows if isinstance(row.get(field), (int, float)) and not isinstance(row.get(field), bool)]
        result[field] = sum(values) if values else None
        result["known"][field] = len(values)
    if result["input_tokens"] is not None and result["cache_read_tokens"] is not None:
        result["cache_ratio"] = result["cache_read_tokens"] / result["input_tokens"] if result["input_tokens"] else None
    components = ("hit_cost", "miss_cost", "write_cost", "output_cost")
    for field in components:
        values = [row.get(field) for row in rows if isinstance(row.get(field), (int, float)) and not isinstance(row.get(field), bool)]
        result[field] = round(sum(values), 8) if values and len(values) == len(rows) else (round(sum(values), 8) if values else None)
        result["known"][field] = len(values)
    known_cost = [row.get("total_cost") for row in rows if isinstance(row.get("total_cost"), (int, float))]
    result["total_cost"] = round(sum(known_cost), 8) if known_cost else None
    result["cost_status"] = "estimated" if len(known_cost) == len(rows) and rows else "partial" if known_cost else "unknown"
    return result
