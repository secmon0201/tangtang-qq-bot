from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from nonebot.exception import FinishedException
from PIL import Image

from bot.services.denia_gallery import (
    RECENT_LIMIT,
    RECENT_WEIGHTS,
    DeniaGallery,
    GalleryHistoryStore,
    load_gallery,
)


ROOT = Path(__file__).resolve().parents[1]


class FixedRandom:
    def __init__(self, value: float) -> None:
        self.value = value

    def random(self) -> float:
        return self.value


def write_manifest(path: Path, count: int = 3) -> None:
    path.mkdir(parents=True)
    images = []
    for index in range(1, count + 1):
        filename = f"denia-{index:03d}.jpg"
        (path / filename).write_bytes(b"fixture")
        images.append(
            {
                "id": f"denia-{index:03d}",
                "file": filename,
                "width": 1,
                "height": 1,
                "sha256": "0" * 64,
            }
        )
    (path / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "persona": "denia",
                "count": count,
                "images": images,
            }
        ),
        encoding="utf-8",
    )


def test_checked_in_gallery_is_nonempty_normalized_and_has_matching_hashes():
    images = load_gallery()

    assert len(images) == 129
    assert [image.image_id for image in images] == [
        f"denia-{index:03d}" for index in range(1, len(images) + 1)
    ]
    for image in images:
        digest = hashlib.sha256(image.path.read_bytes()).hexdigest()
        assert digest == image.sha256
        with Image.open(image.path) as opened:
            assert opened.format == "JPEG"
            assert opened.size == (image.width, image.height)
            assert max(opened.size) <= 2048


def test_recent_draws_are_downweighted_but_remain_selectable(tmp_path):
    gallery_dir = tmp_path / "gallery"
    write_manifest(gallery_dir)
    store = GalleryHistoryStore(tmp_path / "history.db")
    store.record("denia-002")

    gallery = DeniaGallery(
        gallery_dir,
        history_path=tmp_path / "history.db",
        rng=FixedRandom(0.0),
    )
    weights = gallery.selection_weights(store.recent_ids())

    assert weights == (1.0, RECENT_WEIGHTS[0], 1.0)
    assert gallery.draw().image_id == "denia-001"
    recent = store.recent_ids()
    assert recent == ("denia-001", "denia-002")


def test_history_keeps_only_the_most_recent_ten_items(tmp_path):
    store = GalleryHistoryStore(tmp_path / "history.db")
    for index in range(1, RECENT_LIMIT + 3):
        store.record(f"denia-{index:03d}")

    assert store.recent_ids() == tuple(
        f"denia-{index:03d}" for index in range(RECENT_LIMIT + 2, 2, -1)
    )


def test_conversational_handler_sends_one_local_image(monkeypatch, tmp_path):
    from bot.plugins import denia_gallery

    image_path = tmp_path / "denia-001.jpg"
    image_path.write_bytes(b"fixture")
    monkeypatch.setattr(
        denia_gallery.gallery,
        "draw",
        lambda **_kwargs: SimpleNamespace(path=image_path),
    )
    sent = []

    class Matcher:
        async def send(self, message):
            sent.append(message)

    with pytest.raises(FinishedException):
        asyncio.run(
            denia_gallery._send_random_denia_image(
                Matcher(),
                SimpleNamespace(),
                SimpleNamespace(group_id=1001, user_id=900000001),
                SimpleNamespace(),
            )
        )

    assert len(sent) == 1
    assert image_path.resolve().as_uri() in str(sent[0])
