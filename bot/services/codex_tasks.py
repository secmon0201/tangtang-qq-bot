from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from nonebot import logger

from bot.config import ROOT, settings
from bot.db import Database
from bot.services.codex_completion import notify_codex_completion


MAX_TASK_PROMPT_CHARS = 12_000
MAX_TASK_RESULT_CHARS = 24_000
MAX_PROCESS_ERROR_CHARS = 4_000


def task_title_from_prompt(prompt: str) -> str:
    """Make a compact task label without storing a second user-provided title."""
    normalized = " ".join(prompt.strip().split())
    return normalized[:36] + ("..." if len(normalized) > 36 else "")


def clipped_text(value: str, maximum: int) -> str:
    normalized = value.strip()
    if len(normalized) <= maximum:
        return normalized
    return normalized[: maximum - 16].rstrip() + "\n[内容已截断]"


def worker_prompt(task_id: int, title: str, prompt: str) -> str:
    """Add the invariant operational boundary to every remote-supervised turn."""
    return f"""你正在执行本机 QQ 超级管理员发起的 Codex 持续任务 #{task_id}：{title}。

工作目录固定为当前项目。只处理下方任务要求及其必要的代码、测试和文档；不要读取、输出或修改 .env、认证文件、令牌、密码、Cookie 或工作目录以外的文件。不要请求或尝试提升沙箱权限。不要把推理过程、工具日志或任何密钥放进最终回答。

完成时请用中文简要说明：完成的改动、验证结果、尚未解决的限制。若无法安全完成，请直接说明原因。

任务要求：
{prompt.strip()}
"""


def initial_command(command: Path, prompt: str, sandbox: str) -> list[str]:
    return [
        str(command),
        "exec",
        "--json",
        "--sandbox",
        sandbox,
        "--cd",
        str(ROOT),
        prompt,
    ]


def resume_command(command: Path, thread_id: str, prompt: str) -> list[str]:
    return [
        str(command),
        "exec",
        "resume",
        "--json",
        thread_id,
        prompt,
    ]


def parse_codex_event(raw: str) -> tuple[str | None, str | None]:
    """Return a new thread ID or final assistant text from one JSONL event."""
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        return None, None
    if not isinstance(event, dict):
        return None, None
    if event.get("type") == "thread.started" and isinstance(event.get("thread_id"), str):
        return event["thread_id"], None
    item = event.get("item")
    if (
        event.get("type") == "item.completed"
        and isinstance(item, dict)
        and item.get("type") == "agent_message"
        and isinstance(item.get("text"), str)
    ):
        return None, item["text"]
    return None, None


