from __future__ import annotations

from typing import Any
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw, ImageFont


_EMOJI_CACHE: dict[tuple[str, int], Image.Image | None] = {}


def is_emoji_character(value: str) -> bool:
    if len(value) != 1:
        return False
    codepoint = ord(value)
    return 0x1F000 <= codepoint <= 0x1FAFF or 0x2600 <= codepoint <= 0x27BF


class EmojiTextDraw:
    """ImageDraw proxy that renders Unicode emoji through Twemoji bitmap assets."""

    def __init__(self, image: Image.Image) -> None:
        self.image = image
        self._draw = ImageDraw.Draw(image)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._draw, name)

    def text(self, xy: tuple[int, int], text: Any, *args: Any, **kwargs: Any) -> None:
        value = str(text)
        if not any(is_emoji_character(character) for character in value):
            self._draw.text(xy, value, *args, **kwargs)
            return
        font = kwargs.get("font") or (args[0] if args else ImageFont.load_default())
        fill = kwargs.get("fill") if "fill" in kwargs else (args[1] if len(args) > 1 else "#000000")
        if kwargs.get("anchor") is not None:
            self._draw.text(xy, value, *args, **kwargs)
            return
        cursor_x, y = int(xy[0]), int(xy[1])
        index = 0
        emoji_size = _line_height(font)
        while index < len(value):
            character = value[index]
            if not is_emoji_character(character):
                end = index + 1
                while end < len(value) and not is_emoji_character(value[end]):
                    end += 1
                run = value[index:end]
                self._draw.text((cursor_x, y), run, *args, **kwargs)
                cursor_x += _text_width(run, font)
                index = end
                continue
            end = index + 1
            while end < len(value) and ord(value[end]) in {0xFE0F, 0x1F3FB, 0x1F3FC, 0x1F3FD, 0x1F3FE, 0x1F3FF}:
                end += 1
            if end < len(value) - 1 and ord(value[end]) == 0x200D and is_emoji_character(value[end + 1]):
                end += 2
            emoji = _emoji_image(value[index:end], emoji_size)
            emoji_width = max(emoji_size, _text_width(character, font))
            top = y + max(0, (emoji_size - emoji.height) // 2) if emoji is not None else y
            if emoji is not None:
                if self.image.mode == "RGBA":
                    self.image.alpha_composite(emoji, (cursor_x, top))
                else:
                    self.image.paste(emoji, (cursor_x, top), emoji)
            else:
                self._draw.text((cursor_x, y), "?", font=font, fill=fill)
            cursor_x += emoji_width
            index = end


def _emoji_image(value: str, size: int) -> Image.Image | None:
    key = (value, size)
    if key not in _EMOJI_CACHE:
        codepoints = "-".join(f"{ord(character):x}" for character in value if ord(character) != 0xFE0F)
        url = f"https://cdn.jsdelivr.net/gh/jdecked/twemoji@latest/assets/72x72/{codepoints}.png"
        try:
            request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(request, timeout=10) as response:
                source = Image.open(response).convert("RGBA")
            source.thumbnail((size, size), Image.Resampling.LANCZOS)
        except Exception:
            source = None
        _EMOJI_CACHE[key] = source
    cached = _EMOJI_CACHE[key]
    return cached.copy() if cached is not None else None


def _line_height(font: ImageFont.ImageFont) -> int:
    box = font.getbbox("Ag")
    return max(1, box[3] - box[1])


def _text_width(value: str, font: ImageFont.ImageFont) -> int:
    box = font.getbbox(value)
    return box[2] - box[0]
