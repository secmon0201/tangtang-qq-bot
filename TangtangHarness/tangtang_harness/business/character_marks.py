"""Compact non-text character motifs for locally rendered report decorations."""

from __future__ import annotations

from PIL import ImageDraw


def draw_stitched_mascot(draw: ImageDraw.ImageDraw, left: int, top: int, size: int = 54) -> None:
    """Draw a hanging cross-eye stitched smile badge without any text label."""
    cx = left + size // 2
    cy = top + size // 2 + 3
    radius = max(13, size // 2 - 4)
    ink = "#342b31"
    paper = "#fffafd"
    draw.line((cx, top - 12, cx, cy - radius + 2), fill="#6d5661", width=2)
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=ink, outline="#1f191e", width=2)
    eye = max(5, radius // 3)
    for offset in (-radius // 2, radius // 2):
        draw.line((cx + offset - eye, cy - eye, cx + offset + eye, cy + eye), fill=paper, width=3)
        draw.line((cx + offset + eye, cy - eye, cx + offset - eye, cy + eye), fill=paper, width=3)
    mouth_top = cy + radius // 6
    draw.arc((cx - radius // 2, mouth_top - 2, cx + radius // 2, mouth_top + radius // 2 + 7), 5, 175, fill=paper, width=2)
    stitch_y = mouth_top + radius // 2 + 1
    draw.line(
        [(cx - radius // 2 + 2, stitch_y), (cx - radius // 4, stitch_y + 4), (cx, stitch_y), (cx + radius // 4, stitch_y + 4), (cx + radius // 2 - 2, stitch_y)],
        fill=paper,
        width=2,
    )
    _heart(draw, left + 4, top + 20, 8, ink)
    _heart(draw, left + size - 4, top + 20, 8, ink)


def draw_heart_tail(draw: ImageDraw.ImageDraw, left: int, top: int, scale: int = 1) -> None:
    """Draw a restrained curled tail ending in a heart, sized for footer whitespace."""
    ink = "#4b3d45"
    points = [
        (left + 42 * scale, top - 26 * scale),
        (left + 28 * scale, top - 20 * scale),
        (left + 23 * scale, top - 9 * scale),
        (left + 15 * scale, top - 3 * scale),
    ]
    draw.line(points, fill=ink, width=max(2, 3 * scale), joint="curve")
    _heart(draw, left + 12 * scale, top, 12 * scale, ink)


def _heart(draw: ImageDraw.ImageDraw, center_x: int, center_y: int, size: int, fill: str) -> None:
    radius = max(2, size // 4)
    draw.ellipse((center_x - radius * 2, center_y - radius, center_x, center_y + radius), fill=fill)
    draw.ellipse((center_x, center_y - radius, center_x + radius * 2, center_y + radius), fill=fill)
    draw.polygon([(center_x - radius * 2, center_y), (center_x + radius * 2, center_y), (center_x, center_y + size)], fill=fill)
