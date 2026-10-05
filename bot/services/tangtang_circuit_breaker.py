"""Adaptive sliding-window circuit breaker for Tangtang model endpoints."""

from __future__ import annotations

import collections
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from bot.services.tangtang_models import TangtangModelCatalog, TangtangModelProfile


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


ELIGIBLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class TangtangCircuitBreaker:
    """Sliding-window circuit breaker that tracks upstream failures and enables multi-model failover."""

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        window_seconds: float = 60.0,
        cool_down_seconds: float = 180.0,
        half_open_success_threshold: int = 2,
    ) -> None:
        self.failure_threshold = max(1, int(failure_threshold))
        self.window_seconds = max(0.05, float(window_seconds))
        self.cool_down_seconds = max(0.05, float(cool_down_seconds))
        self.half_open_success_threshold = max(1, int(half_open_success_threshold))

        self._state: CircuitState = CircuitState.CLOSED
        self._failure_timestamps: collections.deque[float] = collections.deque()
        self._consecutive_successes_in_half_open: int = 0
        self._state_changed_at: float = 0.0

    @property
    def state(self) -> CircuitState:
        now = time.monotonic()
        if self._state == CircuitState.OPEN:
            if now - self._state_changed_at >= self.cool_down_seconds:
                self._state = CircuitState.HALF_OPEN
                self._consecutive_successes_in_half_open = 0
                self._state_changed_at = now
        return self._state

    def is_available(self) -> bool:
        """Return True if requests to the primary endpoint are permitted."""
        return self.state in {CircuitState.CLOSED, CircuitState.HALF_OPEN}

    def record_success(self) -> None:
        """Record a successful response (200 OK)."""
        now = time.monotonic()
        current_state = self.state
        if current_state == CircuitState.HALF_OPEN:
            self._consecutive_successes_in_half_open += 1
            if self._consecutive_successes_in_half_open >= self.half_open_success_threshold:
                self._state = CircuitState.CLOSED
                self._failure_timestamps.clear()
                self._state_changed_at = now
        elif current_state == CircuitState.CLOSED:
            self._evict_expired(now)

    def record_failure(self, error: Any = None) -> bool:
        """Record an eligible failure. Returns True if circuit tripped to OPEN."""
        if not self._is_eligible_error(error):
            return False

        now = time.monotonic()
        current_state = self.state
        if current_state == CircuitState.HALF_OPEN:
            self._trip(now)
            return True

        self._evict_expired(now)
        self._failure_timestamps.append(now)

        if len(self._failure_timestamps) >= self.failure_threshold:
            self._trip(now)
            return True
        return False

    def _trip(self, now: float) -> None:
        self._state = CircuitState.OPEN
        self._state_changed_at = now
        self._consecutive_successes_in_half_open = 0
        self._failure_timestamps.clear()

    def _evict_expired(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._failure_timestamps and self._failure_timestamps[0] < cutoff:
            self._failure_timestamps.popleft()

    def _is_eligible_error(self, error: Any) -> bool:
        if error is None:
            return True
        if isinstance(error, int):
            return error in ELIGIBLE_STATUS_CODES
        status_code = getattr(getattr(error, "response", None), "status_code", None)
        if status_code is not None:
            return status_code in ELIGIBLE_STATUS_CODES
        err_name = type(error).__name__
        if "Timeout" in err_name or "Transport" in err_name or "Connect" in err_name:
            return True
        return False

    def resolve_fallback_profile(
        self,
        catalog: TangtangModelCatalog | None,
    ) -> TangtangModelProfile | None:
        """Locate an eligible backup profile when the primary circuit is open."""
        if catalog is None or not catalog.profiles:
            return None
        active = catalog.active
        for profile in catalog.profiles:
            if profile.name.casefold() == active.name.casefold():
                continue
            if "4" in profile.name or "gemini" in profile.name.casefold():
                return profile
        for profile in catalog.profiles:
            if profile.name.casefold() != active.name.casefold():
                return profile
        return None
