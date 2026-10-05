"""Deterministic mood decay without changing identity or memories."""
from datetime import datetime


def mood_decay(value: float, previous: str, now: str) -> float:
    try:
        seconds = max(0.0, (datetime.fromisoformat(now) - datetime.fromisoformat(previous)).total_seconds())
    except (TypeError, ValueError):
        return value
    return 0.5 + (float(value) - 0.5) * 0.5 ** (seconds / 21600)
