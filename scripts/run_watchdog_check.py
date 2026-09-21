"""Windowless scheduled-task entrypoint; invoke with the project pythonw.exe."""

from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess


def run_check(root: Path, *, timeout: float = 45) -> int:
    log = root / "logs" / "watchdog-supervisor-check.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    engine = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    try:
        result = subprocess.run(
            [str(engine), "-NoLogo", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-File", str(root / "scripts/ensure_watchdog.ps1")],
            cwd=root, stdin=subprocess.DEVNULL, capture_output=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode == 0:
            return 0
        detail = f"exit={result.returncode}\n{result.stdout}\n{result.stderr}"
        code = result.returncode
    except subprocess.TimeoutExpired:
        detail, code = "check timed out", 124
    except OSError as exc:
        detail, code = f"check launch failed: {exc}", 1
    # Hidden failures remain diagnosable without displaying a dialog/console.
    with log.open("a", encoding="utf-8") as stream:
        stream.write(f"{datetime.now(timezone.utc).isoformat()} {detail.strip()}\n")
    return code


if __name__ == "__main__":
    raise SystemExit(run_check(Path(__file__).resolve().parents[1]))
