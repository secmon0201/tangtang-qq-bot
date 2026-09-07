from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import time


ROOT = Path(__file__).resolve().parents[1]


def source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_full_start_orders_core_before_nonebot_and_qq_transport():
    script = source("scripts/start_all.ps1")

    core = script.index("start_gsuid_core.ps1') -Background")
    nonebot = script.index("start.ps1')")
    qq_transport = script.index("start_qq_transport.ps1')")

    assert core < nonebot < qq_transport


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

    assert "stop_nte_tunnel.ps1') -PreserveGuardState" in script
    assert "stop_gsuid_core.ps1')" in script
    assert script.index("stop_nte_tunnel.ps1')") < script.index("stop_gsuid_core.ps1')")
    assert "stop_snowluma.ps1" in script


def test_stopping_core_waits_for_the_process_to_exit_before_a_restart():
    script = source("scripts/stop_gsuid_core.ps1")

    assert "had already stopped" in script
    assert "Wait-Process" in script
    assert "did not stop within 15 seconds" in script


def test_nonebot_restart_waits_for_exit_and_retries_locked_log_archives():
    stop = source("scripts/stop.ps1")
    start = source("scripts/start.ps1")

    assert "Wait-Process -Id $processId -Timeout 15" in stop
    assert "Bot PID $processId did not stop within 15 seconds" in stop
    assert "for ($attempt = 0; $attempt -lt 60" in start
    assert "Move-Item -LiteralPath $path -Destination $destination -Force -ErrorAction Stop" in start
    assert "if ($attempt -eq 59) { throw }" in start
    assert "Start-Sleep -Milliseconds 250" in start


def test_tunnel_stop_can_preserve_the_user_guard_choice():
    script = source("scripts/stop_nte_tunnel.ps1")

    assert "[switch]$PreserveGuardState" in script
    assert "if (-not $PreserveGuardState)" in script


def test_start_and_restart_wrappers_restore_tunnel_and_watchdog():
    start_wrapper = source("启动工具/01-启动全部.bat")
    assert 'for %%I in ("%~dp0..") do set "ROOT=%%~fI"' in start_wrapper
    assert "scripts\\start_all.ps1" in start_wrapper
    assert "scripts\\watch_qq_transport.ps1" not in start_wrapper

    startup = source("scripts/start_all.ps1")
    assert "nte_tunnel_disabled.flag" in startup
    assert "start_nte_tunnel.ps1" in startup
    assert "start_watchdog.ps1" in startup
    assert startup.index("start_watchdog.ps1") < startup.index("verify_full_stack.ps1")
    assert "data\\cloudflared\\tangtang-web.yml" in startup

    restart_wrapper = source("启动工具/02-重启全部.bat")
    assert 'for %%I in ("%~dp0..") do set "ROOT=%%~fI"' in restart_wrapper
    restart_script = source("scripts/restart_all.ps1")
    assert "scripts\\restart_all.ps1" in restart_wrapper
    assert restart_script.index("stop_all.ps1") < restart_script.index("start_all.ps1")


def test_full_stack_verifier_checks_transport_watchdog_and_error_log():
    verifier = source("scripts/verify_full_stack.ps1")

    assert "Test-OneBotConnectionOwnership" in verifier
    assert "watch_qq_transport.ps1" in verifier
    assert "last_check_completed_at" in verifier
    assert "GSUID_CORE_PORT" in verifier
    assert "bot.err.log" in verifier
    assert "Full stack is healthy and ready." in verifier


def test_tunnel_uses_http2_and_watchdog_requires_an_edge_connection():
    tunnel = source("scripts/start_nte_tunnel.ps1")
    watchdog = source("scripts/watch_qq_transport.ps1")

    assert '"--protocol", "http2"' in tunnel
    assert "Get-NetTCPConnection -OwningProcess" in watchdog
    assert "$edgeConnections.Count -gt 0" in watchdog
    assert "tangtang_web_gateway.py" in watchdog
    assert "start_tangtang_named_tunnel.ps1" in watchdog
    assert "Invoke-BoundedPowerShellScript" in watchdog
    assert "Wait-Process -Id $process.Id -Timeout $TimeoutSeconds" in watchdog
    assert '-WindowStyle Hidden -Wait' not in watchdog
    assert "scriptArguments = if (Test-Path -LiteralPath $namedConfig) { @('-SkipCoreRestart') }" in watchdog


