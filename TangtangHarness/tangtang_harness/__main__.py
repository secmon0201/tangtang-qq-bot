from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger
import psutil
import uvicorn

from .config import DEFAULT_ROOT, load_config


def main():
    parser = argparse.ArgumentParser(description="TangtangHarness 独立运行入口")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    root = args.root.resolve()
    load_dotenv(root / ".env", override=False)
    config = load_config(root)
    (root / "data").mkdir(parents=True, exist_ok=True)
    process = psutil.Process(os.getpid())
    port = args.port or config.port
    record = {"pid": os.getpid(), "root": str(root), "port": port, "started_at": time.time(),
              "created": process.create_time(), "cmdline": process.cmdline()}
    (root / "data" / "process.json").write_text(json.dumps(record), encoding="utf-8")
    (root / "logs").mkdir(parents=True, exist_ok=True)
    logger.add(root / "logs" / "harness.log", rotation="16 MB", retention="30 days", encoding="utf-8")
    from .app import create_app
    try:
        uvicorn.run(create_app(root), host=args.host or config.host, port=port)
    finally:
        process_file = root / "data" / "process.json"
        try:
            current = json.loads(process_file.read_text(encoding="utf-8"))
            if int(current.get("pid", -1)) == os.getpid():
                process_file.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass


if __name__ == "__main__":
    main()
