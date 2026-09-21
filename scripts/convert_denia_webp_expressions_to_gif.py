"""Replace the reviewed Denia WebP animations with mobile-compatible GIF files."""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

from PIL import Image

from import_denia_expression_pack import (
    ASSET_MANIFEST_PATH,
    CATALOG_PATH,
    ENTRIES,
    EXPRESSION_DIR,
    ROOT,
    digest,
    resize_gif,
    write_json,
)


def gif_durations(path: Path) -> list[int]:
    with Image.open(path) as image:
        durations = []
        for frame_index in range(image.n_frames):
            image.seek(frame_index)
            durations.append(int(image.info.get("duration", 0)))
        return durations


def convert(source_dir: Path) -> None:
    source_dir = source_dir.resolve(strict=True)
    source_by_hash = {
        digest(path): path for path in source_dir.iterdir() if path.is_file()
    }
    missing_sources = sorted(
        entry.source_sha256
        for entry in ENTRIES
        if entry.source_sha256 not in source_by_hash
    )
    if missing_sources:
        raise SystemExit(f"Missing reviewed source files: {missing_sources}")

    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    rows_by_id = {row["id"]: row for row in catalog}
    manifest = json.loads(ASSET_MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest_by_path = {row["path"]: row for row in manifest["files"]}

    old_paths: list[Path] = []
    for entry in ENTRIES:
        row = rows_by_id.get(entry.expression_id)
        old_filename = str(Path(entry.filename).with_suffix(".webp"))
        old_path = EXPRESSION_DIR / old_filename
        if row is None or row.get("file") != old_filename:
            raise SystemExit(f"Unexpected catalog row for {entry.expression_id}")
        if not old_path.is_file() or digest(old_path) != row.get("sha256"):
            raise SystemExit(f"Active WebP does not match catalog: {old_filename}")
        if f"expressions/{old_filename}" not in manifest_by_path:
            raise SystemExit(f"Missing asset manifest row: {old_filename}")
        old_paths.append(old_path)

    all_webp = sorted(EXPRESSION_DIR.glob("*.webp"))
    if all_webp != sorted(old_paths):
        raise SystemExit("The active WebP set differs from the reviewed 25-file pack")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = ROOT / "data" / "backups" / f"denia-expressions-webp-{timestamp}"
    backup_assets = backup / "webp"
    backup_assets.mkdir(parents=True)
    shutil.copy2(CATALOG_PATH, backup / CATALOG_PATH.name)
    shutil.copy2(ASSET_MANIFEST_PATH, backup / ASSET_MANIFEST_PATH.name)
    for path in old_paths:
        shutil.copy2(path, backup_assets / path.name)

    conversions = []
    generated_paths: list[Path] = []
    for entry, old_path in zip(ENTRIES, old_paths, strict=True):
        target = EXPRESSION_DIR / entry.active_filename
        if target.exists():
            raise SystemExit(f"Destination already exists: {target.name}")
        source = source_by_hash[entry.source_sha256]
        durations, output_size = resize_gif(source, target)
        generated_paths.append(target)
        with Image.open(target) as image:
            if (
                image.format != "GIF"
                or not image.is_animated
                or image.size != output_size
                or image.n_frames != entry.frames
                or image.info.get("loop") != 0
            ):
                raise SystemExit(f"Generated GIF failed validation: {target.name}")
        if gif_durations(target) != durations:
            raise SystemExit(f"Generated GIF timing changed: {target.name}")

        new_sha256 = digest(target)
        row = rows_by_id[entry.expression_id]
        row["file"] = entry.active_filename
        row["sha256"] = new_sha256
        manifest_row = manifest_by_path[f"expressions/{old_path.name}"]
        manifest_row["path"] = f"expressions/{entry.active_filename}"
        conversions.append(
            {
                "id": entry.expression_id,
                "source": source.name,
                "old_file": old_path.name,
                "old_sha256": digest(old_path),
                "new_file": target.name,
                "new_sha256": new_sha256,
                "size": list(output_size),
                "frames": entry.frames,
                "durations": durations,
                "loop": 0,
            }
        )

    write_json(CATALOG_PATH, catalog)
    write_json(ASSET_MANIFEST_PATH, manifest)
    for path in old_paths:
        path.unlink()
    write_json(
        backup / "manifest.json",
        {
            "source": str(source_dir),
            "reason": "QQ mobile does not animate WebP expression files",
            "converted": conversions,
        },
    )
    (backup / "README.txt").write_text(
        "webp/ 保存转换前的 25 个运行时 WebP 动图。\n"
        "expression_catalog.json 与 asset-manifest.json 保存转换前清单。\n"
        "manifest.json 记录 GIF 文件名、校验值、尺寸、帧数、时长和循环参数。\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {"backup": str(backup), "converted": len(generated_paths)},
            ensure_ascii=False,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    convert(args.source)


if __name__ == "__main__":
    main()
