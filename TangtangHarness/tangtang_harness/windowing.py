"""Activity windows and cost projections for Harness conversations.

The permanent session is the unit used for stored history.  An activity
epoch is only the bounded, cache-friendly context that is currently being
assembled for that session.  This module intentionally keeps the two units
separate so releasing a private activity window never removes its history.

Usage values are provider observations.  Missing hit, miss, write, output or
price fields stay ``None`` throughout the calculation; an unknown cost is
never treated as zero.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal


Number = int | float
SessionKind = Literal["group", "private"]
Recommendation = Literal["switch", "continue", "unknown"]


def _number(value: Any) -> Number | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return None


@dataclass(frozen=True, slots=True)
class UsageSample:
    """Canonical usage fields needed by a window cost calculation.

    ``total_cost`` is accepted when a provider reports an authoritative total.
    It may therefore be known even when the provider omits its cost split.
    """

    input_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_miss_tokens: int | None = None
    cache_write_tokens: int | None = None
    output_tokens: int | None = None
    total_cost: float | None = None

    @classmethod
    def from_mapping(cls, usage: dict[str, Any] | None) -> "UsageSample":
        """Build from Harness ``normalize_usage`` output or provider aliases."""
        usage = usage or {}
        details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
        read = usage.get("cache_read_tokens")
        if read is None:
            read = usage.get("cache_read_input_tokens", usage.get("cached_tokens"))
        if read is None and isinstance(details, dict):
            read = details.get("cached_tokens", details.get("cachedTokens"))
        write = usage.get("cache_write_tokens")
        if write is None:
            write = usage.get("cache_creation_input_tokens", usage.get("cache_write_input_tokens"))
        input_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
        output_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
        miss = usage.get("cache_miss_tokens", usage.get("cache_miss_input_tokens"))
        return cls(
            input_tokens=_int_or_none(input_tokens),
            cache_read_tokens=_int_or_none(read),
            cache_miss_tokens=_int_or_none(miss),
            cache_write_tokens=_int_or_none(write),
            output_tokens=_int_or_none(output_tokens),
            total_cost=_float_or_none(usage.get("cost", usage.get("total_cost"))),
        )


def _int_or_none(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _float_or_none(value: Any) -> float | None:
    number = _number(value)
    return float(number) if number is not None else None


@dataclass(frozen=True, slots=True)
class CostRates:
    """Provider prices per one million tokens.

    ``input`` is the non-cached input price.  A missing rate keeps the
    corresponding component unknown, even when its token count is present.
    """

    input_per_million: float | None = None
    cache_read_per_million: float | None = None
    cache_write_per_million: float | None = None
    output_per_million: float | None = None
    currency: str = ""

    @classmethod
    def from_profile(cls, profile: Any) -> "CostRates":
        return cls(
            input_per_million=getattr(profile, "input_price_per_million", None),
            cache_read_per_million=getattr(profile, "cache_read_price_per_million", None),
            cache_write_per_million=getattr(profile, "cache_write_price_per_million", None),
            output_per_million=getattr(profile, "output_price_per_million", None),
            currency=getattr(profile, "currency", "") or "",
        )


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    hit_tokens: int | None
    miss_tokens: int | None
    write_tokens: int | None
    output_tokens: int | None
    hit_cost: float | None
    miss_cost: float | None
    write_cost: float | None
    output_cost: float | None
    total_cost: float | None
    source: Literal["provider", "calculated", "unknown"]


def _component(tokens: int | None, price: float | None) -> float | None:
    if tokens is None or price is None:
        return None
    return tokens * price / 1_000_000


def _fresh_tokens(sample: UsageSample) -> int | None:
    """Return non-cached input only when the split is actually observable."""
    if sample.input_tokens is not None and sample.cache_read_tokens is not None and sample.cache_write_tokens is not None:
        return max(0, sample.input_tokens - sample.cache_read_tokens - sample.cache_write_tokens)
    if sample.cache_miss_tokens is not None and sample.cache_write_tokens is not None:
        return max(0, sample.cache_miss_tokens - sample.cache_write_tokens)
    if sample.cache_miss_tokens is not None:
        return sample.cache_miss_tokens
    return None


def cost_breakdown(sample: UsageSample, rates: CostRates) -> CostBreakdown:
    """Calculate one request without filling omitted usage or prices."""
    hit = sample.cache_read_tokens
    write = sample.cache_write_tokens
    miss = _fresh_tokens(sample)
    hit_cost = _component(hit, rates.cache_read_per_million)
    miss_cost = _component(miss, rates.input_per_million)
    write_cost = _component(write, rates.cache_write_per_million)
    output_cost = _component(sample.output_tokens, rates.output_per_million)
    components = (hit_cost, miss_cost, write_cost, output_cost)
    if sample.total_cost is not None:
        total, source = sample.total_cost, "provider"
    elif all(value is not None for value in components):
        total, source = sum(value for value in components if value is not None), "calculated"
    else:
        total, source = None, "unknown"
    return CostBreakdown(hit, miss, write, sample.output_tokens, hit_cost, miss_cost,
                         write_cost, output_cost, total, source)


@dataclass(frozen=True, slots=True)
class CostScenario:
    """Usage pattern for future rounds in one candidate context segment."""

    name: str
    first: UsageSample | None
    subsequent: UsageSample | None

    @classmethod
    def steady(cls, name: str, usage: UsageSample) -> "CostScenario":
        return cls(name, usage, usage)


@dataclass(frozen=True, slots=True)
class ProjectedCost:
    rounds: int
    total_cost: float | None
    known_rounds: int
    unknown_rounds: int
    currency: str
    round_costs: tuple[float | None, ...]


def project_cost(scenario: CostScenario, rounds: int, rates: CostRates) -> ProjectedCost:
    if rounds < 1:
        raise ValueError("未来轮数必须大于零")
    values: list[float | None] = []
    for index in range(rounds):
        sample = scenario.first if index == 0 and scenario.first is not None else scenario.subsequent
        values.append(cost_breakdown(sample, rates).total_cost if sample is not None else None)
    known = sum(value is not None for value in values)
    total = sum(value for value in values if value is not None) if known == rounds else None
    return ProjectedCost(rounds, total, known, rounds - known, rates.currency, tuple(values))


@dataclass(frozen=True, slots=True)
class WindowCostEvaluation:
    rounds: int
    continue_projection: ProjectedCost
    rebuild_projection: ProjectedCost
    saving_if_rebuilt: float | None
    recommendation: Recommendation
    explanation: str


def compare_window_costs(continue_scenario: CostScenario, rebuild_scenario: CostScenario,
                         rounds: int, rates: CostRates, *, minimum_saving: float = 0.0) -> WindowCostEvaluation:
    """Compare continuing the old epoch with rebuilding a new one.

    A recommendation is only made when both complete projections have known
    totals.  ``minimum_saving`` avoids switching for rounding noise and can be
    expressed in the configured currency.
    """
    continued = project_cost(continue_scenario, rounds, rates)
    rebuilt = project_cost(rebuild_scenario, rounds, rates)
    if continued.total_cost is None or rebuilt.total_cost is None:
        return WindowCostEvaluation(
            rounds, continued, rebuilt, None, "unknown",
            f"未来{rounds}轮至少有一部分实际 usage 或价格未知，暂时不能比较继续旧段和重建新段的成本。",
        )
    saving = continued.total_cost - rebuilt.total_cost
    if saving > minimum_saving:
        recommendation: Recommendation = "switch"
        explanation = f"按未来{rounds}轮实际 usage 投影，重建新段预计少花 {saving:.8f}{rates.currency}，建议在本轮完成后切换。"
    else:
        recommendation = "continue"
        explanation = f"按未来{rounds}轮实际 usage 投影，重建新段未少花超过 {minimum_saving:.8f}{rates.currency}，继续当前段。"
    return WindowCostEvaluation(rounds, continued, rebuilt, saving, recommendation, explanation)


@dataclass(slots=True)
class ContextEpoch:
    epoch: int
    opened_at: float
    snapshot_revision: int | None = None
    closed_at: float | None = None
    close_reason: str = ""


@dataclass(frozen=True, slots=True)
class WindowTransition:
    session_key: str
    from_epoch: int
    to_epoch: int | None
    reason: str
    snapshot_revision: int | None
    at: float


@dataclass(slots=True)
class PermanentSession:
    session_key: str
    kind: SessionKind
    epochs: list[ContextEpoch] = field(default_factory=list)
    active: ContextEpoch | None = None
    last_activity_at: float | None = None
    turn_in_progress: bool = False
    turn_completed_since_switch: bool = False
    completed_turns: int = 0


class WindowManager:
    """Own activity epochs while leaving permanent message history elsewhere."""

    def __init__(self, *, private_idle_seconds: float = 1_800) -> None:
        self.private_idle_seconds = private_idle_seconds
        self._sessions: dict[str, PermanentSession] = {}
        self._transitions: list[WindowTransition] = []

    @staticmethod
    def kind_for(session_key: str) -> SessionKind:
        if session_key.startswith("group:"):
            return "group"
        if session_key.startswith("private:"):
            return "private"
        raise ValueError("session_key 必须以 group: 或 private: 开头")

    def _session(self, session_key: str) -> PermanentSession:
        session = self._sessions.get(session_key)
        if session is None:
            session = PermanentSession(session_key, self.kind_for(session_key))
            self._sessions[session_key] = session
        return session

    def get_or_open(self, session_key: str, *, now: float | None = None) -> ContextEpoch:
        now = time.time() if now is None else now
        session = self._session(session_key)
        if session.active is not None:
            session.last_activity_at = now
            return session.active
        epoch_number = session.epochs[-1].epoch + 1 if session.epochs else 1
        epoch = ContextEpoch(epoch_number, now)
        previous = session.epochs[-1] if session.epochs else None
        session.epochs.append(epoch)
        session.active = epoch
        session.last_activity_at = now
        if previous is not None:
            self._transitions.append(WindowTransition(session_key, previous.epoch, epoch.epoch,
                                                       "private_idle_reopen", previous.snapshot_revision, now))
        return epoch

    def begin_turn(self, session_key: str, *, now: float | None = None) -> ContextEpoch:
        session = self._session(session_key)
        if session.turn_in_progress:
            raise RuntimeError("当前会话已有未完成的轮次")
        epoch = self.get_or_open(session_key, now=now)
        session.turn_in_progress = True
        session.last_activity_at = time.time() if now is None else now
        return epoch

    def cancel_turn(self, session_key: str) -> None:
        """Release a pending attempt without erasing the last completed turn."""
        self._session(session_key).turn_in_progress = False

    def complete_turn(self, session_key: str, *, snapshot_revision: int | None = None,
                      now: float | None = None) -> ContextEpoch:
        now = time.time() if now is None else now
        session = self._session(session_key)
        if not session.turn_in_progress or session.active is None:
            raise RuntimeError("只能在已开始的轮次完成后记录窗口")
        session.turn_in_progress = False
        session.turn_completed_since_switch = True
        session.completed_turns += 1
        session.last_activity_at = now
        session.active.snapshot_revision = snapshot_revision or None
        return session.active

    def switch_after_turn(self, session_key: str, *, reason: str, snapshot_revision: int | None,
                          now: float | None = None) -> ContextEpoch:
        """Close the current epoch and open the next one after a completed turn."""
        now = time.time() if now is None else now
        session = self._session(session_key)
        if (session.turn_in_progress or not session.turn_completed_since_switch
                or session.completed_turns == 0 or session.active is None):
            raise RuntimeError("上下文段只能在轮次完成后切换")
        old = session.active
        old.closed_at, old.close_reason = now, reason
        snapshot_revision = snapshot_revision or None
        epoch = ContextEpoch(old.epoch + 1, now, snapshot_revision=snapshot_revision)
        session.epochs.append(epoch)
        session.active = epoch
        session.last_activity_at = now
        session.turn_completed_since_switch = False
        self._transitions.append(WindowTransition(session_key, old.epoch, epoch.epoch,
                                                   reason, snapshot_revision, now))
        return epoch

    def release_idle(self, *, now: float | None = None) -> list[WindowTransition]:
        """Release private runtime state while retaining its permanent session."""
        now = time.time() if now is None else now
        released: list[WindowTransition] = []
        for session in self._sessions.values():
            if session.kind != "private" or session.active is None or session.turn_in_progress:
                continue
            if session.last_activity_at is None or now - session.last_activity_at < self.private_idle_seconds:
                continue
            old = session.active
            old.closed_at, old.close_reason = now, "private_idle_release"
            transition = WindowTransition(session.session_key, old.epoch, None,
                                          "private_idle_release", old.snapshot_revision, now)
            self._transitions.append(transition)
            released.append(transition)
            session.active = None
        return released

    def session(self, session_key: str) -> PermanentSession:
        return self._session(session_key)

    def export(self, session_key: str) -> dict[str, Any]:
        """Serialize only activity metadata; permanent messages stay in Store."""
        session = self._session(session_key)
        return {
            'session_key': session.session_key,
            'kind': session.kind,
            'epochs': [
                {'epoch': item.epoch, 'opened_at': item.opened_at,
                 'snapshot_revision': item.snapshot_revision,
                 'closed_at': item.closed_at, 'close_reason': item.close_reason}
                for item in session.epochs
            ],
            'active_epoch': session.active.epoch if session.active else None,
            'last_activity_at': session.last_activity_at,
            'completed_turns': session.completed_turns,
            'turn_completed_since_switch': session.turn_completed_since_switch,
        }

    def restore(self, session_key: str, value: dict[str, Any] | None) -> None:
        """Restore activity metadata after a Harness process restart."""
        if not value or session_key in self._sessions:
            return
        session = self._session(session_key)
        session.epochs = [ContextEpoch(int(item['epoch']), float(item['opened_at']),
                                       item.get('snapshot_revision') or None, item.get('closed_at'),
                                       str(item.get('close_reason', '')))
                          for item in value.get('epochs', []) if isinstance(item, dict)]
        active_id = value.get('active_epoch')
        session.active = next((item for item in session.epochs if item.epoch == active_id), None)
        session.last_activity_at = value.get('last_activity_at')
        session.completed_turns = int(value.get('completed_turns', 0))
        session.turn_completed_since_switch = bool(value.get('turn_completed_since_switch', False))

    def transitions(self, session_key: str | None = None) -> tuple[WindowTransition, ...]:
        if session_key is None:
            return tuple(self._transitions)
        return tuple(item for item in self._transitions if item.session_key == session_key)

