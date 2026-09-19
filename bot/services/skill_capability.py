"""Runtime upstream capability probes and fail-fast skill degradation."""
from __future__ import annotations

import importlib.util
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from bot.config import ROOT


SUPPORTED = "supported"
DEGRADED = "degraded"
UNAVAILABLE = "unavailable"
KNOWN_STATES = frozenset({SUPPORTED, DEGRADED, UNAVAILABLE})


@dataclass(frozen=True, slots=True)
class Capability:
    name: str
    skills: tuple[str, ...]
    required: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()
    probe: Callable[[], bool] | None = None


@dataclass(frozen=True, slots=True)
class CapabilityState:
    name: str
    state: str
    detail: str

    @property
    def usable(self) -> bool:
        return self.state in {SUPPORTED, DEGRADED}


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False


def _path_available(relative: str) -> bool:
    return (ROOT / relative).exists()


DEFINITIONS: tuple[Capability, ...] = (
    Capability(
        name="gsuid_core",
        skills=("nte_game_ui", "wuwa_game_ui"),
        required=("gsuid_core",),
        paths=("GsUID.Core/data/GsData.db",),
        probe=lambda: _module_available("gsuid_core") and _path_available("GsUID.Core/data/GsData.db"),
    ),
    Capability(
        name="nteuid",
        skills=("nte_game_ui",),
        required=("NTEUID",),
        paths=("GsUID.Core/gsuid_core/plugins/NTEUID",),
        probe=lambda: _module_available("NTEUID") and _path_available("GsUID.Core/gsuid_core/plugins/NTEUID"),
    ),
    Capability(
        name="wuwa_uid",
        skills=("wuwa_game_ui",),
        required=("XutheringWavesUID",),
        paths=("GsUID.Core/gsuid_core/plugins/XutheringWavesUID",),
        probe=lambda: _module_available("XutheringWavesUID") and _path_available("GsUID.Core/gsuid_core/plugins/XutheringWavesUID"),
    ),
)


class CapabilityRegistry:
    """Cache read-only probes; never connect, retry or block the event path."""

    def __init__(
        self,
        definitions: tuple[Capability, ...] = DEFINITIONS,
        *,
        ttl_seconds: float = 300.0,
        now: Callable[[], float] | None = None,
    ) -> None:
        self.definitions = definitions
        self.ttl_seconds = max(1.0, float(ttl_seconds))
        self._now = now or time.monotonic
        self._states: dict[str, CapabilityState] = {}
        self._checked_at = 0.0

    def _probe(self, capability: Capability) -> CapabilityState:
        try:
            ok = bool(capability.probe()) if capability.probe else all(
                _module_available(name) for name in capability.required
            )
        except Exception as exc:
            return CapabilityState(capability.name, UNAVAILABLE, type(exc).__name__)
        if ok:
            return CapabilityState(capability.name, SUPPORTED, "")
        missing = [
            name for name in capability.required if not _module_available(name)
        ]
        missing_paths = [
            path for path in capability.paths if not (ROOT / path).exists()
        ]
        detail = "missing " + ", ".join((*missing, *missing_paths)) if (missing or missing_paths) else "probe failed"
        return CapabilityState(capability.name, UNAVAILABLE, detail[:200])

    def refresh(self, *, force: bool = False) -> dict[str, CapabilityState]:
        now = self._now()
        if not force and self._states and now - self._checked_at < self.ttl_seconds:
            return dict(self._states)
        self._states = {
            capability.name: self._probe(capability) for capability in self.definitions
        }
        self._checked_at = now
        return dict(self._states)

    def state(self, name: str) -> CapabilityState:
        states = self.refresh()
        return states.get(name) or CapabilityState(name, UNAVAILABLE, "unknown capability")

    def unusable_skills(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for capability in self.definitions:
            state = self.state(capability.name)
            if state.usable:
                continue
            for skill_id in capability.skills:
                result[skill_id] = capability.name
        return result

    def skill_available(self, skill_id: str) -> bool:
        for capability in self.definitions:
            if skill_id not in capability.skills:
                continue
            if not self.state(capability.name).usable:
                return False
        return True

    def snapshot(self) -> list[dict[str, Any]]:
        states = self.refresh()
        by_name = {capability.name: capability for capability in self.definitions}
        return [
            {
                "name": state.name,
                "state": state.state,
                "detail": state.detail,
                "skills": list(by_name[state.name].skills),
                "usable": state.usable,
            }
            for state in states.values()
        ]


capabilities = CapabilityRegistry()
