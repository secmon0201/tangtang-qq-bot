"""Validate the maintained Markdown set and reject retired documentation."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote


LINK_RE = re.compile(r"!?(?:\[[^]]*\])\(([^)]+)\)")
RETIRED_REFERENCES = (
    "deploy/linux",
    "scripts/vm",
    "linux_vm_",
    "start_bot_and_napcat.bat",
    "restart_bot_and_napcat.bat",
    "stop_bot_and_napcat.bat",
    "restart_nonebot.bat",
    "start_nte_tunnel.bat",
    "stop_nte_tunnel.bat",
    "set_nte_login_url.bat",
    "start_watchdog.bat",
    "stop_watchdog.bat",
)
STALE_MARKERS = (
    "状态：等待确认",
    "尚未写入机器人运行提示词",
    "用于在另一个对话中交给其他模型实施",
    "待实施时确认",
)


def markdown_files(root: Path) -> list[Path]:
    return [root / "README.md", root / "AGENTS.md", *sorted((root / "docs").rglob("*.md"))]


def local_link(source: Path, raw_target: str) -> Path | None:
    target = raw_target.strip().strip("<>")
    if not target or target.startswith("#") or re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I):
        return None
    decoded = unquote(target)
    direct = (source.parent / decoded).resolve()
    if direct.exists():
        return direct
    path_only = decoded.split("#", 1)[0]
    return (source.parent / path_only).resolve()


def validate_docs(root: Path) -> list[str]:
    errors: list[str] = []
    files = markdown_files(root)
    for source in files:
        if not source.is_file():
            errors.append(f"missing required document: {source.relative_to(root).as_posix()}")
            continue
        text = source.read_text(encoding="utf-8")
        normalised_text = text.replace("\\", "/").lower()
        for retired in RETIRED_REFERENCES:
            if retired.lower() in normalised_text:
                errors.append(
                    f"retired documentation reference in {source.relative_to(root).as_posix()}: {retired}"
                )
        for marker in STALE_MARKERS:
            if marker in text:
                errors.append(
                    f"stale documentation marker in {source.relative_to(root).as_posix()}: {marker}"
                )
        for raw_target in LINK_RE.findall(text):
            target = local_link(source, raw_target)
            if target is not None and not target.exists():
                errors.append(
                    f"broken local link in {source.relative_to(root).as_posix()}: {raw_target}"
                )

    index = root / "docs" / "README.md"
    if index.is_file():
        indexed = {
            target
            for raw_target in LINK_RE.findall(index.read_text(encoding="utf-8"))
            if (target := local_link(index, raw_target)) is not None
        }
        for document in sorted((root / "docs").rglob("*.md")):
            if document != index and document.resolve() not in indexed:
                errors.append(
                    f"document missing from docs/README.md: {document.relative_to(root).as_posix()}"
                )
    return errors


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    errors = validate_docs(root)
    if errors:
        print("Documentation validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Documentation validation passed: links, index, and retirement rules are clean.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
