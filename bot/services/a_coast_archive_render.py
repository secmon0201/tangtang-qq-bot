from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from math import ceil, cos, pi, sin
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont, ImageOps

from bot.services.emoji_text import EmojiTextDraw
from bot.services.character_marks import draw_heart_tail, draw_stitched_mascot
from bot.services.image_style import paste_horizontal_gradient


class ACoastArchiveImageRenderer:
    """Render paged A海岸 message archives as readable QQ images."""

    WIDTH = 900
    ARCHIVE_STARTED_ON = "2026年7月29日"
    PADDING = 36
    HEADER_HEIGHT = 154
    FOOTER_HEIGHT = 62
    ROW_GAP = 5
    HEADER_AVATAR_SIZE = 32
    FRAME_BAND = 6
    FRAME_SPAN = 82
    # AI profile text begins at x=62/72 and can use the full inner card width.
    PROFILE_TEXT_WIDTH = WIDTH - PADDING * 2 - 56
    BACKGROUND = "#fff8fc"
    PANEL = "#fffefe"
    HEADER = "#fff4f8"
    BORDER = "#f0d9e4"
    ROW_ALT = "#fff8fb"
    TEXT = "#342a32"
    MUTED = "#907885"
    ACCENT = "#df5687"
    PROFILE_HEADER_FILL = "#fff1f6"
    PROFILE_HEADER_BORDER = "#f3cfde"
    PROFILE_HEADER_ACCENT = "#df5687"
    TIMELINE_PANEL = "#fffafd"
    TIMELINE_BORDER = "#f0d9e4"
    TIMELINE_GRID = "#f8e8ee"
    TIMELINE_ACCENT = "#d36188"
    TIMELINE_LINE = "#e9789f"
    TIMELINE_POINT = "#ffd7e5"
    TIMELINE_POINT_OUTLINE = "#d36188"
    RADAR_PANEL = "#fffafd"
    RADAR_BORDER = "#eedce8"
    RADAR_ROW = "#fff4f8"
    RADAR_GRID = "#f6e6ee"
    RADAR_AXIS = "#ebd6e2"
    RADAR_FILL = "#f8dce7"
    RADAR_LINE = "#da6b94"
    RADAR_ACCENT = "#d65c8a"
    RADAR_BAR = "#e98aab"
    AI_PANEL = "#fff9fc"
    AI_BORDER = "#f0d9e4"
    AI_CARD = "#fff1f6"
    AI_CARD_BORDER = "#f1d2df"
    AI_ACCENT = "#d65c8a"

    def __init__(
        self,
        output_dir: Path,
        font_path: Path | None = None,
        retention_hours: int = 24,
        timezone_name: str = "Asia/Shanghai",
    ) -> None:
        self.output_dir = output_dir
        self.font_path = font_path
        self.retention_hours = retention_hours
        self.zone = ZoneInfo(timezone_name)
        self._font_cache: dict[tuple[int, bool], ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}

    def render(
        self,
        user_id: int,
        rows: list[Any],
        page: int,
        keyword: str = "",
        avatar_paths: Mapping[int, Path] | None = None,
        total_pages: int = 1,
    ) -> Path:
        title = "A海岸发言搜索" if keyword else "A海岸发言记录"
        subtitle = f"QQ {user_id}  |  第 {page}/{max(1, total_pages)} 页  |  本页 {len(rows)} 条"
        if keyword:
            subtitle += f"  |  关键词：{keyword}"

        title_font = self._font(38, True)
        subtitle_font = self._font(18)
        meta_font = self._font(17)
        content_font = self._font(21)
        content_width = self.WIDTH - self.PADDING * 2 - 28
        prepared = [self._prepare_row(row, content_font, content_width) for row in rows]
        row_heights = [
            51 + len(item[2]) * self._line_height(content_font) + 10 for item in prepared
        ]
        if not prepared:
            row_heights = [130]
        body_height = sum(row_heights) + self.ROW_GAP * max(0, len(row_heights) - 1)
        height = self.HEADER_HEIGHT + body_height + self.FOOTER_HEIGHT + 42

        image = Image.new("RGB", (self.WIDTH, height), self.BACKGROUND)
        draw = EmojiTextDraw(image)
        self._draw_header(
            image,
            draw,
            title,
            subtitle,
            user_id,
            (avatar_paths or {}).get(user_id),
            title_font,
            subtitle_font,
        )
        y = self.HEADER_HEIGHT
        if not prepared:
            draw.rounded_rectangle(
                (self.PADDING, y, self.WIDTH - self.PADDING, y + row_heights[0]),
                radius=12,
                fill=self.PANEL,
                outline=self.BORDER,
                width=2,
            )
            self._draw_centered(draw, self.WIDTH // 2, y + 50, "没有找到符合条件的已存档纯文本发言。", meta_font, self.MUTED)
        else:
            for index, ((metadata, group, lines), row_height) in enumerate(zip(prepared, row_heights)):
                draw.rounded_rectangle(
                    (self.PADDING, y, self.WIDTH - self.PADDING, y + row_height),
                    radius=8,
                    fill=self.PANEL if index % 2 == 0 else self.ROW_ALT,
                    outline=self.BORDER,
                    width=2,
                )
                metadata_y = y + 10
                draw.text((self.PADDING + 12, metadata_y), metadata, font=meta_font, fill=self.ACCENT)
                group = self._ellipsize(group, meta_font, 330)
                group_width = self._text_width(group, meta_font)
                group_x = max(self.PADDING + 12, self.WIDTH - self.PADDING - 12 - group_width)
                draw.text((group_x, metadata_y), group, font=meta_font, fill=self.MUTED)
                text_y = y + 51
                for line in lines:
                    draw.text((self.PADDING + 12, text_y), line, font=content_font, fill=self.TEXT)
                    text_y += self._line_height(content_font)
                y += row_height + self.ROW_GAP

        self._draw_centered(
            draw,
            self.WIDTH // 2,
            height - 31,
            "A海岸 Bot · 发言档案仅展示已存档纯文本",
            self._font(16),
            self.MUTED,
        )
        self._draw_corner_ribbons(draw, height)
        return self._save(image)

    def render_profile(
        self,
        user_id: int,
        nickname: str,
        established_at: str,
        profile_text: str,
        rows: list[Any],
        avatar_path: Path | None = None,
        *,
        include_ai_profile: bool = True,
    ) -> Path:
        """Render local panels, with the AI narrative included only when enabled."""
        if include_ai_profile:
            profile_font = self._font(22)
            summary_font = self._font(20)
            summary_lines, profile_lines = self._profile_sections(
                profile_text,
                summary_font,
                profile_font,
                self.PROFILE_TEXT_WIDTH,
            )
            summary_label = self._profile_lead_label(profile_text)
            closing_lines = self._profile_closing_lines(
                profile_text,
                summary_font,
                self.PROFILE_TEXT_WIDTH,
            )
            line_height = self._line_height(profile_font)
            summary_height = max(82, 38 + len(summary_lines) * self._line_height(summary_font) + 16)
            closing_height = (
                max(82, 38 + len(closing_lines) * self._line_height(summary_font) + 16)
                if closing_lines
                else 0
            )
            profile_height = max(
                264,
                132 + summary_height + len(profile_lines) * line_height + 26
                + (closing_height + 18 if closing_lines else 0),
            )
        header_height = 226
        timeline_height = 322
        radar_height = 396
        gap = 18
        footer_height = 72
        height = header_height + timeline_height + radar_height + gap * 2 + footer_height
        if include_ai_profile:
            height += profile_height + gap

        image = Image.new("RGB", (self.WIDTH, height), self.BACKGROUND)
        draw = EmojiTextDraw(image)
        self._draw_profile_header(image, draw, user_id, nickname, established_at, avatar_path)

        y = header_height
        self._draw_hourly_panel(draw, y, timeline_height, rows)
        y += timeline_height + gap
        self._draw_radar_panel(draw, y, radar_height, rows)
        if include_ai_profile:
            y += radar_height + gap
            self._draw_profile_panel(
                draw,
                y,
                profile_height,
                summary_label,
                summary_lines,
                profile_lines,
                closing_lines,
                summary_height,
                closing_height,
                summary_font,
                profile_font,
            )

        self._draw_centered(
            draw,
            self.WIDTH // 2,
            height - 32,
            "A海岸 Bot · Generated by Secmon · bysecmon",
            self._font(16),
            self.MUTED,
        )
        self._draw_corner_ribbons(draw, height)
        return self._save(image)

    def _draw_profile_header(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        user_id: int,
        nickname: str,
        established_at: str,
        avatar_path: Path | None,
    ) -> None:
        left = self.PADDING
        right = self.WIDTH - self.PADDING
        paste_horizontal_gradient(
            image,
            (left, 24, right, 208),
            "#f3a6bf",
            "#fff3f8",
            radius=16,
            outline=self.PROFILE_HEADER_BORDER,
            outline_width=2,
        )
        title_font = self._font(38, True)
        draw.text((left + 24, 42), "A海岸发言画像", font=title_font, fill=self.TEXT)
        draw_stitched_mascot(draw, self.WIDTH - 118, 18, 50)

        avatar_left, avatar_top, avatar_size = left + 24, 112, 72
        self._draw_avatar(image, draw, avatar_left, avatar_top, avatar_size, avatar_path, str(user_id))
        name_font = self._font(26, True)
        meta_font = self._font(18)
        name = self._ellipsize(nickname or "A海岸用户", name_font, 540)
        draw.text((avatar_left + avatar_size + 18, 116), name, font=name_font, fill=self.TEXT)
        metadata_left = avatar_left + avatar_size + 18
        qq_label = f"QQ：{user_id}"
        draw.text((metadata_left, 166), qq_label, font=meta_font, fill=self.PROFILE_HEADER_ACCENT)
        label = self._format_established_at(established_at)
        established_label = f"画像确立：{label}"
        established_left = metadata_left + self._text_width(qq_label, meta_font) + 42
        draw.text((established_left, 166), established_label, font=meta_font, fill=self.MUTED)

    def _draw_hourly_panel(self, draw: ImageDraw.ImageDraw, top: int, height: int, rows: list[Any]) -> None:
        self._draw_panel(
            draw,
            top,
            height,
            "发言倾向",
            "24 个小时的发言占比（本地计算）",
            self.TIMELINE_PANEL,
            self.TIMELINE_BORDER,
        )
        note = f"发言统计起始于{self.ARCHIVE_STARTED_ON}"
        note_font = self._font(15)
        note_right = self.WIDTH - self.PADDING - 24
        draw.text(
            (note_right - self._text_width(note, note_font), top + 29),
            note,
            font=note_font,
            fill=self.MUTED,
        )
        left, right = self.PADDING + 70, self.WIDTH - self.PADDING - 32
        chart_top, chart_bottom = top + 104, top + height - 54
        counts = [0] * 24
        for row in rows:
            hour = self._hour(row)
            if hour is not None:
                counts[hour] += 1
        total = sum(counts)
        ratios = [value / total if total else 0.0 for value in counts]
        maximum = max(ratios, default=0.0)
        ceiling = max(0.05, ceil(maximum * 20) / 20)
        grid_font = self._font(13)
        for step in range(5):
            fraction = step / 4
            y = chart_bottom - (chart_bottom - chart_top) * fraction
            draw.line((left, y, right, y), fill=self.TIMELINE_GRID, width=1)
            label = f"{ceiling * fraction:.0%}"
            draw.text((self.PADDING + 8, y - 9), label, font=grid_font, fill=self.MUTED)
        draw.line((left, chart_top, left, chart_bottom), fill=self.TIMELINE_ACCENT, width=2)
        draw.line((left, chart_bottom, right, chart_bottom), fill=self.TIMELINE_ACCENT, width=2)
        point_font = self._font(11)
        points: list[tuple[float, float]] = []
        span = right - left
        for hour, ratio in enumerate(ratios):
            x = left + span * hour / 23
            y = chart_bottom - (chart_bottom - chart_top) * min(1.0, ratio / ceiling)
            points.append((x, y))
            self._draw_centered(draw, int(x), chart_bottom + 20, f"{hour:02d}", point_font, self.MUTED)
        if len(points) > 1:
            draw.line(points, fill=self.TIMELINE_LINE, width=3, joint="curve")
        for x, y in points:
            draw.ellipse(
                (x - 4, y - 4, x + 4, y + 4),
                fill=self.TIMELINE_POINT,
                outline=self.TIMELINE_POINT_OUTLINE,
                width=1,
            )

    def _draw_radar_panel(self, draw: ImageDraw.ImageDraw, top: int, height: int, rows: list[Any]) -> None:
        metrics = self._style_metrics(rows)
        maximum = self._metric_ceiling(score for _, score in metrics)
        self._draw_panel(
            draw,
            top,
            height,
            "表达小雷达",
            f"六维文本命中条数（本地计算，动态最高刻度 {maximum} 条）",
            self.RADAR_PANEL,
            self.RADAR_BORDER,
        )
        center_x, center_y, radius = 246, top + 235, 95
        angles = [-pi / 2 + index * pi / 3 for index in range(6)]
        for level in (0.25, 0.5, 0.75, 1.0):
            points = [
                (center_x + cos(angle) * radius * level, center_y + sin(angle) * radius * level)
                for angle in angles
            ]
            draw.line(points + [points[0]], fill=self.RADAR_GRID, width=1)
        for angle in angles:
            draw.line(
                (center_x, center_y, center_x + cos(angle) * radius, center_y + sin(angle) * radius),
                fill=self.RADAR_AXIS,
                width=1,
            )
        values = [score / maximum for _, score in metrics]
        polygon = [
            (center_x + cos(angle) * radius * value, center_y + sin(angle) * radius * value)
            for angle, value in zip(angles, values)
        ]
        draw.polygon(polygon, fill=self.RADAR_FILL)
        draw.line(polygon + [polygon[0]], fill=self.RADAR_LINE, width=3, joint="curve")
        label_font = self._font(16, True)
        for angle, (label, score) in zip(angles, metrics):
            x = center_x + cos(angle) * (radius + 32)
            y = center_y + sin(angle) * (radius + 32)
            self._draw_centered(draw, int(x), int(y - 8), label, label_font, self.TEXT)
            self._draw_centered(draw, int(x), int(y + 14), f"{score}", self._font(14), self.RADAR_ACCENT)

        right_left = 472
        chip_font = self._font(18, True)
        detail_font = self._font(17)
        for index, (label, score) in enumerate(metrics):
            row_top = top + 102 + index * 42
            draw.rounded_rectangle(
                (right_left, row_top, self.WIDTH - self.PADDING - 22, row_top + 31),
                radius=8,
                fill=self.RADAR_ROW,
            )
            draw.text((right_left + 12, row_top + 5), label, font=chip_font, fill=self.TEXT)
            bar_left, bar_right = right_left + 126, self.WIDTH - self.PADDING - 72
            draw.rounded_rectangle(
                (bar_left, row_top + 10, bar_right, row_top + 21), radius=5, fill=self.RADAR_GRID
            )
            fill_right = bar_left + (bar_right - bar_left) * score / maximum
            draw.rounded_rectangle(
                (bar_left, row_top + 10, fill_right, row_top + 21), radius=5, fill=self.RADAR_BAR
            )
            draw.text(
                (self.WIDTH - self.PADDING - 60, row_top + 5),
                str(score),
                font=detail_font,
                fill=self.RADAR_ACCENT,
            )

    def _draw_profile_panel(
        self,
        draw: ImageDraw.ImageDraw,
        top: int,
        height: int,
        summary_label: str,
        summary_lines: list[str],
        lines: list[str],
        closing_lines: list[str],
        summary_height: int,
        closing_height: int,
        summary_font: ImageFont.ImageFont,
        font: ImageFont.ImageFont,
    ) -> None:
        self._draw_panel(
            draw,
            top,
            height,
            "AI 发言画像",
            "仅基于已存档 A 海岸纯文本与本地统计生成",
            self.AI_PANEL,
            self.AI_BORDER,
        )
        summary_top = top + 92
        summary_bottom = summary_top + summary_height
        draw.rounded_rectangle(
            (self.PADDING + 20, summary_top, self.WIDTH - self.PADDING - 20, summary_bottom),
            radius=10,
            fill=self.AI_CARD,
            outline=self.AI_CARD_BORDER,
            width=1,
        )
        draw.text(
            (self.PADDING + 36, summary_top + 14),
            summary_label,
            font=self._font(18, True),
            fill=self.AI_ACCENT,
        )
        y = summary_top + 42
        for line in summary_lines:
            draw.text((self.PADDING + 36, y), line, font=summary_font, fill=self.TEXT)
            y += self._line_height(summary_font)
        y = summary_bottom + 22
        for line in lines:
            if line:
                draw.text((self.PADDING + 26, y), line, font=font, fill=self.TEXT)
            y += self._line_height(font)
        if closing_lines:
            closing_top = y + 20
            closing_bottom = closing_top + closing_height
            draw.rounded_rectangle(
                (self.PADDING + 20, closing_top, self.WIDTH - self.PADDING - 20, closing_bottom),
                radius=10,
                fill=self.AI_CARD,
                outline=self.AI_CARD_BORDER,
                width=1,
            )
            draw.text(
                (self.PADDING + 36, closing_top + 14),
                "糖糖总评",
                font=self._font(18, True),
                fill=self.AI_ACCENT,
            )
            y = closing_top + 42
            for line in closing_lines:
                draw.text((self.PADDING + 36, y), line, font=summary_font, fill=self.TEXT)
                y += self._line_height(summary_font)

    def _draw_panel(
        self,
        draw: ImageDraw.ImageDraw,
        top: int,
        height: int,
        title: str,
        subtitle: str,
        fill: str | None = None,
        border: str | None = None,
    ) -> None:
        left, right = self.PADDING, self.WIDTH - self.PADDING
        draw.rounded_rectangle(
            (left, top, right, top + height),
            radius=14,
            fill=fill or self.PANEL,
            outline=border or self.BORDER,
            width=2,
        )
        draw.text((left + 24, top + 20), title, font=self._font(26, True), fill=self.TEXT)
        draw.text((left + 24, top + 56), subtitle, font=self._font(16), fill=self.MUTED)

    def _profile_lines(self, value: str, font: ImageFont.ImageFont, width: int) -> list[str]:
        lines: list[str] = []
        for paragraph in value.strip().splitlines() or ["暂无可用画像。"]:
            paragraph = paragraph.strip()
            if not paragraph:
                lines.append("")
                continue
            lines.extend(self._wrap_text(paragraph, font, width))
        return lines or ["暂无可用画像。"]

    def _profile_sections(
        self,
        value: str,
        summary_font: ImageFont.ImageFont,
        profile_font: ImageFont.ImageFont,
        width: int,
    ) -> tuple[list[str], list[str]]:
        paragraphs = [paragraph.strip() for paragraph in value.strip().splitlines() if paragraph.strip()]
        if not paragraphs:
            return ["目前记录还不多，糖糖先留个小位置。"], []
        summary = paragraphs[0]
        if summary.startswith("糖糖总评："):
            summary = summary.removeprefix("糖糖总评：").strip()
        elif summary.startswith("糖糖开篇："):
            summary = summary.removeprefix("糖糖开篇：").strip()
        detail_paragraphs = paragraphs[1:]
        if detail_paragraphs and detail_paragraphs[-1].startswith("糖糖总评："):
            detail_paragraphs = detail_paragraphs[:-1]
        detail = "\n\n".join(detail_paragraphs)
        return (
            self._wrap_text(summary, summary_font, width),
            self._profile_lines(detail, profile_font, width) if detail else [],
        )

    @staticmethod
    def _profile_lead_label(value: str) -> str:
        first = next((line.strip() for line in value.splitlines() if line.strip()), "")
        return "糖糖开篇" if first.startswith("糖糖开篇：") else "糖糖总评"

    def _profile_closing_lines(
        self, value: str, font: ImageFont.ImageFont, width: int
    ) -> list[str]:
        paragraphs = [paragraph.strip() for paragraph in value.strip().splitlines() if paragraph.strip()]
        if len(paragraphs) < 2 or not paragraphs[-1].startswith("糖糖总评："):
            return []
        closing = paragraphs[-1].removeprefix("糖糖总评：").strip()
        return self._wrap_text(closing, font, width) if closing else []

    def _style_metrics(self, rows: list[Any]) -> list[tuple[str, int]]:
        contents = [" ".join(str(self._value(row, "content", "") or "").split()) for row in rows]
        contents = [content for content in contents if content]
        total = len(contents)
        if not total:
            return [("复读魂", 0), ("好奇雷达", 0), ("情绪电波", 0), ("小作文", 0), ("接话欲", 0), ("情报站", 0)]
        repeated = sum(count for count in Counter(contents).values() if count > 1)
        question = sum(1 for content in contents if any(mark in content for mark in "?？") or "吗" in content or "怎么" in content)
        emotion = sum(1 for content in contents if any(mark in content for mark in "!！～~哈哈呵呵呜哭笑"))
        interaction = sum(1 for content in contents if any(word in content for word in ("你", "您", "大家", "各位", "谢谢", "早安", "晚安")))
        information = sum(1 for content in contents if any(word in content for word in ("直播", "公告", "更新", "数据", "时间", "消息", "据说")) or any(char.isdigit() for char in content))
        return [
            ("复读魂", repeated),
            ("好奇雷达", question),
            ("情绪电波", emotion),
            ("小作文", sum(1 for content in contents if len(content) >= 60)),
            ("接话欲", interaction),
            ("情报站", information),
        ]

    @staticmethod
    def _metric_ceiling(values) -> int:
        maximum = max((max(0, int(value)) for value in values), default=0)
        if maximum <= 5:
            return 5
        base = 1
        while base * 10 < maximum:
            base *= 10
        for factor in (1, 2, 5, 10):
            ceiling = base * factor
            if maximum <= ceiling:
                return ceiling
        return maximum

    def _hour(self, row: Any) -> int | None:
        value = self._value(row, "hour", None)
        if value is not None:
            try:
                hour = int(value)
            except (TypeError, ValueError):
                return None
            return hour if 0 <= hour <= 23 else None
        raw = str(self._value(row, "occurred_at", "") or "")
        try:
            return datetime.fromisoformat(raw).hour
        except ValueError:
            return None

    def _format_established_at(self, value: str) -> str:
        if not value:
            return "尚未确立"
        try:
            moment = datetime.fromisoformat(value)
            if moment.tzinfo is not None:
                moment = moment.astimezone(self.zone)
            return moment.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return value[:16]

    def _prepare_row(
        self, row: Any, font: ImageFont.ImageFont, max_width: int
    ) -> tuple[str, str, list[str]]:
        content = " ".join(str(self._value(row, "content", "") or "").split())
        if len(content) > 220:
            content = content[:217] + "..."
        metadata = str(self._value(row, "occurred_at", ""))
        group = str(self._value(row, "group_name", "") or self._value(row, "group_id", ""))
        return metadata, group, self._wrap_text(content, font, max_width)

    def _draw_header(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        title: str,
        subtitle: str,
        user_id: int,
        avatar_path: Path | None,
        title_font: ImageFont.ImageFont,
        subtitle_font: ImageFont.ImageFont,
    ) -> None:
        paste_horizontal_gradient(
            image,
            (self.PADDING, 24, self.WIDTH - self.PADDING, self.HEADER_HEIGHT - 16),
            "#f3a6bf",
            "#fff5f9",
            radius=18,
            outline=self.BORDER,
            outline_width=2,
        )
        draw.text((self.PADDING + 22, 42), title, font=title_font, fill=self.TEXT)
        avatar_left = self.PADDING + 22
        avatar_top = 94
        self._draw_avatar(
            image, draw, avatar_left, avatar_top, self.HEADER_AVATAR_SIZE, avatar_path, str(user_id)
        )
        available_width = self.WIDTH - self.PADDING * 2 - 220
        draw.text(
            (avatar_left + self.HEADER_AVATAR_SIZE + 10, 99),
            self._ellipsize(subtitle, subtitle_font, available_width),
            font=subtitle_font,
            fill=self.MUTED,
        )
        draw_stitched_mascot(draw, self.WIDTH - 118, 16, 50)

    def _wrap_text(self, value: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
        if not value:
            return [""]
        lines: list[str] = []
        current = ""
        for character in value:
            candidate = current + character
            if current and self._text_width(candidate, font) > max_width:
                lines.append(current)
                current = character
            else:
                current = candidate
        if current:
            lines.append(current)
        return lines or [""]

    def _draw_avatar(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        left: int,
        top: int,
        size: int,
        avatar_path: Path | None,
        label: str,
    ) -> None:
        avatar = None
        if avatar_path and avatar_path.exists():
            try:
                with Image.open(avatar_path) as source:
                    avatar = ImageOps.fit(
                        source.convert("RGB"), (size, size), method=Image.Resampling.LANCZOS
                    )
            except (OSError, ValueError):
                avatar = None
        if avatar is not None:
            mask = Image.new("L", (size, size), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
            image.paste(avatar, (left, top), mask)
            draw.ellipse((left, top, left + size - 1, top + size - 1), outline=self.BORDER, width=1)
            return
        draw.ellipse((left, top, left + size - 1, top + size - 1), fill="#fde1ec", outline="#dfa1b7", width=1)
        self._draw_centered(draw, left + size // 2, top + size // 2, label[-4:] or "?", self._font(11, True), self.ACCENT)

    def _draw_corner_ribbons(self, draw: ImageDraw.ImageDraw, height: int) -> None:
        inset, span = 18, self.FRAME_SPAN
        draw.line((inset, inset + span, inset, inset, inset + span, inset), fill="#ff91b4", width=self.FRAME_BAND, joint="curve")
        draw.line((self.WIDTH - inset - 42, inset, self.WIDTH - inset, inset, self.WIDTH - inset, inset + 26), fill="#f7c8d8", width=3, joint="curve")
        draw.line((inset, height - inset - 26, inset, height - inset, inset + 44, height - inset), fill="#f7c8d8", width=3, joint="curve")
        self._draw_bunny(draw, self.WIDTH - 64, height - 58, 17)
        draw_heart_tail(draw, 78, height - 42)

    def _draw_bunny(self, draw: ImageDraw.ImageDraw, center_x: int, center_y: int, size: int) -> None:
        ink = "#dc789c"
        draw.ellipse((center_x - size, center_y - size // 2, center_x + size, center_y + size + size // 2), fill="#fffafd", outline=ink, width=2)
        draw.ellipse((center_x - size + 2, center_y - size * 2, center_x - 1, center_y + 2), fill="#fffafd", outline=ink, width=2)
        draw.ellipse((center_x + 1, center_y - size * 2, center_x + size - 2, center_y + 2), fill="#fffafd", outline=ink, width=2)

    @staticmethod
    def _draw_heart(
        draw: ImageDraw.ImageDraw, center_x: int, center_y: int, size: int, fill: str
    ) -> None:
        radius = max(3, size // 4)
        draw.ellipse((center_x - radius * 2, center_y - radius, center_x, center_y + radius), fill=fill)
        draw.ellipse((center_x, center_y - radius, center_x + radius * 2, center_y + radius), fill=fill)
        draw.polygon(
            [(center_x - radius * 2, center_y), (center_x + radius * 2, center_y), (center_x, center_y + size)],
            fill=fill,
        )

    def _ellipsize(self, value: str, font: ImageFont.ImageFont, max_width: int) -> str:
        if self._text_width(value, font) <= max_width:
            return value
        suffix = "..."
        while value and self._text_width(value + suffix, font) > max_width:
            value = value[:-1]
        return value + suffix

    def _font(self, size: int, bold: bool = False):
        key = (size, bold)
        if key in self._font_cache:
            return self._font_cache[key]
        candidates: list[Path] = []
        if self.font_path:
            candidates.append(self.font_path)
        if bold:
            candidates.extend((Path(r"C:\Windows\Fonts\msyhbd.ttc"), Path(r"C:\Windows\Fonts\Dengb.ttf")))
        candidates.extend((
            Path(r"C:\Windows\Fonts\msyh.ttc"),
            Path(r"C:\Windows\Fonts\Deng.ttf"),
            Path(r"C:\Windows\Fonts\simhei.ttf"),
            Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        ))
        for candidate in candidates:
            if candidate.exists():
                try:
                    font = ImageFont.truetype(str(candidate), size=size)
                    self._font_cache[key] = font
                    return font
                except OSError:
                    continue
        font = ImageFont.load_default(size=size)
        self._font_cache[key] = font
        return font

    @staticmethod
    def _value(row: Any, key: str, default: Any = None) -> Any:
        if isinstance(row, Mapping):
            return row.get(key, default)
        try:
            return row[key]
        except (IndexError, KeyError, TypeError):
            return default

    @staticmethod
    def _text_width(value: str, font: ImageFont.ImageFont) -> int:
        bbox = font.getbbox(value)
        return bbox[2] - bbox[0]

    @staticmethod
    def _line_height(font: ImageFont.ImageFont) -> int:
        bbox = font.getbbox("Ag")
        return max(28, bbox[3] - bbox[1] + 9)

    @staticmethod
    def _draw_centered(
        draw: ImageDraw.ImageDraw,
        center_x: int,
        center_y: int,
        text: str,
        font: ImageFont.ImageFont,
        fill: str,
    ) -> None:
        bbox = draw.textbbox((0, 0), text, font=font)
        draw.text((center_x - (bbox[2] - bbox[0]) // 2, center_y - (bbox[3] - bbox[1]) // 2), text, font=font, fill=fill)

    def _save(self, image: Image.Image) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        cutoff = datetime.now(self.zone) - timedelta(hours=self.retention_hours)
        for path in self.output_dir.glob("a_coast_archive_*.png"):
            try:
                if datetime.fromtimestamp(path.stat().st_mtime, self.zone) < cutoff:
                    path.unlink(missing_ok=True)
            except OSError:
                continue
        path = self.output_dir / f"a_coast_archive_{uuid4().hex}.png"
        image.save(path, format="PNG", optimize=True)
        return path
