"""Start upstream GsUID Core through the project-owned compatibility boundary."""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE_DIR = Path(os.getenv("GSUID_CORE_DIR", ROOT / "GsUID.Core")).resolve()


def main() -> None:
    if not (CORE_DIR / "gsuid_core" / "core.py").is_file():
        raise SystemExit("GsUID Core is not installed. Run scripts/install_gsuid.ps1 first.")
    for path in (ROOT, CORE_DIR):
        value = str(path)
        if value not in sys.path:
            sys.path.insert(0, value)
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="backslashreplace")
    os.chdir(CORE_DIR)

    from bot.integrations.gsuid_core_compat import install_gsuid_core_compatibility

    install_gsuid_core_compatibility()
    runpy.run_module("gsuid_core.core", run_name="__main__")


if __name__ == "__main__":
    main()
