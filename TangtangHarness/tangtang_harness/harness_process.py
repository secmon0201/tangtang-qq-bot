"""Ownership checks for the TangtangHarness process only."""
from __future__ import annotations

import json
from pathlib import Path

import psutil

from .speech_runtime import SpeechRuntimeError, SpeechRuntimeManager


class HarnessProcessError(RuntimeError):
    pass


def record_path(root: Path) -> Path:
    return Path(root).resolve() / "data" / "process.json"


def read_record(root: Path) -> dict | None:
    path = record_path(root)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HarnessProcessError(f"无法读取 Harness 进程记录：{path}") from exc


def owned_process(root: Path, record: dict | None = None) -> psutil.Process | None:
    root = Path(root).resolve()
    record = record if record is not None else read_record(root)
    if not record:
        return None
    try:
        process = psutil.Process(int(record["pid"]))
        if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
            return None
        command = process.cmdline()
        same_root_command = (Path(record["root"]).resolve() == root
                             and "-m" in command and "tangtang_harness" in command
                             and str(root) in " ".join(command))
        # Records written by the first Harness build had no creation/cmdline fields;
        # command plus root still identifies that process without killing an arbitrary PID.
        same = same_root_command if "created" not in record or "cmdline" not in record else (
            same_root_command and abs(process.create_time() - float(record["created"])) < 0.01
            and command == record["cmdline"])
        if not same:
            raise HarnessProcessError("PID 已由其他进程使用；未停止。")
        return process
    except psutil.NoSuchProcess:
        return None
    except (KeyError, TypeError, ValueError, psutil.AccessDenied) as exc:
        raise HarnessProcessError("无法确认 Harness 进程归属；未停止。") from exc


def status(root: Path) -> dict:
    root = Path(root).resolve()
    record = read_record(root)
    try:
        process = owned_process(root, record)
    except HarnessProcessError as exc:
        return {"state": "error", "pid": record.get("pid") if record else None, "error": str(exc)}
    if process is None:
        return {"state": "stopped", "pid": None, "record": bool(record),
                "error": "" if not record else "进程记录已过期；可直接启动。"}
    return {"state": "running", "pid": process.pid, "created": process.create_time(),
            "port": record.get("port"), "root": str(root), "error": ""}


def stop(root: Path, *, timeout: float = 3.0) -> dict:
    """Stop the Harness and its own descendants, including optional speech."""
    root = Path(root).resolve()
    process = owned_process(root)
    # Speech can outlive an exited Harness. App shutdown preserves its saved gate.
    try:
        SpeechRuntimeManager(root).stop(disable=False)
    except SpeechRuntimeError:
        pass
    if process is None:
        record_path(root).unlink(missing_ok=True)
        return status(root)
    try:
        members = [process, *process.children(recursive=True)]
        for member in reversed(members):
            try:
                member.terminate()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(members, timeout=timeout)
        for member in alive:
            try:
                member.kill()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(alive, timeout=1)
        if alive:
            raise HarnessProcessError("Harness 自有进程未退出；保留进程记录。")
    finally:
        if not psutil.pid_exists(process.pid):
            record_path(root).unlink(missing_ok=True)
    return status(root)
