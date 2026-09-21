from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
PERSONA_DIR = ROOT / "bot" / "resources" / "personas" / "denia"
EXPRESSION_DIR = PERSONA_DIR / "expressions"
NEW_ANIMATED_IDS = {f"expr_{index:03d}" for index in range(52, 77)}


def test_checked_in_denia_expressions_match_catalog() -> None:
    catalog = json.loads(
        (PERSONA_DIR / "expression_catalog.json").read_text(encoding="utf-8")
    )
    files = {path.name: path for path in EXPRESSION_DIR.iterdir() if path.is_file()}

    assert len(catalog) == 76
    assert len(files) == 76
    assert len({row["id"] for row in catalog}) == len(catalog)
    assert {row["file"] for row in catalog} == set(files)
    assert not list(EXPRESSION_DIR.glob("*.webp"))

    for row in catalog:
        path = files[row["file"]]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"]
        with Image.open(path) as image:
            assert max(image.size) <= 300


def test_imported_denia_gif_animations_preserve_timing() -> None:
    catalog = json.loads(
        (PERSONA_DIR / "expression_catalog.json").read_text(encoding="utf-8")
    )
    imported = [row for row in catalog if row["id"] in NEW_ANIMATED_IDS]

    assert {row["id"] for row in imported} == NEW_ANIMATED_IDS
    for row in imported:
        with Image.open(EXPRESSION_DIR / row["file"]) as image:
            assert image.format == "GIF"
            assert image.is_animated
            assert image.n_frames == row["frames"]
            assert image.info.get("loop") == 0
            durations = []
            for frame_index in range(image.n_frames):
                image.seek(frame_index)
                durations.append(image.info.get("duration"))
            assert durations == [70] * image.n_frames
