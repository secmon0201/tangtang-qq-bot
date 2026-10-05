"""Optional GPT-SoVITS process owned only by this Harness installation."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
import json
import math
import msvcrt
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time

import psutil

from .config import DEFAULT_ROOT


HOST, PORT = "127.0.0.1", 9890
SOURCE_SUFFIXES = {".py", ".pyi", ".json", ".yaml", ".yml", ".txt", ".rep", ".csv", ".md", ".h", ".cpp", ".cu"}
SOURCE_EXCLUDES = {".git", "__pycache__", "pretrained_models", "G2PWModel", "uvr5_weights"}


class SpeechRuntimeError(RuntimeError):
    pass


class SpeechRuntimeManager:
    """Start is explicit; ensure_running obeys the saved stop choice and retries slowly.

    A running process is not a readiness claim. SpeechClient owns API health checks
    and synthesis. The external interpreter, API source and weights are inputs;
    source-side caches and GPT-SoVITS config writes happen in our runtime copy.
    """

    def __init__(self, root: Path = DEFAULT_ROOT):
        self.root = Path(root).resolve()
        self.config_path = self.root / "config" / "speech.json"
        self.directory = self.root / "runtime" / "speech-runtime"
        self.source = self.directory / "source"
        self.record_path = self.directory / "process.json"
        self.stop_path = self.directory / "stopped.flag"
        self.error = ""
        self._retry_at = 0.0
        self._ensure_lock = asyncio.Lock()
        self._child: subprocess.Popen | None = None

    def _config(self) -> dict:
        if not self.config_path.exists():
            return {"enabled": False, "retry_seconds": 15}
        value = json.loads(self.config_path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict):
            raise SpeechRuntimeError("config/speech.json 必须是 JSON 对象")
        return {"enabled": False, "retry_seconds": 15, **value}

    def _inputs(self, config: dict) -> tuple[Path, Path, Path]:
        if config.get("host", HOST) != HOST or config.get("port", PORT) != PORT:
            raise SpeechRuntimeError("独立语音只能监听 127.0.0.1:9890")
        paths = []
        for name in ("python", "api_script", "tts_config"):
            value = str(config.get(name) or "")
            path = Path(value)
            if not value or not path.is_absolute() or not path.is_file():
                raise SpeechRuntimeError(f"speech.{name} 必须是已存在文件的绝对路径")
            paths.append(path.resolve())
        if not paths[2].is_relative_to(self.root):
            raise SpeechRuntimeError("tts_config 必须位于 TangtangHarness 内，不能使用旧运行时的 YAML")
        return tuple(paths)

    def _record(self) -> dict | None:
        if not self.record_path.exists():
            return None
        return json.loads(self.record_path.read_text(encoding="utf-8"))

    def _owned_process(self, record: dict | None) -> psutil.Process | None:
        if record is None:
            return None
        try:
            process = psutil.Process(int(record["pid"]))
            if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
                return None
            same_identity = (
                Path(record["root"]).resolve() == self.root
                and abs(process.create_time() - float(record["created"])) < 0.01
                and process.cmdline() == record["cmdline"]
                and str(Path(record["script"]).resolve()) in process.cmdline()
                and Path(record["script"]).resolve().is_relative_to(self.source)
            )
            if not same_identity:
                raise SpeechRuntimeError("语音 PID 的创建时间或命令不匹配；未操作该进程")
            return process
        except psutil.NoSuchProcess:
            return None
        except (KeyError, TypeError, ValueError, psutil.AccessDenied) as exc:
            raise SpeechRuntimeError("无法确认语音进程归属；未操作该进程") from exc

    def status(self) -> dict:
        """Read process status without probing the API or creating files."""
        result = {"enabled": False, "state": "disabled", "pid": None,
                  "endpoint": f"http://{HOST}:{PORT}", "error": self.error,
                  "next_retry_seconds": round(max(0.0, self._retry_at - time.monotonic()), 2),
                  "log": str(self.directory / "server.log")}
        try:
            config = self._config()
            result["enabled"] = bool(config["enabled"]) and not self.stop_path.exists()
            record = self._record()
            process = self._owned_process(record)
            if process is not None:
                result.update(state="running", pid=process.pid)
            elif self.stop_path.exists():
                result["state"] = "stopped"
            elif result["enabled"]:
                result["state"] = "exited" if record else "not_started"
                if self._child is not None and self._child.poll() is not None:
                    result["error"] = self.error or f"语音进程已退出，exit={self._child.returncode}；查看 server.log"
        except (OSError, ValueError, SpeechRuntimeError) as exc:
            result.update(state="error", error=str(exc))
        return result

    @contextmanager
    def _operation(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / "operation.lock").open("a+b") as handle:
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise SpeechRuntimeError("另一项语音启停操作正在进行") from exc
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

    def _copy_source(self, api_script: Path) -> Path:
        self.source.mkdir(parents=True, exist_ok=True)
        copied_api = self.source / api_script.name
        shutil.copy2(api_script, copied_api)
        for package in ("GPT_SoVITS", "tools"):
            package_source = api_script.parent / package
            if not package_source.is_dir():
                continue
            for directory, subdirs, files in os.walk(package_source, followlinks=False):
                subdirs[:] = [name for name in subdirs if name not in SOURCE_EXCLUDES
                              and not (Path(directory) / name).is_symlink()]
                for name in files:
                    source = Path(directory) / name
                    if source.is_symlink() or (source.suffix.lower() not in SOURCE_SUFFIXES
                                             and not source.name.startswith("LICENSE")):
                        continue
                    target = self.source / package / source.relative_to(package_source)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
        return copied_api

    def _environment(self) -> dict[str, str]:
        env = dict(os.environ)
        cache = self.directory / "cache"
        output = self.directory / "output"
        temp = self.directory / "temp"
        for directory in (cache, output, temp, cache / "nltk"):
            directory.mkdir(parents=True, exist_ok=True)
        env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1",
                    "PYTHONPATH": os.pathsep.join(map(str, (self.source, self.source / "GPT_SoVITS",
                                                          self.source / "GPT_SoVITS" / "eres2net"))),
                    "TEMP": str(temp), "TMP": str(temp), "TMPDIR": str(temp),
                    "HF_HOME": str(cache / "huggingface"), "HF_HUB_CACHE": str(cache / "huggingface" / "hub"),
                    "HUGGINGFACE_HUB_CACHE": str(cache / "huggingface" / "hub"),
                    "TORCH_HOME": str(cache / "torch"), "TORCH_EXTENSIONS_DIR": str(cache / "torch-extensions"),
                    "XDG_CACHE_HOME": str(cache), "NLTK_DATA": str(cache / "nltk"),
                    "NUMBA_CACHE_DIR": str(cache / "numba"), "MPLCONFIGDIR": str(cache / "matplotlib"),
                    "TANGTANG_SPEECH_OUTPUT": str(output)})
        return env

    def _port_available(self) -> bool:
        with socket.socket() as probe:
            try:
                probe.bind((HOST, PORT))
            except OSError:
                return False
        return True

    def _start(self, explicit: bool) -> dict:
        config = self._config()
        if not config["enabled"] or (not explicit and self.stop_path.exists()):
            return self.status()
        with self._operation():
            config = self._config()
            if not config["enabled"] or (not explicit and self.stop_path.exists()):
                return self.status()
            if self._owned_process(self._record()) is not None:
                if explicit:
                    self.stop_path.unlink(missing_ok=True)
                return self.status()
            python, api_script, tts_config = self._inputs(config)
            if not self._port_available():
                raise SpeechRuntimeError("9890 已被其他进程占用；未停止占用进程")
            copied_api = self._copy_source(api_script)
            command = [str(python), str(copied_api), "-a", HOST, "-p", str(PORT), "-c", str(tts_config)]
            environment = self._environment()
            bert_path = str(config.get("bert_path") or "")
            if bert_path:
                environment["bert_path"] = str(Path(bert_path).resolve())
            environment.setdefault("OMP_NUM_THREADS", "8")
            environment.setdefault("MKL_NUM_THREADS", "8")
            with (self.directory / "server.log").open("ab") as log:
                child = subprocess.Popen(command, cwd=self.source, env=environment, stdin=subprocess.DEVNULL,
                                         stdout=log, stderr=subprocess.STDOUT,
                                         creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
            self._child = child
            process = psutil.Process(child.pid)
            try:
                record = {"root": str(self.root), "pid": child.pid, "created": process.create_time(),
                          "cmdline": process.cmdline(), "script": str(copied_api)}
            except psutil.NoSuchProcess as exc:
                child.wait(timeout=3)
                raise SpeechRuntimeError(f"语音进程启动后退出，exit={child.returncode}；查看 server.log") from exc
            self.record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            if explicit:
                self.stop_path.unlink(missing_ok=True)
            self.error = ""
            return self.status()

    def start(self) -> dict:
        """Launch configured, enabled speech; explicit start clears a previous stop."""
        try:
            return self._start(explicit=True)
        except (OSError, ValueError, SpeechRuntimeError) as exc:
            self.error = str(exc)
            raise SpeechRuntimeError(str(exc)) from exc

    def stop(self, *, disable: bool = True) -> dict:
        """Stop only the recorded process tree; disable=False is for app shutdown."""
        with self._operation():
            if disable:
                self.stop_path.write_text("stopped\n", encoding="utf-8")
            process = self._owned_process(self._record())
            if process is not None:
                try:
                    children = process.children(recursive=True)
                except psutil.NoSuchProcess:
                    children = []
                owned = [process, *children]
                for member in reversed(owned):
                    try:
                        member.terminate()
                    except psutil.NoSuchProcess:
                        pass
                _, alive = psutil.wait_procs(owned, timeout=3)
                for member in alive:
                    try:
                        member.kill()
                    except psutil.NoSuchProcess:
                        pass
                _, alive = psutil.wait_procs(alive, timeout=1)
                if alive:
                    raise SpeechRuntimeError("自有语音进程未退出；保留进程记录")
            if self._child is not None:
                self._child.poll()
            self.record_path.unlink(missing_ok=True)
            self.error = ""
            self._retry_at = 0.0
            return self.status()

    async def ensure_running(self) -> dict:
        """Call from background health work; never wait on a QQ event path."""
        if self._ensure_lock.locked() or time.monotonic() < self._retry_at:
            return self.status()
        async with self._ensure_lock:
            try:
                config = self._config()
                if not config["enabled"] or self.stop_path.exists():
                    return self.status()
                interval = float(config["retry_seconds"])
                if not math.isfinite(interval) or interval <= 0:
                    raise SpeechRuntimeError("speech.retry_seconds 必须是大于 0 的有限秒数")
                self._retry_at = time.monotonic() + interval
                launch = asyncio.create_task(asyncio.to_thread(self._start, False))
                try:
                    return await asyncio.shield(launch)
                except asyncio.CancelledError:
                    # Finish the owned launch before app shutdown stops its process.
                    await asyncio.gather(launch, return_exceptions=True)
                    raise
            except (OSError, ValueError, SpeechRuntimeError) as exc:
                self.error = str(exc)
                self._retry_at = max(self._retry_at, time.monotonic() + 15)
                return self.status()


def main() -> int:
    parser = argparse.ArgumentParser(description="TangtangHarness 独立语音运行时；默认关闭")
    parser.add_argument("operation", choices=("start", "stop", "status"))
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    manager = SpeechRuntimeManager(args.root)
    try:
        result = getattr(manager, args.operation)()
    except (OSError, ValueError, SpeechRuntimeError) as exc:
        result = {**manager.status(), "state": "error", "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(result["state"] == "error")


if __name__ == "__main__":
    raise SystemExit(main())
