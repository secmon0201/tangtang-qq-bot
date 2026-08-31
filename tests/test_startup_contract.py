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


def test_full_start_and_stop_include_every_project_web_tunnel():
    start = source("scripts/start_all.ps1")
    stop = source("scripts/stop_all.ps1")

    for script_name in (
        "start_global_announcement_tunnel.ps1",
        "start_operator_web_tunnel.ps1",
    ):
        assert script_name in start
    for script_name in (
        "stop_tangtang_named_tunnel.ps1",
        "stop_global_announcement_tunnel.ps1",
        "stop_operator_web_tunnel.ps1",
    ):
        assert script_name in stop

    assert "start_tangtang_named_tunnel.ps1" in start
    assert "data\\cloudflared\\tangtang-web.yml" in start


def test_full_stop_preserves_guard_state_and_stops_tunnel_and_core():
    script = source("scripts/stop_all.ps1")

    assert 'stop_nte_tunnel.ps1") -PreserveGuardState' in script
    assert 'stop_gsuid_core.ps1")' in script
    assert script.index('stop_nte_tunnel.ps1")') < script.index('stop_gsuid_core.ps1")')


def test_stopping_core_waits_for_the_process_to_exit_before_a_restart():
    script = source("scripts/stop_gsuid_core.ps1")

    assert "had already stopped" in script
    assert "Wait-Process" in script
    assert "did not stop within 15 seconds" in script


def test_tunnel_stop_can_preserve_the_user_guard_choice():
    script = source("scripts/stop_nte_tunnel.ps1")

    assert "[switch]$PreserveGuardState" in script
    assert "if (-not $PreserveGuardState)" in script


def test_start_and_restart_wrappers_restore_tunnel_and_watchdog():
    for wrapper in ("启动工具/01-启动全部.bat", "启动工具/02-重启全部.bat"):
        script = source(wrapper)
        assert 'for %%I in ("%~dp0..") do set "ROOT=%%~fI"' in script
        assert "nte_tunnel_disabled.flag" in script
        assert "scripts\\start_nte_tunnel.ps1" in script
        assert "scripts\\watch_napcat.ps1" in script
        assert "data\\cloudflared\\tangtang-web.yml" in script


def test_tunnel_uses_http2_and_watchdog_requires_an_edge_connection():
    tunnel = source("scripts/start_nte_tunnel.ps1")
    watchdog = source("scripts/watch_napcat.ps1")

    assert '"--protocol", "http2"' in tunnel
    assert "Get-NetTCPConnection -OwningProcess" in watchdog
    assert "$edgeConnections.Count -gt 0" in watchdog
    assert "tangtang_web_gateway.py" in watchdog
    assert "start_tangtang_named_tunnel.ps1" in watchdog


def test_named_tunnel_starts_gateway_with_bounded_resource_limits():
    script = source("scripts/start_tangtang_named_tunnel.ps1")

    assert "[int]$GatewayMaxConcurrency = 32" in script
    assert "[int]$GatewayClientTimeoutSeconds = 60" in script
    assert "[int]$GatewayMaxRequestMB = 32" in script
    assert "'--max-concurrency', $GatewayMaxConcurrency" in script
    assert "'--client-timeout-seconds', $GatewayClientTimeoutSeconds" in script
    assert "'--max-request-mb', $GatewayMaxRequestMB" in script
    assert "Gateway limits must be positive integers" in script

    configure = source("scripts/configure_tangtang_named_tunnel.ps1")
    assert "[string]$ShortHostname = 's.secmon.cn'" in configure
    assert "tunnel route dns --overwrite-dns $TunnelId $candidateHostname" in configure
    assert '"  - hostname: $ShortHostname"' in configure


def test_root_contains_no_scattered_batch_shortcuts():
    assert list(ROOT.glob("*.bat")) == []
    shortcuts = {path.name for path in (ROOT / "启动工具").glob("*.bat")}
    assert shortcuts == {
        "01-启动全部.bat",
        "02-重启全部.bat",
        "03-关闭全部.bat",
        "11-仅重启机器人.bat",
        "21-启动异环登录隧道.bat",
        "22-关闭异环登录隧道.bat",
        "23-设置异环登录地址.bat",
        "24-启动公告网页隧道.bat",
        "25-关闭公告网页隧道.bat",
        "26-启动运营网页隧道.bat",
        "27-关闭运营网页隧道.bat",
        "31-启动守护程序.bat",
        "32-关闭守护程序.bat",
    }


def test_nonebot_background_logs_use_utf8():
    script = source("scripts/start.ps1")
    assert '$env:PYTHONUTF8 = "1"' in script
    assert '$env:PYTHONIOENCODING = "utf-8"' in script


def test_batch_shortcuts_use_crlf_line_endings():
    for path in (ROOT / "启动工具").glob("*.bat"):
        content = path.read_bytes()
        assert b"\r\n" in content
        assert b"\n" not in content.replace(b"\r\n", b"")
