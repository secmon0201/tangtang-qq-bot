"""Reproduce the project-owned business migration without touching legacy files.

Run from the repository root. This mechanical source migration intentionally
copies the complete mature game algorithms, not a shortened reimplementation.
Only tracked static resources are copied; operational data is never copied.
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


NEW_ROOT = Path(__file__).resolve().parents[1]
OLD_ROOT = NEW_ROOT.parent
BUSINESS = NEW_ROOT / "tangtang_harness" / "business"
MODULES = (
    "mini_games", "idioms", "today_wife", "today_wife_game", "today_wife_story",
    "today_wife_content", "today_wife_narrative", "reports", "mini_game_reports",
    "image_style", "emoji_text", "character_marks", "stats", "group_domains",
    "knowledge_db", "knowledge_search", "knowledge_review", "zhijiang_knowledge",
    "mingchao_meme_culture", "denia_gallery", "asoul", "asoul_render",
    "asoul_web_render", "web_screenshot", "avatars", "duplicate", "hourly_copy",
    "a_coast_archive", "a_coast_archive_render", "tangtang_db", "tangtang_humanize", "community_web", "persona_mood",
    "persona_inbox", "zhijiang_live_guard", "reactions", "triple_repeat", "qq_platform", "gateway",
)


def convert(source: str) -> str:
    source = source.replace("from bot.services.", "from tangtang_harness.business.")
    source = source.replace("from bot.config import", "from tangtang_harness.business.config import")
    source = source.replace("from bot.db import", "from tangtang_harness.business.db import")
    source = source.replace("from nonebot import logger", "from loguru import logger")
    source = source.replace("from tangtang_harness.business.pacing import paced_call_api",
                            "from tangtang_harness.business.protocol import paced_call_api")
    source = source.replace('ROOT / "bot" / "resources"', 'RESOURCE_DIR')
    source = source.replace('ROOT / "data"', 'ROOT / "runtime"')
    source = source.replace('from tangtang_harness.business.config import ROOT\n',
                            'from tangtang_harness.business.config import ROOT, RESOURCE_DIR\n')
    source = source.replace('from tangtang_harness.business.config import ROOT, settings',
                            'from tangtang_harness.business.config import ROOT, RESOURCE_DIR, settings')
    source = source.replace('Path(__file__).resolve().parent.parent / "resources"', 'RESOURCE_DIR')
    source = source.replace('Path(__file__).resolve().parents[1] / "resources"', 'RESOURCE_DIR')
    if "RESOURCE_DIR" in source and "config import" not in source:
        source = source.replace("from pathlib import Path\n",
                                "from pathlib import Path\nfrom tangtang_harness.business.config import RESOURCE_DIR\n")
    return source


def main() -> None:
    BUSINESS.mkdir(parents=True, exist_ok=True)
    provenance = []
    for name in (*MODULES, "db"):
        old_path = OLD_ROOT / "bot" / ("db.py" if name == "db" else f"services/{name}.py")
        raw = old_path.read_bytes()
        converted = convert(raw.decode("utf-8-sig"))
        ast.parse(converted)
        (BUSINESS / f"{name}.py").write_text(converted, encoding="utf-8", newline="\n")
        provenance.append({"source": old_path.relative_to(OLD_ROOT).as_posix(),
                           "source_sha256": hashlib.sha256(raw).hexdigest(),
                           "destination": f"tangtang_harness/business/{name}.py"})
    paths = subprocess.check_output(
        ["git", "ls-files", "-z", "--", "bot/resources"], cwd=OLD_ROOT
    ).decode("utf-8").split("\0")
    for relative in filter(None, paths):
        destination = NEW_ROOT / "resources" / Path(relative).relative_to("bot/resources")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(OLD_ROOT / relative, destination)
    (NEW_ROOT / "resources" / "business-migration.json").write_text(
        json.dumps({"modules": provenance, "resource_count": len([p for p in paths if p])},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Migrated {len(provenance)} business modules and tracked static resources into {NEW_ROOT.name}.")


if __name__ == "__main__":
    main()
