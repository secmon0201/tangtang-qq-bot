"""Pure proactive timing rules and per-turn hooks; never decides what to say."""
from __future__ import annotations

import hashlib
import math
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Callable, Iterator

STRATEGIES = ("legacy", "active_v1", "low_traffic_v1")
LABELS = {"legacy": "旧规则", "active_v1": "活跃群", "low_traffic_v1": "低流量群"}


def ordinary_text(text: str) -> str:
    text = re.sub(r"\[[^\]]{0,100}\]", "", text).strip()
    if text.startswith(("#", "/", "!", "！")):
        return ""
    text = re.sub(r"https?://\S+", "", text).strip()
    return text if len(text) >= 2 and re.search(r"\w", text) else ""


@dataclass
class TrafficState:
    heat: float = 0.0
    heat_at: float = 0.0
    last_message: float = 0.0
    last_attempt: float = 0.0
    last_tick: float = 0.0
    episode: int = 0
    offered_episode: int = -1
    misses: int = 0
    consumed_message: str = ""
    recent: list[dict] = field(default_factory=list)

    def decay(self, now: float) -> None:
        self.heat *= 2 ** (-max(0, now - self.heat_at) / 300)
        self.heat_at = max(now, self.heat_at)

    def observe(self, user: int, message: str, text: str, now: float) -> bool:
        if now < self.last_message:
            return False
        self.recent = [r for r in self.recent if now - r["at"] <= 600][-511:]
        digest = hashlib.sha256(" ".join(text.split()).encode()).hexdigest()
        if any(r["id"] == message or (r["user"] == user and r["hash"] == digest
                                      and now - r["at"] < 60) for r in self.recent):
            return False
        self.decay(now)
        if not self.last_message or now - self.last_message >= 600:
            self.episode += 1
        if not any(r["user"] == user and int(r["at"] // 60) == int(now // 60) for r in self.recent):
            self.heat += 1
        self.last_message = now
        self.recent.append({"user": user, "id": message, "hash": digest, "at": now, "chars": len(text)})
        return True

    @property
    def rate(self) -> float:
        return self.heat * math.log(2) / 5


@dataclass(frozen=True)
class ProactiveDecision:
    reason: str
    probability: float = 0.0


def decide(state: TrafficState, strategy: str, now: float, hour: int, random_value: float) -> ProactiveDecision:
    state.decay(now)
    if strategy == "legacy":
        return ProactiveDecision("legacy")
    if 2 <= hour < 8:
        return ProactiveDecision("quiet")
    if not state.recent or now - state.last_message > 90:
        return ProactiveDecision("stale")
    cooldown = 1800 if strategy == "low_traffic_v1" else 900
    if state.last_attempt and now - state.last_attempt < cooldown:
        return ProactiveDecision("cooldown")
    if state.recent[-1]["id"] == state.consumed_message:
        return ProactiveDecision("consumed")
    if strategy == "low_traffic_v1":
        if state.offered_episode == state.episode:
            return ProactiveDecision("episode_consumed")
        if state.recent[-1]["chars"] < 6 and sum(now - r["at"] <= 300 for r in state.recent) < 2:
            return ProactiveDecision("short_context")
        state.offered_episode = state.episode
        forced = state.misses >= 2
        if not forced and random_value >= .7:
            state.misses += 1
            return ProactiveDecision("random_miss", .7)
        return ProactiveDecision("candidate", 1.0 if forced else .7)
    if sum(now - r["at"] <= 180 for r in state.recent) < 2 or state.rate < .3:
        return ProactiveDecision("low_heat")
    intensity = 12 if state.rate < 1.5 else 24 if state.rate < 3 else 48
    probability = 1 - math.exp(-intensity / 240)
    return ProactiveDecision("candidate" if random_value < probability else "random_miss", probability)


@dataclass(frozen=True)
class ProactiveTurn:
    strategy: str
    admit: Callable[[], bool]
    current: Callable[[], bool]
    outcome: Callable[[str, str], None]


_turn: ContextVar[ProactiveTurn | None] = ContextVar("proactive_turn", default=None)


def proactive_turn() -> ProactiveTurn | None:
    return _turn.get()


@contextmanager
def proactive_turn_for(turn: ProactiveTurn) -> Iterator[None]:
    token = _turn.set(turn)
    try:
        yield
    finally:
        _turn.reset(token)
