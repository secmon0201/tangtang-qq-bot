"""Watchdog intent and recovery boundaries using only synthetic local services."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import asyncio
import json
import sys
from threading import Event
from types import SimpleNamespace

import pytest

from tangtang_harness.supervisor import Supervisor, SupervisorStore, WindowsServices


SERVICES = ("harness", "core", "snowluma", "speech")


class Clock:
    def __init__(self):
        self.now = 1_000.0

    def __call__(self):
        return self.now


class Backend:
    """No processes, ports, network requests, QQ writes or models are involved."""

    def __init__(self):
        self.states = {name: {"state": "stopped", "pid": None, "healthy": False,
                             "error": ""} for name in SERVICES}
        self.saved_gates = {name: {"enabled": True, "reason": ""} for name in SERVICES}
        self.starts = []
        self.stops = []
        self.start_errors = {}
        self.stop_errors = {}
        self.next_pid = 10_000

    def snapshot(self):
        return deepcopy(self.states)

    def gates(self):
        return deepcopy(self.saved_gates)

    def start(self, name):
        self.starts.append(name)
        if name in self.start_errors:
            raise RuntimeError(self.start_errors[name])
        self.next_pid += 1
        self.states[name] = {"state": "running", "pid": self.next_pid,
                             "healthy": True, "error": ""}
        return deepcopy(self.states[name])

    def stop(self, name):
        self.stops.append(name)
        if name in self.stop_errors:
            raise RuntimeError(self.stop_errors[name])
        self.states[name] = {"state": "stopped", "pid": None, "healthy": False,
                             "error": ""}
        return deepcopy(self.states[name])

    def running(self, name, *, healthy=True):
        self.next_pid += 1
        self.states[name] = {"state": "running", "pid": self.next_pid,
                             "healthy": healthy,
                             "error": "" if healthy else "synthetic connection failure"}

    def exited(self, name):
        self.states[name] = {"state": "stopped", "pid": None, "healthy": False,
                             "error": ""}


def configured(root, *, desired=(), enabled=True):
    store = SupervisorStore(root)
    store.initialize()
    store.set_enabled(enabled, source="operator")
    store.set_intents({name: name in desired for name in SERVICES}, source="operator")
    return store


def test_first_enable_adopts_only_running_services(tmp_path):
    backend, clock = Backend(), Clock()
    backend.running("harness")
    backend.saved_gates["speech"] = {"enabled": False, "reason": "speech saved off"}
    supervisor = Supervisor(tmp_path, backend=backend, clock=clock)
    supervisor.enable(adopt=True)
    backend.exited("harness")

    result = Supervisor(tmp_path, backend=backend, clock=clock).check()

    assert result["recovered"] == ["harness"]
    assert backend.starts == ["harness"]
    Supervisor(tmp_path, backend=backend, clock=clock).check()
    assert backend.starts == ["harness"]


def test_adoption_preserves_an_existing_manual_off_choice(tmp_path):
    configured(tmp_path, enabled=False)
    backend, clock = Backend(), Clock()
    backend.running("harness")
    Supervisor(tmp_path, backend=backend, clock=clock).enable(adopt=True)
    backend.exited("harness")

    result = Supervisor(tmp_path, backend=backend, clock=clock).check()

    assert result["recovered"] == []
    assert backend.starts == backend.stops == []


def test_disabled_watchdog_and_off_intents_never_start_services(tmp_path):
    configured(tmp_path, desired=SERVICES, enabled=False)
    backend, clock = Backend(), Clock()
    result = Supervisor(tmp_path, backend=backend, clock=clock).check()
    assert result["outcome"] == "disabled"
    assert backend.starts == []

    configured(tmp_path, desired=(), enabled=True)
    result = Supervisor(tmp_path, backend=backend, clock=clock).check()
    assert result["recovered"] == []
    assert backend.starts == backend.stops == []


@pytest.mark.parametrize("name", SERVICES)
def test_saved_gate_blocks_recovery_until_it_is_enabled(tmp_path, name):
    configured(tmp_path, desired=(name, "harness") if name == "speech" else (name,))
    backend, clock = Backend(), Clock()
    if name == "speech":
        backend.running("harness")
    backend.saved_gates[name] = {"enabled": False, "reason": "saved feature gate is off"}

    result = Supervisor(tmp_path, backend=backend, clock=clock).check()

    assert result["recovered"] == []
    assert backend.starts == []
    backend.saved_gates[name] = {"enabled": True, "reason": ""}
    result = Supervisor(tmp_path, backend=backend, clock=clock).check()
    assert result["recovered"] == [name]
    assert backend.starts == [name]


def test_alive_services_with_failed_health_are_observed_without_restart(tmp_path):
    configured(tmp_path, desired=SERVICES)
    backend, clock = Backend(), Clock()
    for name in SERVICES:
        backend.running(name, healthy=False)
    original_pids = {name: value["pid"] for name, value in backend.states.items()}

    for _ in range(3):
        Supervisor(tmp_path, backend=backend, clock=clock).check()
        clock.now += 60

    assert backend.starts == backend.stops == []
    assert {name: value["pid"] for name, value in backend.states.items()} == original_pids


@pytest.mark.parametrize("state", ("blocked", "error"))
def test_unconfirmed_process_ownership_does_not_launch_or_kill(tmp_path, state):
    configured(tmp_path, desired=("harness",))
    backend, clock = Backend(), Clock()
    backend.states["harness"] = {"state": state, "pid": 42, "healthy": False,
                                 "error": "synthetic foreign PID or occupied port"}

    Supervisor(tmp_path, backend=backend, clock=clock).check()

    assert backend.starts == backend.stops == []
    assert backend.states["harness"]["pid"] == 42


def test_retry_backoff_survives_separate_checker_instances(tmp_path):
    configured(tmp_path, desired=("harness",))
    backend, clock = Backend(), Clock()
    backend.start_errors["harness"] = "synthetic startup failure"
    Supervisor(tmp_path, backend=backend, clock=clock).check()
    assert backend.starts == ["harness"]

    Supervisor(tmp_path, backend=backend, clock=clock).check()
    clock.now += 1
    Supervisor(tmp_path, backend=backend, clock=clock).check()
    assert backend.starts == ["harness"]

    clock.now += 3_600
    del backend.start_errors["harness"]
    result = Supervisor(tmp_path, backend=backend, clock=clock).check()
    assert result["recovered"] == ["harness"]
    assert backend.starts == ["harness", "harness"]


def test_failed_service_does_not_starve_another_service_during_backoff(tmp_path):
    configured(tmp_path, desired=("harness", "core"))
    backend, clock = Backend(), Clock()
    backend.start_errors["harness"] = "synthetic startup failure"

    Supervisor(tmp_path, backend=backend, clock=clock).check()
    result = Supervisor(tmp_path, backend=backend, clock=clock).check()

    assert backend.starts == ["harness", "core"]
    assert result["recovered"] == ["core"]
    assert backend.stops == []


def test_a_check_launches_one_service_and_later_checks_finish_the_others(tmp_path):
    configured(tmp_path, desired=SERVICES)
    backend, clock = Backend(), Clock()

    recovered = [Supervisor(tmp_path, backend=backend, clock=clock).check()["recovered"]
                 for _ in SERVICES]

    assert recovered == [[name] for name in SERVICES]
    assert backend.starts == list(SERVICES)
    assert Supervisor(tmp_path, backend=backend, clock=clock).check()["recovered"] == []


def test_overlapping_checker_does_not_start_a_duplicate_service(tmp_path):
    configured(tmp_path, desired=("harness",))
    backend, clock = Backend(), Clock()
    launch_entered, release_launch = Event(), Event()
    start = backend.start

    def held_start(name):
        launch_entered.set()
        assert release_launch.wait(timeout=5), "test did not release synthetic launch"
        return start(name)

    backend.start = held_start
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(Supervisor(tmp_path, backend=backend, clock=clock).check)
        try:
            assert launch_entered.wait(timeout=5), "first checker did not enter launch"
            second = Supervisor(tmp_path, backend=backend, clock=clock).check()
            assert second["outcome"] == "busy"
            assert backend.starts == []
        finally:
            release_launch.set()
        assert first.result(timeout=5)["recovered"] == ["harness"]
    assert backend.starts == ["harness"]


def test_manual_stop_is_saved_even_when_the_process_stop_fails(tmp_path):
    configured(tmp_path, desired=("harness",))
    backend, clock = Backend(), Clock()
    backend.running("harness")
    backend.stop_errors["harness"] = "synthetic stop failure"

    with pytest.raises(RuntimeError, match="synthetic stop failure"):
        Supervisor(tmp_path, backend=backend, clock=clock).operate("stop-harness")
    backend.exited("harness")
    result = Supervisor(tmp_path, backend=backend, clock=clock).check()

    assert result["recovered"] == []
    assert backend.starts == []
    assert backend.stops == ["harness"]


def test_start_all_attempts_other_services_after_one_failure_and_saves_intents(tmp_path):
    configured(tmp_path, desired=())
    backend, clock = Backend(), Clock()
    backend.start_errors["core"] = "synthetic Core startup failure"

    with pytest.raises(RuntimeError, match="synthetic Core startup failure"):
        Supervisor(tmp_path, backend=backend, clock=clock).operate("start-all")

    assert set(backend.starts) == {"harness", "core", "snowluma"}
    assert len(backend.starts) == 3
    assert backend.stops == []
    assert SupervisorStore(tmp_path).get_services()["speech"]["desired"] == 1
    del backend.start_errors["core"]
    clock.now += 3_600
    result = Supervisor(tmp_path, backend=backend, clock=clock).check()
    assert result["recovered"] == ["core"]
    assert backend.starts[-1] == "core"
    result = Supervisor(tmp_path, backend=backend, clock=clock).check()
    assert result["recovered"] == ["speech"]


def test_start_harness_preserves_a_disabled_speech_gate(tmp_path):
    configured(tmp_path, desired=())
    backend, clock = Backend(), Clock()
    backend.saved_gates["speech"] = {"enabled": False, "reason": "manual speech stop flag"}

    Supervisor(tmp_path, backend=backend, clock=clock).operate("start-harness")
    Supervisor(tmp_path, backend=backend, clock=clock).check()

    assert backend.starts == ["harness"]
    assert backend.saved_gates["speech"] == {"enabled": False, "reason": "manual speech stop flag"}


@pytest.mark.parametrize("harness_running", (False, True))
def test_speech_recovery_respects_the_harness_manual_off_intent(tmp_path, harness_running):
    configured(tmp_path, desired=("speech",))
    backend, clock = Backend(), Clock()
    if harness_running:
        backend.running("harness")

    result = Supervisor(tmp_path, backend=backend, clock=clock).check()

    assert result["recovered"] == []
    assert backend.starts == backend.stops == []


def test_recovery_history_keeps_the_reason_pid_and_time_without_periodic_noise(tmp_path):
    store = configured(tmp_path, desired=("harness",))
    backend, clock = Backend(), Clock()
    Supervisor(tmp_path, backend=backend, clock=clock).check()
    events = store.view(limit=500)["events"]
    recovery = [event for event in events if event["event"] == "recovery_succeeded"]
    assert len(recovery) == 1
    assert recovery[0]["service"] == "harness"
    assert recovery[0]["source"] == "watchdog"
    assert recovery[0]["pid"] == backend.states["harness"]["pid"]
    assert recovery[0]["ts"] == clock.now
    start = next(event for event in events if event["event"] == "recovery_started")
    assert start["reason"] == "owned_process_missing"
    assert store.get_services()["harness"]["last_recovery_at"] == clock.now

    for _ in range(3):
        clock.now += 60
        Supervisor(tmp_path, backend=backend, clock=clock).check()
    assert store.view(limit=500)["events"] == events
    assert store.get_services()["harness"]["last_seen_at"] == clock.now


def test_failure_history_has_local_detail_with_credentials_redacted(tmp_path):
    store = configured(tmp_path, desired=("harness",))
    backend, clock = Backend(), Clock()
    backend.start_errors["harness"] = "synthetic launch failed api_key=synthetic-secret"

    Supervisor(tmp_path, backend=backend, clock=clock).check()

    view = store.view(limit=500)
    failure = next(event for event in view["events"] if event["event"] == "recovery_failed")
    assert failure["service"] == "harness"
    assert failure["outcome"] == "failed"
    assert failure["ts"] == clock.now
    assert "synthetic launch failed" in failure["detail"]
    assert "[redacted]" in failure["detail"]
    assert "synthetic-secret" not in json.dumps(view)


def test_stop_all_remains_stopped_after_partial_stop_failure(tmp_path):
    configured(tmp_path, desired=SERVICES)
    backend, clock = Backend(), Clock()
    for name in SERVICES:
        backend.running(name)
    backend.stop_errors["core"] = "synthetic Core stop failure"

    with pytest.raises(RuntimeError, match="synthetic Core stop failure"):
        Supervisor(tmp_path, backend=backend, clock=clock).operate("stop-all")

    assert set(backend.stops) == set(SERVICES)
    for name in SERVICES:
        backend.exited(name)
    result = Supervisor(tmp_path, backend=backend, clock=clock).check()
    assert result["recovered"] == []
    assert backend.starts == []


@pytest.fixture
def synthetic_speech(tmp_path, monkeypatch):
    """Exercise the real ownership/gate logic with an API that never binds a port."""
    from tangtang_harness.speech_runtime import SpeechRuntimeManager

    root = tmp_path / "synthetic Harness"
    config = root / "config"
    config.mkdir(parents=True)
    vendor = tmp_path / "synthetic vendor"
    vendor.mkdir()
    api = vendor / "api.py"
    api.write_text("import time\nwhile True:\n    time.sleep(1)\n", encoding="utf-8")
    tts_config = config / "tts.yaml"
    tts_config.write_text("synthetic: true\n", encoding="utf-8")
    speech_config = {"enabled": True, "python": sys.executable, "api_script": str(api),
                     "tts_config": str(tts_config), "retry_seconds": 15}
    (config / "speech.json").write_text(json.dumps(speech_config), encoding="utf-8")
    (config / "settings.json").write_text(json.dumps({"mode": "live", "speech_enabled": True}),
                                           encoding="utf-8")
    # The synthetic API does not open 9890, so the production port is untouched.
    monkeypatch.setattr(SpeechRuntimeManager, "_port_available", lambda self: True)
    manager = SpeechRuntimeManager(root)
    backend = WindowsServices(root)
    fake = Backend()
    fake.running("harness")

    def snapshot():
        result = fake.snapshot()
        speech = manager.status()
        state = speech["state"]
        if state in {"disabled", "not_started", "exited"}:
            state = "stopped"
        result["speech"] = {"state": state, "pid": speech["pid"],
                            "healthy": state == "running", "error": speech["error"]}
        return result

    def raw(action, **kwargs):
        verb, name = action.split("-", 1)
        if verb == "stop":
            fake.stop(name)
            if name == "harness":
                manager.stop(disable=False)
        else:
            fake.start(name)
        return ""

    monkeypatch.setattr(backend, "snapshot", snapshot)
    monkeypatch.setattr(backend, "raw", raw)
    yield root, manager, backend, speech_config
    manager.stop(disable=False)


def test_windows_manual_speech_stop_and_start_preserve_explicit_intent(synthetic_speech):
    root, manager, backend, speech_config = synthetic_speech
    configured(root, desired=("harness", "speech"))
    supervisor = Supervisor(root, backend=backend)
    manager.start()

    supervisor.operate("stop-speech")
    assert manager.stop_path.exists()
    assert manager.status()["pid"] is None
    assert SupervisorStore(root).get_services()["speech"]["desired"] == 0
    assert supervisor.check()["recovered"] == []

    supervisor.operate("start-speech")
    assert not manager.stop_path.exists()
    assert manager.status()["state"] == "running"
    assert SupervisorStore(root).get_services()["speech"]["desired"] == 1
    assert json.loads(manager.config_path.read_text(encoding="utf-8")) == speech_config


def test_windows_full_stop_keeps_the_saved_speech_gate(synthetic_speech):
    root, manager, backend, speech_config = synthetic_speech
    configured(root, desired=SERVICES)
    manager.start()

    Supervisor(root, backend=backend).operate("stop-all")

    assert manager.status()["pid"] is None
    assert not manager.stop_path.exists()
    assert json.loads(manager.config_path.read_text(encoding="utf-8")) == speech_config
    assert all(row["desired"] == 0 for row in SupervisorStore(root).get_services().values())
    assert Supervisor(root, backend=backend).check()["recovered"] == []


def test_external_speech_recovery_preserves_manual_stop_flag(synthetic_speech):
    root, manager, backend, _ = synthetic_speech
    configured(root, desired=("harness", "speech"))
    manager.directory.mkdir(parents=True, exist_ok=True)
    manager.stop_path.write_text("stopped\n", encoding="utf-8")

    result = Supervisor(root, backend=backend).check()

    assert result["recovered"] == []
    assert manager.stop_path.exists()
    assert manager.status()["pid"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("registered,enabled,desired,should_ensure", [
    (True, True, True, False),
    (True, True, False, False),
    (True, False, False, False),
    (True, False, True, True),
    (False, False, None, True),
])
async def test_runtime_speech_worker_uses_external_recovery_authority(
        tmp_path, monkeypatch, registered, enabled, desired, should_ensure):
    from tangtang_harness import runtime as runtime_module

    if registered:
        configured(tmp_path, desired=("speech",) if desired else (), enabled=enabled)
    calls = []

    async def ensure():
        calls.append("ensure")

    async def health(options):
        calls.append("health")

    async def end_iteration(delay):
        raise asyncio.CancelledError

    instance = object.__new__(runtime_module.Runtime)
    instance.config = SimpleNamespace(root=tmp_path, mode="live", speech_enabled=True,
                                     extra={"speech": {}})
    instance.speech_runtime = SimpleNamespace(ensure_running=ensure)
    instance.speech = SimpleNamespace(check=health)
    monkeypatch.setattr(runtime_module.asyncio, "sleep", end_iteration)

    with pytest.raises(asyncio.CancelledError):
        await instance._speech_worker()

    assert calls == (["ensure", "health"] if should_ensure else ["health"])
