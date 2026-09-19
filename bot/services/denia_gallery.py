"""Weighted random access to the local Denia image gallery."""
from __future__ import annotations

import json
import random
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from bot.config import ROOT


DEFAULT_GALLERY_DIR = ROOT / "bot" / "resources" / "personas" / "denia" / "gallery"
DEFAULT_HISTORY_PATH = ROOT / "data" / "tangtang" / "denia-gallery.db"
RECENT_LIMIT = 10
RECENT_WEIGHTS = (0.08, 0.14, 0.22, 0.32, 0.44, 0.58, 0.72, 0.84, 0.92, 1.0)

SCHEMA = """
CREATE TABLE IF NOT EXISTS denia_gallery_draws(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    image_id TEXT NOT NULL,
    group_id INTEGER NOT NULL DEFAULT 0,
    user_id INTEGER NOT NULL DEFAULT 0,
    drawn_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS denia_gallery_draws_recent_idx
    ON denia_gallery_draws(id DESC);
"""


class GalleryManifestError(RuntimeError):
    """The checked-in gallery manifest is missing or malformed."""


@dataclass(frozen=True, slots=True)
class GalleryImage:
    image_id: str
    path: Path
    width: int
    height: int
    sha256: str


def load_gallery(gallery_dir: Path = DEFAULT_GALLERY_DIR) -> tuple[GalleryImage, ...]:
    manifest_path = gallery_dir / "manifest.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GalleryManifestError(f"unable to read gallery manifest: {manifest_path}") from exc
    if not isinstance(payload, dict):
        raise GalleryManifestError("gallery manifest must be an object")
    if payload.get("schema_version") != 1 or payload.get("persona") != "denia":
        raise GalleryManifestError("unsupported gallery manifest")
    rows = payload.get("images")
    if not isinstance(rows, list) or not rows:
        raise GalleryManifestError("gallery manifest has no images")

    images: list[GalleryImage] = []
    seen_ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise GalleryManifestError("gallery image entry must be an object")
        image_id = str(row.get("id") or "").strip()
        filename = str(row.get("file") or "").strip()
        try:
            width = int(row.get("width") or 0)
            height = int(row.get("height") or 0)
        except (TypeError, ValueError) as exc:
            raise GalleryManifestError(f"invalid gallery image entry: {row!r}") from exc
        sha256 = str(row.get("sha256") or "").strip().lower()
        if (
            not image_id
            or image_id in seen_ids
            or not filename
            or Path(filename).name != filename
            or width <= 0
            or height <= 0
            or len(sha256) != 64
        ):
            raise GalleryManifestError(f"invalid gallery image entry: {row!r}")
        path = gallery_dir / filename
        if not path.is_file():
            raise GalleryManifestError(f"gallery image is missing: {filename}")
        seen_ids.add(image_id)
        images.append(GalleryImage(image_id, path, width, height, sha256))
    if int(payload.get("count") or -1) != len(images):
        raise GalleryManifestError("gallery manifest count does not match its images")
    return tuple(images)


class GalleryHistoryStore:
    """Persist recent global draws so restarts do not reset the weighting."""

    def __init__(self, path: Path = DEFAULT_HISTORY_PATH, *, now=None) -> None:
        self.path = path
        self._now = now or time.time

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.executescript(SCHEMA)
        return connection

    def recent_ids(self, limit: int = RECENT_LIMIT) -> tuple[str, ...]:
        safe_limit = max(1, min(int(limit), 100))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT image_id FROM denia_gallery_draws "
                "ORDER BY id DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()
        return tuple(str(row["image_id"]) for row in rows)

    def record(self, image_id: str, *, group_id: int = 0, user_id: int = 0) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO denia_gallery_draws(image_id,group_id,user_id,drawn_at) "
                "VALUES (?,?,?,?)",
                (str(image_id), int(group_id), int(user_id), float(self._now())),
            )
            connection.execute(
                "DELETE FROM denia_gallery_draws WHERE id NOT IN "
                "(SELECT id FROM denia_gallery_draws ORDER BY id DESC LIMIT 1000)"
            )


class DeniaGallery:
    """Choose one image and atomically persist it as a recent draw."""

    def __init__(
        self,
        gallery_dir: Path = DEFAULT_GALLERY_DIR,
        *,
        history_path: Path = DEFAULT_HISTORY_PATH,
        rng: random.Random | None = None,
    ) -> None:
        self.images = load_gallery(gallery_dir)
        self.history = GalleryHistoryStore(history_path)
        self._rng = rng or random.SystemRandom()
        self._lock = threading.Lock()

    def selection_weights(self, recent_ids: tuple[str, ...] = ()) -> tuple[float, ...]:
        ranks: dict[str, int] = {}
        for rank, image_id in enumerate(recent_ids[:RECENT_LIMIT]):
            if image_id:
                ranks.setdefault(image_id, rank)
        return tuple(
            RECENT_WEIGHTS[min(ranks[image.image_id], len(RECENT_WEIGHTS) - 1)]
            if image.image_id in ranks
            else 1.0
            for image in self.images
        )

    def _choose(self, recent_ids: tuple[str, ...]) -> GalleryImage:
        weights = self.selection_weights(recent_ids)
        target = float(self._rng.random()) * sum(weights)
        cursor = 0.0
        for image, weight in zip(self.images, weights):
            cursor += weight
            if target < cursor:
                return image
        return self.images[-1]

    def draw(self, *, group_id: int = 0, user_id: int = 0) -> GalleryImage:
        with self._lock:
            image = self._choose(self.history.recent_ids())
            self.history.record(image.image_id, group_id=group_id, user_id=user_id)
            return image


__all__ = [
    "DEFAULT_GALLERY_DIR",
    "DEFAULT_HISTORY_PATH",
    "RECENT_LIMIT",
    "DeniaGallery",
    "GalleryHistoryStore",
    "GalleryImage",
    "GalleryManifestError",
    "load_gallery",
]
