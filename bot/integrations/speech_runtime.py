"""Start/recover only the configured local speech process, with finite backoff."""
from __future__ import annotations

import asyncio
import os
import subprocess
import time
from pathlib import Path


class SpeechRuntime:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._last_attempt = 0.0
        self._lock = asyncio.Lock()

    async def ensure_started(self) -> None:
        if os.name != "nt" or self._lock.locked() or time.monotonic() - self._last_attempt < 120:
            return
        if not (self.root / "data" / "tts" / "service.json").exists():
            return
        async with self._lock:
            self._last_attempt = time.monotonic()
            result = await asyncio.to_thread(subprocess.run,
                ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", str(self.root / "scripts" / "start_speech.ps1")],
                cwd=self.root, capture_output=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:
                raise RuntimeError("local speech launcher failed; inspect speech service logs")