def test_watchdog_records_a_completed_heartbeat_on_every_loop():
    watchdog = source("scripts/watch_qq_transport.ps1")

    assert "last_check_started_at" in watchdog
    assert "last_check_completed_at" in watchdog
    assert "$connections = @(" in watchdog
    assert "if ($listenerReady) { Get-OneBotClientConnections" in watchdog
    assert 'nte_tunnel_recovery_timed_out' in watchdog
    assert "finally {" in watchdog


def test_watchdog_waits_for_recovery_script_not_its_long_lived_children(tmp_path):
    powershell = shutil.which("powershell.exe")
    assert powershell is not None

    child_pid_path = tmp_path / "child.pid"
    probe = ROOT / "tests" / "fixtures" / "watchdog_bounded_probe.ps1"
    watchdog = ROOT / "scripts" / "watch_qq_transport.ps1"
    started = time.monotonic()
    completed = subprocess.run(
        [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(probe),
            "-WatchdogPath",
            str(watchdog),
            "-ChildPidPath",
            str(child_pid_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    elapsed = time.monotonic() - started
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    child_pid = int(child_pid_path.read_text(encoding="ascii").strip())

    try:
        assert result["Outcome"] == "completed"
        assert result["ExitCode"] == 0
        assert elapsed < 5
    finally:
        subprocess.run(
            [
                powershell,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f"Stop-Process -Id {child_pid} -Force -ErrorAction SilentlyContinue",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )


def test_named_tunnel_starts_gateway_with_bounded_resource_limits():
    script = source("scripts/start_tangtang_named_tunnel.ps1")

    assert "[int]$GatewayMaxConcurrency = 32" in script
    assert "[int]$GatewayClientTimeoutSeconds = 60" in script
    assert "[int]$GatewayMaxRequestMB = 32" in script
    assert "'--max-concurrency', $GatewayMaxConcurrency" in script
    assert "'--client-timeout-seconds', $GatewayClientTimeoutSeconds" in script
    assert "'--max-request-mb', $GatewayMaxRequestMB" in script
    assert "Gateway limits must be positive integers" in script
    assert "'set_wuwa_login_url.ps1'" in script
    assert "'GsUID.Core\\data\\XutheringWavesUID\\config.json'" in script

    wuwa_setter = source("scripts/set_wuwa_login_url.ps1")
    assert "$config.WavesLoginUrl.data = $url" in wuwa_setter
    assert "$config.WavesLoginUrlSelf.data = $true" in wuwa_setter

    configure = source("scripts/configure_tangtang_named_tunnel.ps1")
    assert "[string]$ShortHostname = ''" in configure
    assert "PUBLIC_SHORT_HOST" in configure
    assert "tunnel route dns --overwrite-dns $TunnelId $candidateHostname" in configure
    assert '"  - hostname: $ShortHostname"' in configure


def test_root_contains_no_scattered_batch_shortcuts():
    assert list(ROOT.glob("*.bat")) == []
    shortcuts = {path.name for path in (ROOT / "启动工具").glob("*.bat")}
    assert shortcuts == {
        "00-首次初始化设置.bat",
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
    assert '$env:PYTHONUNBUFFERED = "1"' in script
    assert '$env:PYTHONFAULTHANDLER = "1"' in script
    assert '@("-u", "-X", "faulthandler", "-m", "bot")' in script


def test_nonebot_background_start_archives_previous_logs_and_records_lifecycle():
    start = source("scripts/start.ps1")
    stop = source("scripts/stop.ps1")

    assert "function Archive-PreviousBotLogs" in start
    assert 'Join-Path $LogDir "history"' in start
    assert "Select-Object -Skip $KeepRuns" in start
    assert "logs_archived" in start
    assert "bot_started" in start
    assert "bot_stop_requested" in stop


def test_batch_shortcuts_use_crlf_line_endings():
    for path in (ROOT / "启动工具").glob("*.bat"):
        content = path.read_bytes()
        assert b"\r\n" in content
        assert b"\n" not in content.replace(b"\r\n", b"")
