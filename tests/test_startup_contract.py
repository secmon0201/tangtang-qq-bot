from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_full_start_orders_core_before_nonebot_and_napcat():
    script = source("scripts/start_all.ps1")

    core = script.index('start_gsuid_core.ps1") -Background')
    nonebot = script.index('start.ps1")')
    napcat = script.index("Start-Process -FilePath $env:ComSpec")

    assert core < nonebot < napcat


def test_full_stop_preserves_guard_state_and_stops_tunnel_and_core():
    script = source("scripts/stop_all.ps1")

    assert 'stop_nte_tunnel.ps1") -PreserveGuardState' in script
    assert 'stop_gsuid_core.ps1")' in script
    assert script.index('stop_nte_tunnel.ps1")') < script.index('stop_gsuid_core.ps1")')


def test_tunnel_stop_can_preserve_the_user_guard_choice():
    script = source("scripts/stop_nte_tunnel.ps1")

    assert "[switch]$PreserveGuardState" in script
    assert "if (-not $PreserveGuardState)" in script


def test_start_and_restart_wrappers_restore_tunnel_and_watchdog():
    for wrapper in ("start_bot_and_napcat.bat", "restart_bot_and_napcat.bat"):
        script = source(wrapper)
        assert "nte_tunnel_disabled.flag" in script
        assert "scripts\\start_nte_tunnel.ps1" in script
        assert "scripts\\watch_napcat.ps1" in script
