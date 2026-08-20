from pathlib import Path

from bot.services.media import local_image_segment


def test_local_image_segment_uses_file_uri(tmp_path: Path):
    path = tmp_path / "report.png"

    segment = local_image_segment(path)

    assert segment.type == "image"
    assert segment.data["file"].startswith("file:///")
    assert segment.data["file"].endswith("report.png")