class CodexWorker:
    """Single local Codex runner; task rows provide durable queue and session state."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self._lock = asyncio.Lock()
        self._process: asyncio.subprocess.Process | None = None
        self._active_task_id: int | None = None

    @property
    def active_task_id(self) -> int | None:
        return self._active_task_id

    async def run_once(self) -> None:
        if not settings.codex_worker_enabled or self._lock.locked():
            return
        async with self._lock:
            turn = self.database.claim_next_codex_task_message()
            if turn is None:
                return
            await self._run_turn(turn)

    async def request_stop(self, task_id: int, *, cancel_all: bool = False) -> str:
        state = self.database.stop_codex_task(task_id, cancel_all=cancel_all)
        if self._active_task_id == int(task_id) and self._process is not None:
            if self._process.returncode is None:
                self._process.terminate()
        return state

    async def shutdown(self) -> None:
        """Stop an in-flight child before the parent NoneBot process exits."""
        if self._active_task_id is not None:
            self.database.stop_codex_task(self._active_task_id)
        if self._process is not None and self._process.returncode is None:
            self._process.terminate()

    async def _run_turn(self, turn: Any) -> None:
        task_id = int(turn["task_id"])
        message_id = int(turn["message_id"])
        prompt = str(turn["content"])
        if len(prompt) > MAX_TASK_PROMPT_CHARS:
            error = f"任务内容超过 {MAX_TASK_PROMPT_CHARS} 字符上限"
            self.database.finish_codex_task_message(message_id, "failed", error=error)
            await self._notify(task_id, str(turn["title"]), "failed", error)
            return
        command = settings.codex_worker_command
        if command is None:
            error = "Codex worker command is not configured"
            self.database.finish_codex_task_message(message_id, "failed", error=error)
            await self._notify(task_id, str(turn["title"]), "failed", error)
            return

        prepared_prompt = worker_prompt(task_id, str(turn["title"]), prompt)
        thread_id = str(turn["codex_thread_id"] or "").strip()
        args: Sequence[str]
        if thread_id:
            args = resume_command(command, thread_id, prepared_prompt)
        else:
            args = initial_command(command, prepared_prompt, settings.codex_worker_sandbox)

        self._active_task_id = task_id
        final_message = ""
        stderr_lines: list[str] = []
        timed_out = False
        try:
            self._process = await asyncio.create_subprocess_exec(
                *args,
                cwd=str(ROOT),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_task = asyncio.create_task(
                self._consume_stdout(self._process.stdout, task_id), name=f"codex-stdout-{task_id}"
            )
            stderr_task = asyncio.create_task(
                self._consume_stderr(self._process.stderr, stderr_lines), name=f"codex-stderr-{task_id}"
            )
            try:
                await asyncio.wait_for(
                    self._process.wait(), timeout=settings.codex_worker_timeout_seconds
                )
            except TimeoutError:
                timed_out = True
                if self._process.returncode is None:
                    self._process.terminate()
                await self._process.wait()
            final_message = await stdout_task
            await stderr_task
            current = self.database.codex_task(task_id)
            externally_stopped = current is not None and str(current["status"]) in {
                "stopping",
                "cancelled",
            }
            if externally_stopped:
                outcome = "cancelled"
                error = "超级管理员已停止此轮 Codex 执行"
                result = ""
            elif timed_out:
                outcome = "failed"
                error = f"执行超过 {settings.codex_worker_timeout_seconds} 秒超时，已终止。"
                result = ""
            elif self._process.returncode == 0:
                outcome = "completed"
                result = clipped_text(
                    final_message or "Codex 已完成，但没有返回可转发的最终文字。",
                    MAX_TASK_RESULT_CHARS,
                )
                error = ""
            else:
                outcome = "failed"
                diagnostic = "\n".join(stderr_lines).strip()
                error = clipped_text(
                    f"Codex 进程退出码 {self._process.returncode}。\n{diagnostic}".strip(),
                    MAX_PROCESS_ERROR_CHARS,
                )
                result = ""
            self.database.finish_codex_task_message(
                message_id, outcome, result=result, error=error
            )
            await self._notify(task_id, str(turn["title"]), outcome, result or error)
        except Exception as exc:
            logger.exception("Codex worker task {} failed before completion", task_id)
            error = clipped_text(f"Codex worker 启动失败：{exc}", MAX_PROCESS_ERROR_CHARS)
            self.database.finish_codex_task_message(message_id, "failed", error=error)
            await self._notify(task_id, str(turn["title"]), "failed", error)
        finally:
            self._process = None
            self._active_task_id = None

    async def _consume_stdout(
        self, stream: asyncio.StreamReader | None, task_id: int
    ) -> str:
        if stream is None:
            return ""
        final_message = ""
        while raw := await stream.readline():
            text = raw.decode("utf-8", "replace").strip()
            thread_id, message = parse_codex_event(text)
            if thread_id:
                self.database.set_codex_task_thread(task_id, thread_id)
            if message:
                final_message = message
        return final_message

    @staticmethod
    async def _consume_stderr(stream: asyncio.StreamReader | None, sink: list[str]) -> None:
        if stream is None:
            return
        collected = 0
        while raw := await stream.readline():
            if collected >= MAX_PROCESS_ERROR_CHARS:
                continue
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            remaining = MAX_PROCESS_ERROR_CHARS - collected
            sink.append(line[:remaining])
            collected += len(sink[-1])

    async def _notify(self, task_id: int, title: str, outcome: str, details: str) -> None:
        state = {"completed": "已完成", "failed": "失败", "cancelled": "已停止"}.get(
            outcome, outcome
        )
        message = f"Codex 持续任务 #{task_id} {state}：{title}。完整结果请展开合并转发查看。"
        try:
            await notify_codex_completion(message, details=details)
        except Exception:
            logger.exception("Codex worker could not send QQ notification for task {}", task_id)
