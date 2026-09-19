"""Runtime capability probes and fail-fast skill degradation."""
from __future__ import annotations

from bot.services.skill_capability import (
    Capability,
    CapabilityRegistry,
    SUPPORTED,
    UNAVAILABLE,
    capabilities,
)


def test_live_capabilities_report_upstream_state():
    snapshot = capabilities.snapshot()
    names = {item["name"] for item in snapshot}
    assert {"gsuid_core", "nteuid", "wuwa_uid"} <= names
    assert all(item["state"] in {SUPPORTED, "degraded", UNAVAILABLE} for item in snapshot)
    by_name = {item["name"]: item for item in snapshot}
    assert by_name["gsuid_core"]["state"] == SUPPORTED
    assert by_name["nteuid"]["state"] == SUPPORTED
    assert by_name["wuwa_uid"]["state"] == SUPPORTED


def test_probe_success_and_failure_are_reported():
    registry = CapabilityRegistry(
        (
            Capability("good", ("a",), probe=lambda: True),
            Capability("bad", ("b",), probe=lambda: False),
        ),
        now=lambda: 0.0,
    )
    states = registry.refresh()
    assert states["good"].state == SUPPORTED
    assert states["bad"].state == UNAVAILABLE
    assert registry.skill_available("a")
    assert not registry.skill_available("b")
    assert registry.unusable_skills() == {"b": "bad"}


def test_probe_exception_is_unavailable_not_crash():
    def boom() -> bool:
        raise RuntimeError("probe exploded")

    registry = CapabilityRegistry(
        (Capability("broken", ("x",), probe=boom),),
        now=lambda: 0.0,
    )
    state = registry.state("broken")
    assert state.state == UNAVAILABLE
    assert state.detail == "RuntimeError"


def test_probe_result_is_cached_until_ttl():
    clock = {"value": 0.0}
    calls = {"count": 0}

    def probe() -> bool:
        calls["count"] += 1
        return True

    registry = CapabilityRegistry(
        (Capability("cached", ("s",), probe=probe),),
        ttl_seconds=100.0,
        now=lambda: clock["value"],
    )
    registry.refresh()
    registry.refresh()
    assert calls["count"] == 1
    clock["value"] = 200.0
    registry.refresh()
    assert calls["count"] == 2
    registry.refresh(force=True)
    assert calls["count"] == 3
