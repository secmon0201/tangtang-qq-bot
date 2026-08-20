"""Small deterministic visual primitives shared by Pillow report renderers."""

from __future__ import annotations

from PIL import Image, ImageDraw


def paste_horizontal_gradient(
    image: Image.Image,
    box: tuple[int, int, int, int],
    start: str,
    end: str,
    *,
    radius: int = 0,
    outline: str | None = None,
    outline_width: int = 1,
) -> None:
    """Paste a left-to-right RGB gradient with an optional rounded clipping mask."""
    left, top, right, bottom = box
    width, height = right - left + 1, bottom - top + 1
    if width <= 0 or height <= 0:
        return

    start_rgb = _rgb(start)
    end_rgb = _rgb(end)
    gradient = Image.new("RGB", (width, height))
    gradient_draw = ImageDraw.Draw(gradient)
    denominator = max(1, width - 1)
    for x in range(width):
        ratio = x / denominator
        color = tuple(round(a + (b - a) * ratio) for a, b in zip(start_rgb, end_rgb, strict=True))
        gradient_draw.line((x, 0, x, height), fill=color)

    mask = Image.new("L", (width, height), 0)
    mask_draw = ImageDraw.Draw(mask)
    if radius:
        mask_draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=255)
    else:
        mask_draw.rectangle((0, 0, width - 1, height - 1), fill=255)
    image.paste(gradient, (left, top), mask)
    if outline:
        ImageDraw.Draw(image).rounded_rectangle(box, radius=radius, outline=outline, width=outline_width)


def _rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    if len(value) != 6:
        raise ValueError(f"expected a #RRGGBB color, got {value!r}")
    return tuple(int(value[index:index + 2], 16) for index in range(0, 6, 2))
