from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class _RepeatRun:
    text: str
    count: int = 1


class TripleRepeatTracker:
    """Track consecutive repeatable text in memory without retaining chat history."""

    def __init__(self) -> None:
        self._runs: dict[int, _RepeatRun] = {}

    def observe(self, group_id: int, text: str) -> bool:
        """Return True exactly once when a group reaches three matching messages."""
        group_id = int(group_id)
        run = self._runs.get(group_id)
        if run is None or run.text != text:
            self._runs[group_id] = _RepeatRun(text=text)
            return False
        run.count += 1
        return run.count == 3

    def clear(self, group_id: int | None = None) -> None:
        if group_id is None:
            self._runs.clear()
        else:
            self._runs.pop(int(group_id), None)


triple_repeat_tracker = TripleRepeatTracker()
