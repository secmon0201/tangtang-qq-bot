"""Small deterministic visual primitives shared by Pillow report renderers."""

from __future__ import annotations

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFont


DEFAULT_OUTPUT_CORNER_RADIUS = 34
AURORA_SIGNAL_COLORS = ("#f26f82", "#8d67ce", "#47c9b5", "#f2ce63")
AURORA_BACKGROUND_STOPS = (
    (0.0, "#fff6f9"),
    (0.34, "#ffffff"),
    (0.7, "#f2f6ff"),
    (1.0, "#eefaf7"),
)


def transparent_rounded_corners(
    image: Image.Image,
    *,
    radius: int = DEFAULT_OUTPUT_CORNER_RADIUS,
) -> Image.Image:
    """Return an RGBA image with transparent pixels outside its rounded corners."""
    result = image.convert("RGBA")
    actual_radius = min(max(0, radius), min(result.size) // 2)
    if actual_radius == 0:
        return result

    mask = Image.new("L", result.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, result.width - 1, result.height - 1),
        radius=actual_radius,
        fill=255,
    )
    result.putalpha(ImageChops.multiply(result.getchannel("A"), mask))
    return result


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


def multistop_gradient(
    width: int,
    height: int,
    stops: tuple[tuple[float, str], ...],
    *,
    vertical: bool = False,
) -> Image.Image:
    """Create a deterministic RGB gradient interpolated through ordered color stops."""
    if width <= 0 or height <= 0:
        raise ValueError("gradient dimensions must be positive")
    if len(stops) < 2:
        raise ValueError("a multistop gradient needs at least two color stops")

    ordered = tuple(sorted(stops, key=lambda item: item[0]))
    length = height if vertical else width
    strip = Image.new("RGB", (1, length) if vertical else (length, 1))
    pixels = strip.load()
    segment = 0
    for index in range(length):
        position = index / max(1, length - 1)
        while segment + 1 < len(ordered) - 1 and position > ordered[segment + 1][0]:
            segment += 1
        start_at, start_color = ordered[segment]
        end_at, end_color = ordered[min(segment + 1, len(ordered) - 1)]
        ratio = min(1.0, max(0.0, (position - start_at) / max(0.0001, end_at - start_at)))
        start_rgb = ImageColor.getrgb(start_color)
        end_rgb = ImageColor.getrgb(end_color)
        color = tuple(
            round(left + (right - left) * ratio)
            for left, right in zip(start_rgb, end_rgb, strict=True)
        )
        if vertical:
            pixels[0, index] = color
        else:
            pixels[index, 0] = color
    return strip.resize((width, height), Image.Resampling.BILINEAR)


def paste_multistop_gradient(
    image: Image.Image,
    box: tuple[int, int, int, int],
    stops: tuple[tuple[float, str], ...],
    *,
    vertical: bool = False,
    radius: int = 0,
    outline: str | None = None,
    outline_width: int = 1,
) -> None:
    """Paste a clipped multistop gradient into an inclusive Pillow box."""
    left, top, right, bottom = box
    width, height = right - left + 1, bottom - top + 1
    if width <= 0 or height <= 0:
        return
    gradient = multistop_gradient(width, height, stops, vertical=vertical)
    mask = Image.new("L", (width, height), 0)
    mask_draw = ImageDraw.Draw(mask)
    if radius:
        mask_draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=255)
    else:
        mask_draw.rectangle((0, 0, width - 1, height - 1), fill=255)
    image.paste(gradient, (left, top), mask)
    if outline:
        ImageDraw.Draw(image).rounded_rectangle(box, radius=radius, outline=outline, width=outline_width)


def new_aurora_signal_canvas(
    width: int,
    height: int,
    *,
    rail_width: int = 12,
) -> Image.Image:
    """Create the shared Aurora Signal Glass background and four-color side rail."""
    image = multistop_gradient(width, height, AURORA_BACKGROUND_STOPS)
    vertical_tint = multistop_gradient(
        width,
        height,
        ((0.0, "#ffffff"), (0.58, "#faf9ff"), (1.0, "#f1f7fb")),
        vertical=True,
    )
    image = Image.blend(image, vertical_tint, 0.26)
    if rail_width > 0:
        rail = multistop_gradient(
            min(width, rail_width),
            height,
            (
                (0.0, AURORA_SIGNAL_COLORS[1]),
                (0.34, AURORA_SIGNAL_COLORS[0]),
                (0.68, AURORA_SIGNAL_COLORS[3]),
                (1.0, AURORA_SIGNAL_COLORS[2]),
            ),
            vertical=True,
        )
        image.paste(rail, (0, 0))
    return image


def draw_signal_diamonds(
    draw: ImageDraw.ImageDraw,
    right: int,
    top: int,
    *,
    size: int = 8,
    gap: int = 8,
) -> None:
    """Draw the compact pink/yellow/mint signal signature used by ASG surfaces."""
    colors = (AURORA_SIGNAL_COLORS[0], AURORA_SIGNAL_COLORS[3], AURORA_SIGNAL_COLORS[2])
    step = size * 2 + gap
    for index, color in enumerate(colors):
        center_x = right - (len(colors) - 1 - index) * step - size
        center_y = top + size
        draw.polygon(
            (
                (center_x, center_y - size),
                (center_x + size, center_y),
                (center_x, center_y + size),
                (center_x - size, center_y),
            ),
            fill=color,
        )


def draw_gradient_text(
    image: Image.Image,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.ImageFont,
    *,
    stops: tuple[tuple[float, str], ...] | None = None,
) -> None:
    """Draw text filled with the shared pink-purple-mint signal gradient."""
    if not text:
        return
    box = font.getbbox(text)
    width = max(1, box[2] - box[0])
    height = max(1, box[3] - box[1])
    mask = Image.new("L", (width + 4, height + 4), 0)
    ImageDraw.Draw(mask).text((2 - box[0], 2 - box[1]), text, font=font, fill=255)
    gradient = multistop_gradient(
        mask.width,
        mask.height,
        stops
        or (
            (0.0, AURORA_SIGNAL_COLORS[0]),
            (0.58, AURORA_SIGNAL_COLORS[1]),
            (1.0, AURORA_SIGNAL_COLORS[2]),
        ),
    )
    image.paste(gradient, xy, mask)


def _rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    if len(value) != 6:
        raise ValueError(f"expected a #RRGGBB color, got {value!r}")
    return tuple(int(value[index:index + 2], 16) for index in range(0, 6, 2))
