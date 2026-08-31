"""Project-owned ranking data rendered with the upstream NTEUID rank layout."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime, timedelta
from io import BytesIO
from functools import lru_cache
from pathlib import Path
from typing import Mapping
from uuid import uuid4

import httpx
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps

from bot.config import RESOURCE_DIR, ROOT, settings
from bot.services.image_style import transparent_rounded_corners
from bot.services.nte_rank_data import RankResult, RankRow


CORE_NTE_DIR = ROOT / "GsUID.Core" / "gsuid_core" / "plugins" / "NTEUID" / "NTEUID"
RANK_TEX = CORE_NTE_DIR / "nte_role" / "texture2d" / "rank"
CHAR_TEX = CORE_NTE_DIR / "nte_role" / "texture2d" / "character"
COMMON_TEX = CORE_NTE_DIR / "utils" / "texture2d"
CHARACTER_ART_DIR = ROOT / "bot" / "resources" / "nte_rank_characters"
UPSTREAM_CHARACTER_ART_DIR = settings.gsuid_core_dir / "data" / "NTEUID" / "role" / "detail"
ELEMENT_TEX = settings.gsuid_core_dir / "data" / "NTEUID" / "role" / "element"
NTE_FONT = CORE_NTE_DIR / "utils" / "fonts" / "nte_fonts.ttf"
CHARACTER_ART_URL = "https://webstatic.tajiduo.com/bbs/yh-game-records-web-source/character/detail/{char_id}.png"


class NTERankRenderer:
    """Mirror the original role/strongest rank composition without importing Core."""

    WIDTH = 1280
    ROLE_HEADER_HEIGHT = 430
    STRONGEST_HEADER_HEIGHT = 272
    HEADER_HEIGHT = ROLE_HEADER_HEIGHT
    ROW_HEIGHT = 152
    ROW_GAP = 16
    PANEL_LEFT = 30
    PANEL_WIDTH = 1220
    FOOTER_HEIGHT = 110
    CREAM = "#fbddbc"
    TEXT = "#ffffff"
    SUBTEXT = "#c0bee0"
    SELF = "#ff6060"
    GRADE_COLORS = {"S": "#ffd060", "A": "#aaa5f0", "B": "#b0b6d6"}

    def __init__(
        self,
        output_dir: Path | None = None,
        font_path: Path | None = None,
        retention_hours: int | None = None,
        character_art_dir: Path | None = None,
    ) -> None:
        self.output_dir = output_dir or settings.report_dir
        self.font_path = font_path or settings.report_font_path or RESOURCE_DIR / "asoul_stickers" / "font.ttf"
        self.retention_hours = retention_hours or settings.report_retention_hours
        self.character_art_dir = character_art_dir or CHARACTER_ART_DIR
        self._font_cache: dict[tuple[int, bool], ImageFont.ImageFont] = {}

    async def refresh_character_art(
        self,
        character_ids: Iterable[str],
        fetcher: Callable[[str], Awaitable[bytes]] | None = None,
    ) -> tuple[str, ...]:
        """Refresh all original character header art before each ranking render.

        The local files are only a last-successful-response cache. Every board
        asks the original CDN again, so redesigned art and newly added roles
        are reflected without a project update.
        """

        ids = tuple(sorted({str(char_id) for char_id in character_ids if str(char_id).isdigit()}))
        if not ids:
            return ()
        if fetcher is not None:
            return await self._refresh_with_fetcher(ids, fetcher)
        timeout = httpx.Timeout(4.0, connect=3.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async def fetch(char_id: str) -> bytes:
                response = await client.get(CHARACTER_ART_URL.format(char_id=char_id))
                response.raise_for_status()
                return response.content

            return await self._refresh_with_fetcher(ids, fetch)

    async def _refresh_with_fetcher(
        self,
        character_ids: tuple[str, ...],
        fetcher: Callable[[str], Awaitable[bytes]],
    ) -> tuple[str, ...]:
        semaphore = asyncio.Semaphore(8)

        async def refresh(char_id: str) -> str | None:
            try:
                async with semaphore:
                    payload = await fetcher(char_id)
                await asyncio.to_thread(self._store_character_art, char_id, payload)
                return char_id
            except (httpx.HTTPError, OSError, ValueError):
                return None

        refreshed = await asyncio.gather(*(refresh(char_id) for char_id in character_ids))
        return tuple(char_id for char_id in refreshed if char_id is not None)

    def render(self, result: RankResult, avatar_paths: Mapping[int, Path] | None = None) -> Path:
        rows = list(result.rows)
        if result.self_overflow is not None:
            rows.append(result.self_overflow)
        header_height = self.STRONGEST_HEADER_HEIGHT if result.request.strongest else self.ROLE_HEADER_HEIGHT
        overflow_gap = 34 if result.self_overflow is not None else 0
        height = header_height + max(1, len(rows)) * self.ROW_HEIGHT + max(0, len(rows) - 1) * self.ROW_GAP + overflow_gap + self.FOOTER_HEIGHT
        image = self._background(height)
        draw = ImageDraw.Draw(image)
        if result.request.strongest:
            self._draw_strongest_header(image, draw, result)
        else:
            self._draw_role_header(image, draw, result)
        y = header_height
        for index, row in enumerate(rows):
            if result.self_overflow is not None and row is result.self_overflow:
                draw.text((self.WIDTH // 2, y + 15), "· · ·", font=self._font(30), fill=self.SUBTEXT, anchor="mm")
                y += overflow_gap
            self._draw_strongest_row(image, draw, y, row, avatar_paths or {}) if result.request.strongest else self._draw_role_row(image, draw, y, row, avatar_paths or {})
            y += self.ROW_HEIGHT + self.ROW_GAP
        if not rows:
            draw.text((self.WIDTH // 2, header_height + self.ROW_HEIGHT // 2), "这一页没有可展示的记录", font=self._font(30, True), fill=self.SUBTEXT, anchor="mm")
        self._draw_footer(image)
        return self._save(image, "nte_strongest" if result.request.strongest else "nte_rank")

    def _draw_role_header(self, image: Image.Image, draw: ImageDraw.ImageDraw, result: RankResult) -> None:
        image.paste(self._background_slice(self.ROLE_HEADER_HEIGHT), (0, 0))
        image.alpha_composite(self._role_header_scrim(), (0, 0))
        char_id = result.rows[0].char_id if result.rows else ""
        self._draw_role_art(image, char_id)
        self._draw_logo(image, 48, 50, 168)
        self._draw_title_with_badge(draw, result.title, 52, 304)
        draw.rounded_rectangle((54, 346, 318, 353), radius=3, fill=self.CREAM)
        draw.text((56, 380), f"{result.scope_label} {result.total} 个号上榜 · 第 {result.request.page}/{result.total_pages} 页", font=self._font(28, True), fill=self.SUBTEXT, anchor="lm")

    def _draw_strongest_header(self, image: Image.Image, draw: ImageDraw.ImageDraw, result: RankResult) -> None:
        image.alpha_composite(Image.new("RGBA", (self.WIDTH, self.STRONGEST_HEADER_HEIGHT), (14, 12, 28, 126)), (0, 0))
        self._draw_logo(image, 48, 38, 120)
        self._draw_title_with_badge(draw, f"{self._scope_title(result)}最强排行", 52, 208, 60)
        draw.rounded_rectangle((54, 246, 360, 253), radius=3, fill=self.CREAM)

    def _draw_title_with_badge(self, draw: ImageDraw.ImageDraw, title: str, left: int, middle: int, size: int = 64) -> None:
        font = self._font(size, True)
        draw.text((left, middle), title, font=font, fill=self.TEXT, anchor="lm")
        badge_left = min(self.WIDTH - 202, left + self._text_width(title, font) + 28)
        badge_top = middle - 23
        draw.rounded_rectangle((badge_left, badge_top, badge_left + 174, badge_top + 46), radius=20, fill="#573d78", outline="#c7b0ec", width=2)
        draw.text((badge_left + 87, badge_top + 23), "糖糖接管", font=self._font(22, True), fill=self.TEXT, anchor="mm")

    def _draw_role_row(self, image: Image.Image, draw: ImageDraw.ImageDraw, y: int, row: RankRow, avatar_paths: Mapping[int, Path]) -> None:
        self._draw_frame(image, y, row.rank)
        middle = y + self.ROW_HEIGHT // 2
        rank_text = "最强" if row.rank == 1 else str(row.rank)
        self._draw_circle(draw, 60, middle - 48, 96, "#1a162c", self.CREAM if row.rank == 1 else self.TEXT)
        draw.text((108, middle + 1), rank_text, font=self._font(26 if row.rank == 1 else 48, True), fill=self.CREAM if row.rank == 1 else self.TEXT, anchor="mm")
        avatar_y = y + 14
        user_id = _numeric_id(row.user_id)
        self._draw_avatar(image, draw, 172, avatar_y, 124, avatar_paths.get(user_id) if user_id is not None else None, row.user_id or row.uid)
        self._awakening(draw, 226, avatar_y + 80, row.awaken_level)
        draw.text((320, y + 42), self._ellipsize(row.nickname, self._font(34, True), 200), font=self._font(34, True), fill=self.SELF if row.is_self else self.TEXT, anchor="lm")
        draw.text((320, y + 82), f"UID {row.uid}", font=self._font(21), fill=self.SUBTEXT, anchor="lm")
        draw.text((320, y + 118), self._ellipsize(f"所属群：{self._group_label(row)}", self._font(20), 220), font=self._font(20), fill=self.SUBTEXT, anchor="lm")
        self._draw_element(image, 540, middle - 39, row.element_type)
        draw.text((632, middle), self._ellipsize(self._suit_text(row), self._font(28, True), 350), font=self._font(28, True), fill=self.TEXT, anchor="lm")
        self._draw_grade(image, draw, 1014, middle, row.grade, row.score)

    def _draw_strongest_row(self, image: Image.Image, draw: ImageDraw.ImageDraw, y: int, row: RankRow, avatar_paths: Mapping[int, Path]) -> None:
        self._draw_frame(image, y, 3)
        middle = y + self.ROW_HEIGHT // 2
        avatar_y = y + 14
        user_id = _numeric_id(row.user_id)
        self._draw_avatar(image, draw, 60, avatar_y, 124, avatar_paths.get(user_id) if user_id is not None else None, row.char_name or row.uid)
        self._awakening(draw, 114, avatar_y + 80, row.awaken_level)
        draw.text((212, y + 42), self._ellipsize(row.char_name, self._font(34, True), 320), font=self._font(34, True), fill=self.TEXT, anchor="lm")
        holder = f"持有 {row.nickname}" if row.nickname else f"UID {row.uid}"
        draw.text((212, y + 82), self._ellipsize(holder, self._font(21, True), 320), font=self._font(21, True), fill=self.SUBTEXT, anchor="lm")
        draw.text((212, y + 118), self._ellipsize(f"所属群：{self._group_label(row)}", self._font(20), 350), font=self._font(20), fill=self.SUBTEXT, anchor="lm")
        self._draw_element(image, 560, middle - 39, row.element_type)
        draw.text((652, middle), self._ellipsize(self._suit_text(row), self._font(28, True), 350), font=self._font(28, True), fill=self.TEXT, anchor="lm")
        self._draw_grade(image, draw, 1014, middle, row.grade, row.score)

    def _draw_frame(self, image: Image.Image, y: int, rank: int) -> None:
        name = {1: "frame_1.png", 2: "frame_2.png", 3: "frame_3.png"}.get(rank, "frame_n.png")
        frame = self._frame(name)
        if frame is None:
            ImageDraw.Draw(image).rounded_rectangle((self.PANEL_LEFT, y, self.PANEL_LEFT + self.PANEL_WIDTH, y + self.ROW_HEIGHT), radius=76, fill="#090d39", outline="#bcb5ff", width=3)
            return
        image.alpha_composite(frame, (self.PANEL_LEFT, y))

    @staticmethod
    @lru_cache(maxsize=4)
    def _frame(name: str) -> Image.Image | None:
        try:
            with Image.open(RANK_TEX / name) as source:
                frame = source.convert("RGBA")
            alpha_box = frame.getchannel("A").getbbox()
            if alpha_box is None:
                return None
            return frame.crop(alpha_box).resize((NTERankRenderer.PANEL_WIDTH, NTERankRenderer.ROW_HEIGHT), Image.Resampling.LANCZOS)
        except (OSError, ValueError):
            return None

    def _draw_grade(self, image: Image.Image, draw: ImageDraw.ImageDraw, left: int, middle: int, grade: str, score: int) -> None:
        grade_image = self._open_image(CHAR_TEX / f"rank_{grade}.png")
        if grade_image is not None:
            image.alpha_composite(ImageOps.contain(grade_image, (76, 76), method=Image.Resampling.LANCZOS), (left, middle - 38))
        color = self.GRADE_COLORS.get(grade, self.TEXT)
        draw.text((1196, middle - 16), str(score), font=self._font(52, True), fill=color, anchor="rm")
        draw.text((1196, middle + 33), "分", font=self._font(22), fill=self.SUBTEXT, anchor="rm")

    def _draw_logo(self, image: Image.Image, left: int, top: int, height: int) -> None:
        logo = self._open_image(RANK_TEX / "logo_yh.png")
        if logo is None:
            return
        width = round(logo.width * height / logo.height)
        image.alpha_composite(logo.resize((width, height), Image.Resampling.LANCZOS), (left, top))

    def _draw_element(self, image: Image.Image, left: int, top: int, element_type: str) -> None:
        element = self._open_image(ELEMENT_TEX / f"{element_type}.PNG")
        if element is not None:
            image.alpha_composite(ImageOps.contain(element, (78, 78), method=Image.Resampling.LANCZOS), (left, top))

    def _draw_role_art(self, image: Image.Image, char_id: str) -> None:
        art = self._open_image(self._character_art_path(char_id))
        if art is None:
            return
        upper = art.crop((0, 0, art.width, max(1, round(art.height * 0.6))))
        width = round(upper.width * (self.ROLE_HEADER_HEIGHT + 24) / upper.height)
        art = upper.resize((width, self.ROLE_HEADER_HEIGHT + 24), Image.Resampling.LANCZOS)
        fade = Image.new("L", (width, 1))
        edge = max(1, round(width * 0.45))
        for x in range(width):
            fade.putpixel((x, 0), min(255, round(255 * x / edge)))
        alpha = ImageChops.multiply(art.getchannel("A"), fade.resize(art.size))
        art.putalpha(alpha)
        image.alpha_composite(art, (self.WIDTH - width + 60, -12))

    @staticmethod
    @lru_cache(maxsize=1)
    def _role_header_scrim() -> Image.Image:
        scrim = Image.new("RGBA", (1, NTERankRenderer.ROLE_HEADER_HEIGHT))
        for y in range(NTERankRenderer.ROLE_HEADER_HEIGHT):
            alpha = round(24 + 182 * (y / (NTERankRenderer.ROLE_HEADER_HEIGHT - 1)) ** 1.7)
            scrim.putpixel((0, y), (14, 12, 28, alpha))
        return scrim.resize((NTERankRenderer.WIDTH, NTERankRenderer.ROLE_HEADER_HEIGHT), Image.Resampling.BILINEAR)

    def _character_art_path(self, char_id: str) -> Path | None:
        if not char_id:
            return None
        for directory in (self.character_art_dir, UPSTREAM_CHARACTER_ART_DIR):
            for suffix in (".png", ".PNG"):
                candidate = directory / f"{char_id}{suffix}"
                if candidate.is_file():
                    return candidate
        return None

    def _store_character_art(self, char_id: str, payload: bytes) -> None:
        with Image.open(BytesIO(payload)) as source:
            source.verify()
        self.character_art_dir.mkdir(parents=True, exist_ok=True)
        destination = self.character_art_dir / f"{char_id}.png"
        temporary = self.character_art_dir / f".{char_id}.{uuid4().hex}.tmp"
        try:
            temporary.write_bytes(payload)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)

    def _draw_avatar(self, image: Image.Image, draw: ImageDraw.ImageDraw, left: int, top: int, size: int, path: Path | None, label: str) -> None:
        avatar = self._open_image(path) if path else None
        if avatar is None:
            avatar = Image.new("RGBA", (size, size), "#252948")
            ImageDraw.Draw(avatar).text((size // 2, size // 2), (label or "?")[-2:], font=self._font(24, True), fill=self.TEXT, anchor="mm")
        avatar = ImageOps.fit(avatar, (size, size), method=Image.Resampling.LANCZOS)
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
        image.paste(avatar, (left, top), mask)
        draw.ellipse((left, top, left + size - 1, top + size - 1), outline="#9f99ba", width=3)

    def _draw_circle(self, draw: ImageDraw.ImageDraw, left: int, top: int, size: int, fill: str, outline: str) -> None:
        draw.ellipse((left, top, left + size, top + size), fill=fill, outline=outline, width=3)

    def _awakening(self, draw: ImageDraw.ImageDraw, left: int, top: int, level: int) -> None:
        draw.rounded_rectangle((left, top, left + 66, top + 36), radius=18, fill="#8c78e4")
        draw.text((left + 33, top + 18), f"{level}觉", font=self._font(24, True), fill=self.TEXT, anchor="mm")

    def _draw_footer(self, image: Image.Image) -> None:
        footer = self._open_image(COMMON_TEX / "footer.png")
        if footer is None:
            ImageDraw.Draw(image).text((self.WIDTH // 2, image.height - 40), "Created by GsCore & Copyright by 异环", font=self._font(20), fill=self.TEXT, anchor="mm")
            return
        image.alpha_composite(footer, ((self.WIDTH - footer.width) // 2, image.height - footer.height - 20))

    def _background(self, height: int) -> Image.Image:
        source = self._open_image(COMMON_TEX / "bg4.jpg")
        if source is None:
            return Image.new("RGBA", (self.WIDTH, height), "#15142b")
        return ImageOps.fit(source, (self.WIDTH, height), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))

    def _background_slice(self, height: int) -> Image.Image:
        source = self._open_image(COMMON_TEX / "bg4.jpg")
        if source is None:
            return Image.new("RGBA", (self.WIDTH, height), "#15142b")
        return ImageOps.fit(source, (self.WIDTH, height), method=Image.Resampling.LANCZOS)

    @staticmethod
    def _open_image(path: Path | None) -> Image.Image | None:
        if path is None:
            return None
        try:
            with Image.open(path) as source:
                return source.convert("RGBA")
        except (OSError, ValueError):
            return None

    def _scope_title(self, result: RankResult) -> str:
        if result.scope == "group":
            return "本群"
        if result.scope == "bot":
            return "BOT"
        return "A海岸五群"

    @staticmethod
    def _suit_text(row: RankRow) -> str:
        return f"「{row.suit_name.strip().strip('「」')}」 · {row.suit_pieces}件"

    @staticmethod
    def _group_label(row: RankRow) -> str:
        return row.group_name.strip() or (str(row.group_id) if row.group_id is not None else "未登记群")

    def _font(self, size: int, bold: bool = False) -> ImageFont.ImageFont:
        key = (size, bold)
        if key in self._font_cache:
            return self._font_cache[key]
        candidates = [NTE_FONT, self.font_path]
        if bold:
            candidates += [Path(r"C:\Windows\Fonts\msyhbd.ttc"), Path(r"C:\Windows\Fonts\Dengb.ttf")]
        candidates += [Path(r"C:\Windows\Fonts\msyh.ttc"), Path(r"C:\Windows\Fonts\Deng.ttf")]
        for candidate in candidates:
            if candidate and candidate.exists():
                try:
                    font = ImageFont.truetype(str(candidate), size=size)
                    self._font_cache[key] = font
                    return font
                except OSError:
                    continue
        font = ImageFont.load_default(size=size)
        self._font_cache[key] = font
        return font

    def _ellipsize(self, value: str, font: ImageFont.ImageFont, width: int) -> str:
        value = value or "未命名"
        if self._text_width(value, font) <= width:
            return value
        while value and self._text_width(value + "...", font) > width:
            value = value[:-1]
        return (value or "...") + "..."

    @staticmethod
    def _text_width(value: str, font: ImageFont.ImageFont) -> int:
        box = font.getbbox(value or "")
        return box[2] - box[0]

    def _save(self, image: Image.Image, prefix: str) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        cutoff = datetime.now() - timedelta(hours=self.retention_hours)
        for path in self.output_dir.glob("nte_*.png"):
            try:
                if datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
                    path.unlink(missing_ok=True)
            except OSError:
                continue
        path = self.output_dir / f"{prefix}_{uuid4().hex}.png"
        transparent_rounded_corners(image).save(path, format="PNG", optimize=True)
        return path


def _numeric_id(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


__all__ = ["NTERankRenderer"]
