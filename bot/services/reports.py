from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont, ImageOps

from bot.services.emoji_text import EmojiTextDraw
from bot.services.character_marks import draw_heart_tail, draw_stitched_mascot
from bot.services.image_style import paste_horizontal_gradient


def report_group_marker(index: int) -> str:
    """Use font-stable circled digits in Pillow reports instead of emoji glyphs."""
    return chr(0x245F + index) if 1 <= index <= 10 else f"[{index}]"


ReportComparisonMode = Literal["all", "source"]


class ReportRenderer:
    """Create local, deterministic poster-style reports with Pillow only."""

    # Portrait reports read naturally in QQ and leave no wide empty margins.
    WIDTH = 768
    LEFT = 52
    RIGHT = 52
    HEADER_HEIGHT = 214
    FOOTER_HEIGHT = 64
    ROW_GAP = 16
    GROUP_OVERVIEW_ROW_HEIGHT = 158
    FRAME_BAND = 6
    FRAME_SPAN = 82
    CONTENT_FRAME_GAP = 16

    BACKGROUND = "#fffafd"
    PANEL = "#ffffff"
    HEADER = "#fff5f9"
    HEADER_SUBTITLE = "#9b7c8c"
    TABLE_HEADER = "#f25c91"
    TABLE_LINE = "#f2dce6"
    ROW_ALT = "#fff8fb"
    TEXT = "#342a32"
    MUTED = "#907885"
    ACCENT = "#f35d91"
    LABEL_TEXT = "#dc4f81"
    KEY_FILL = "#ffe5ef"
    LAVENDER = "#d995b4"
    INK = "#342a32"
    DARK = "#342a32"
    CHIP = "#ffeef5"
    SOFT_LAVENDER = "#fff1f6"
    SOFT_MINT = "#eef9f4"
    def __init__(
        self,
        output_dir: Path,
        font_path: Path | None = None,
        retention_hours: int = 24,
        timezone_name: str = "Asia/Shanghai",
        command_prefix: str = "",
    ) -> None:
        self.output_dir = output_dir
        self.font_path = font_path
        self.retention_hours = retention_hours
        self.zone = ZoneInfo(timezone_name)
        self.command_prefix = command_prefix
        self.activity_help_hint = f"活动使用方法：{command_prefix}活动帮助"
        self._font_cache: dict[tuple[int, bool], ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}

    # General reports -----------------------------------------------------

    def render_duplicate(
        self,
        items: Sequence[Mapping[str, Any]],
        total_count: int,
        page: int,
        total_pages: int,
        avatar_paths: Mapping[int, Path] | None = None,
        group_labels: Sequence[tuple[int, str]] | None = None,
        comparison_mode: ReportComparisonMode = "source",
    ) -> Path:
        subtitle = f"发现 {total_count} 名重复成员"
        if comparison_mode == "all":
            subtitle += "  |  全部指定群互相比较"
        else:
            subtitle += "  |  起点群对比目标群"
        if total_pages > 1:
            subtitle += f"  |  第 {page}/{total_pages} 页"

        labels = tuple(group_labels or ())
        legend_height = self._legend_height(labels, comparison_mode)
        row_data: list[tuple[int, str, str, str]] = []
        for item in items:
            user_id = int(item.get("user_id", 0))
            groups = self._group_markers(item.get("groups", ()), labels)
            row_data.append((user_id, str(item.get("nickname") or "未获取昵称"), str(user_id), groups))
        if not row_data:
            row_data = [(0, "暂无重复成员", "", "本次查询没有发现重复成员")]

        row_heights = [
            self._duplicate_row_height(nickname, groups) for _, nickname, _, groups in row_data
        ]
        # The drawing loop advances after both the legend and every member card.
        # Reserve those gaps in the canvas so the final row cannot overlap the footer.
        body_height = (
            legend_height
            + (self.ROW_GAP if labels else 0)
            + sum(height + self.ROW_GAP for height in row_heights)
        )
        image, draw, y = self._new_report("跨群查重", subtitle, body_height)
        if labels:
            y = self._draw_group_legend(draw, y, labels, comparison_mode)
            # Compatibility layout anchor retained for existing integrations/tests.
            self._table_header(draw, y, ())
        for index, ((user_id, nickname, qq, groups), height) in enumerate(zip(row_data, row_heights)):
            self._draw_member_row(
                image, draw, y, height, index, user_id, nickname, qq, groups,
                avatar_paths=(avatar_paths or {}), right_label="重复群",
            )
            y += height + self.ROW_GAP
        return self._save(image, "duplicate")

    def render_ranking(
        self,
        rows: Sequence[Any],
        title: str,
        subtitle: str = "按发言数降序，QQ 号升序",
        avatar_paths: Mapping[int, Path] | None = None,
        show_group_labels: bool = False,
        group_totals: Sequence[Any] | None = None,
        group_avatar_paths: Mapping[int, Path] | None = None,
        daily_totals: Sequence[Any] | None = None,
    ) -> Path:
        values = list(rows)
        totals = list(group_totals or ())
        trend = list(daily_totals or ())
        row_height = 100 if show_group_labels else 80
        row_gap = 10
        ranking_height = max(1, len(values)) * row_height + max(0, len(values) - 1) * row_gap
        chart_height = self._ranking_chart_height(totals, trend)
        body_height = ranking_height + chart_height + (16 if chart_height else 0)
        image, draw, y = self._new_report(title, subtitle, body_height + 18)
        if not values:
            self._draw_empty_card(draw, y, "暂无可展示的发言数据")
            y += ranking_height
        else:
            for index, row in enumerate(values, 1):
                rank = int(self._value(row, "rank", index) or index)
                user_id = int(self._value(row, "user_id", 0) or 0)
                nickname = str(self._value(row, "nickname", "") or "未获取昵称")
                count = str(self._value(row, "message_count", 0) or 0)
                group_labels = str(self._value(row, "group_labels", "") or "")
                self._draw_ranking_row(
                    image,
                    draw,
                    y,
                    rank,
                    user_id,
                    nickname,
                    count,
                    (avatar_paths or {}),
                    group_labels,
                    show_group_labels,
                )
                y += row_height + row_gap
        if totals:
            self._draw_group_message_totals(image, draw, y + 6, totals, group_avatar_paths or {})
        elif trend:
            self._draw_daily_message_trend(draw, y + 6, trend)
        return self._save(image, "ranking")

    def _draw_group_message_totals_legacy(self, draw: ImageDraw.ImageDraw, top: int, rows: Sequence[Any]) -> None:
        height = 64 + len(rows) * 34
        left, right = self.LEFT, self.WIDTH - self.RIGHT
        draw.rounded_rectangle(
            (left, top, right, top + height), radius=10, fill=self.KEY_FILL, outline=self.TABLE_LINE, width=1
        )
        draw.text((left + 18, top + 14), "A海岸群发言总数", font=self._font(20, True), fill=self.LABEL_TEXT)
        name_font = self._font(18)
        count_font = self._font(18, True)
        for index, row in enumerate(rows):
            row_top = top + 48 + index * 34
            name = self._ellipsize(str(self._value(row, "group_name", "") or "未命名群"), name_font, 480)
            count = f"{int(self._value(row, 'message_count', 0) or 0)} 条"
            draw.text((left + 18, row_top), name, font=name_font, fill=self.TEXT)
            count_width = self._text_width(count, count_font)
            draw.text((right - 18 - count_width, row_top), count, font=count_font, fill=self.ACCENT)

    @staticmethod
    def _ranking_chart_height(group_rows: Sequence[Any], daily_rows: Sequence[Any]) -> int:
        if group_rows:
            return 304
        if daily_rows:
            return 264
        return 0

    def _draw_group_message_totals(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        top: int,
        rows: Sequence[Any],
        avatar_paths: Mapping[int, Path],
    ) -> None:
        """Render the five A Coast group totals as an identity-rich line chart."""
        height = 304
        left, right = self.LEFT, self.WIDTH - self.RIGHT
        draw.rounded_rectangle(
            (left, top, right, top + height), radius=10, fill=self.KEY_FILL, outline=self.TABLE_LINE, width=1
        )
        draw.text((left + 18, top + 14), "A海岸群发言数", font=self._font(20, True), fill=self.LABEL_TEXT)
        self._draw_message_line_chart(
            draw,
            top,
            [int(self._value(row, "message_count", 0) or 0) for row in rows],
            [str(self._value(row, "group_name", "") or "未命名群") for row in rows],
            "group",
        )
        plot_left, plot_right, plot_bottom = left + 68, right - 20, top + 190
        for index, row in enumerate(rows):
            x = self._chart_x(index, len(rows), plot_left, plot_right)
            group_id = int(self._value(row, "group_id", 0) or 0)
            self._draw_avatar_at(image, draw, x - 16, plot_bottom + 18, 32, avatar_paths.get(group_id), str(group_id))
            name = self._ellipsize(str(self._value(row, "group_name", "") or "未命名群"), self._font(14), 128)
            name_font = self._font(12)
            name = self._ellipsize(name, name_font, 112)
            self._draw_centered(draw, x, plot_bottom + 62, name, name_font, self.MUTED)

    def _draw_daily_message_trend(self, draw: ImageDraw.ImageDraw, top: int, rows: Sequence[Any]) -> None:
        height = 264
        left, right = self.LEFT, self.WIDTH - self.RIGHT
        draw.rounded_rectangle(
            (left, top, right, top + height), radius=10, fill="#fff9fb", outline=self.TABLE_LINE, width=1
        )
        draw.text((left + 18, top + 14), "本群近 7 日发言趋势", font=self._font(20, True), fill=self.LABEL_TEXT)
        self._draw_message_line_chart(
            draw,
            top,
            [int(self._value(row, "message_count", 0) or 0) for row in rows],
            [str(self._value(row, "day", ""))[-5:] for row in rows],
            "day",
        )

    def _draw_message_line_chart(
        self,
        draw: ImageDraw.ImageDraw,
        top: int,
        values: Sequence[int],
        labels: Sequence[str],
        label_kind: Literal["group", "day"],
    ) -> None:
        left, right = self.LEFT, self.WIDTH - self.RIGHT
        plot_left, plot_right = left + 68, right - 20
        plot_top, plot_bottom = top + 62, top + 190
        step, upper = self._chart_scale(max(values, default=0))
        grid_font = self._font(13)
        for index in range(5):
            value = upper - step * index
            y = round(plot_top + (plot_bottom - plot_top) * index / 4)
            draw.line((plot_left, y, plot_right, y), fill="#f0dde6", width=1)
            label = str(value)
            draw.text((plot_left - 10 - self._text_width(label, grid_font), y - 8), font=grid_font, text=label, fill="#a88b99")
        draw.line((plot_left, plot_top, plot_left, plot_bottom), fill="#dcaabc", width=1)
        draw.line((plot_left, plot_bottom, plot_right, plot_bottom), fill="#dcaabc", width=1)

        point_font = self._font(14, True)
        previous: tuple[int, int] | None = None
        count = len(values)
        for index, value in enumerate(values):
            x = self._chart_x(index, count, plot_left, plot_right)
            y = round(plot_bottom - (plot_bottom - plot_top) * value / upper)
            if previous is not None:
                draw.line((previous[0], previous[1], x, y), fill=self.ACCENT, width=3)
            draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill=self.ACCENT, outline="#d9477b", width=1)
            value_text = str(value)
            label_width = self._text_width(value_text, point_font) + 16
            label_top = max(plot_top + 3, y - 31)
            draw.rounded_rectangle(
                (x - label_width // 2, label_top, x + label_width // 2, label_top + 22),
                radius=11,
                fill="#fff5f9",
                outline="#f4c5d7",
                width=1,
            )
            self._draw_centered(draw, x, label_top + 11, value_text, point_font, self.LABEL_TEXT)
            if label_kind == "day":
                self._draw_centered(draw, x, plot_bottom + 25, labels[index], self._font(14), self.MUTED)
            previous = (x, y)

    @staticmethod
    def _chart_x(index: int, count: int, left: int, right: int) -> int:
        if count <= 1:
            return (left + right) // 2
        inset = min(36, (right - left) // 5)
        return round(left + inset + (right - left - inset * 2) * index / (count - 1))

    @staticmethod
    def _chart_scale(maximum: int) -> tuple[int, int]:
        """Choose a readable 1/2/5 scale with four horizontal intervals."""
        maximum = max(0, int(maximum))
        if maximum <= 4:
            return 1, 4
        magnitude = 10 ** (len(str(maximum)) - 1)
        for multiplier in (1, 2, 5, 10):
            step = magnitude * multiplier
            if step * 4 >= maximum:
                return step, step * 4
        return magnitude * 10, magnitude * 40

    def render_status(
        self,
        group_rows: Sequence[Any],
        health_lines: Sequence[str],
        group_avatar_paths: Mapping[int, Path] | None = None,
    ) -> Path:
        groups = [dict(row) for row in group_rows]
        health_rows: list[tuple[str, str, str]] = []
        for line in health_lines:
            label, separator, value = str(line).partition("：")
            health_rows.append((label if separator else "接口状态", value if separator else line, ""))
        if not groups and not health_rows:
            health_rows = [("机器人状态", "暂无已连接的管理群", "")]
        body_height = (
            len(groups) * (self.GROUP_OVERVIEW_ROW_HEIGHT + self.ROW_GAP)
            + max(0, len(groups) - 1) * self.ROW_GAP
            + len(health_rows) * 132
            + max(0, len(health_rows) - 1) * self.ROW_GAP
            + 18
        )
        image, draw, y = self._new_report(
            "机器人状态", f"本地数据快照  |  {datetime.now(self.zone):%Y-%m-%d %H:%M}", body_height + 18
        )
        for index, row in enumerate(groups):
            enabled = bool(self._value(row, "stats_enabled"))
            role = str(self._value(row, "stats_role") or "未知")
            self._draw_group_overview_row(
                image,
                draw,
                y,
                {
                    **row,
                    "detail": str(row.get("detail") or f"统计权限：{role}"),
                    "tag": str(row.get("tag") or ("统计开启" if enabled else "统计关闭")),
                },
                group_avatar_paths or {},
                index,
                self.GROUP_OVERVIEW_ROW_HEIGHT,
            )
            y += self.GROUP_OVERVIEW_ROW_HEIGHT + self.ROW_GAP
        if groups and health_rows:
            y += 8
        for label, value, state in health_rows:
            self._draw_info_row(draw, y, 132, label, value, state)
            y += 132 + self.ROW_GAP
        return self._save(image, "status")

    def render_admin_panel(
        self,
        title: str,
        subtitle: str,
        sections: Sequence[tuple[str, str, str]],
    ) -> Path:
        values = list(sections) or [("管理员", "暂无内容", "")]
        heights = [self._help_card_height(commands, note) for _, commands, note in values]
        body_height = sum(heights) + max(0, len(heights) - 1) * 16 + 18
        image, draw, y = self._new_report(title, subtitle, body_height)
        for index, ((heading, commands, note), height) in enumerate(zip(values, heights), 1):
            self._draw_help_card(draw, y, index, heading, commands, note)
            y += height + 16
        return self._save(image, "admin_panel")

    def render_user_help(
        self,
        title: str,
        subtitle: str,
        categories: Sequence[tuple[str, str, Sequence[tuple[str, str, str]]]],
    ) -> Path:
        """Render the public manual as nested category and command cards."""
        values = [(heading, note, list(entries)) for heading, note, entries in categories]
        heights = [self._user_help_category_height(note, entries) for _, note, entries in values]
        body_height = sum(heights) + max(0, len(heights) - 1) * 18 + 18
        image, draw, y = self._new_report(title, subtitle, body_height)
        command_number = 1
        for (heading, note, entries), height in zip(values, heights):
            self._draw_user_help_category(draw, y, height, heading, note, entries, command_number)
            command_number += len(entries)
            y += height + 18
        return self._save(image, "user_help")

    def render_group_overview(
        self,
        title: str,
        subtitle: str,
        rows: Sequence[Mapping[str, Any]],
        group_avatar_paths: Mapping[int, Path] | None = None,
    ) -> Path:
        values = list(rows)
        row_heights = [self._group_overview_row_height(row) for row in values]
        body_height = (
            (sum(row_heights) if row_heights else self.GROUP_OVERVIEW_ROW_HEIGHT)
            + max(0, len(values) - 1) * self.ROW_GAP
            + 18
        )
        image, draw, y = self._new_report(title, subtitle, body_height)
        if not values:
            self._draw_empty_card(draw, y, "暂无可展示的群")
            return self._save(image, "group_overview")
        for index, (row, row_height) in enumerate(zip(values, row_heights)):
            self._draw_group_overview_row(image, draw, y, row, group_avatar_paths or {}, index, row_height)
            y += row_height + self.ROW_GAP
        return self._save(image, "group_overview")

    def render_whitelist(
        self,
        rows: Sequence[Any],
        avatar_paths: Mapping[int, Path] | None = None,
        title: str = "查重白名单",
        subtitle: str | None = None,
    ) -> Path:
        values = list(rows)
        prepared_rows = [
            (
                int(self._value(row, "user_id", 0) or 0),
                str(self._value(row, "nickname") or "未获取昵称"),
                str(self._value(row, "note") or "未填写备注"),
            )
            for row in values
        ]
        row_heights = [self._whitelist_row_height(note) for _, _, note in prepared_rows]
        body_height = sum(row_heights) + max(0, len(row_heights) - 1) * self.ROW_GAP
        image, draw, y = self._new_report(title, subtitle or f"共 {len(values)} 名用户，不参与查重", body_height + 18)
        if not values:
            self._draw_empty_card(draw, y, "白名单目前为空")
            return self._save(image, "whitelist")
        for index, ((user_id, nickname, note), height) in enumerate(zip(prepared_rows, row_heights)):
            self._draw_whitelist_row(
                image,
                draw,
                y,
                height,
                index,
                user_id,
                nickname,
                note,
                avatar_paths or {},
            )
            y += height + self.ROW_GAP
        return self._save(image, "whitelist")

    # Activity posters ----------------------------------------------------

    def render_survey_poster(self, survey_id: int, question: str) -> Path:
        """Render the image portion of a temporary survey broadcast."""
        question_font = self._font(30, True)
        question_height = max(
            132,
            self._text_block_height(question, question_font, self._content_width - 48) + 48,
        )
        body_height = 58 + question_height + 16 + 108 + 18
        image, draw, y = self._new_report(
            "糖糖小提示",
            f"问卷 #{survey_id}  |  欢迎提交意见",
            body_height,
        )
        self._draw_notice(draw, y, "糖糖想说的话")
        y += 58
        draw.rounded_rectangle(
            (self.LEFT, y, self.WIDTH - self.RIGHT, y + question_height),
            radius=12,
            fill="#fffafd",
            outline=self.TABLE_LINE,
            width=1,
        )
        self._draw_wrapped(
            draw,
            self.LEFT + 24,
            y + 24,
            question,
            question_font,
            self._content_width - 48,
            self.TEXT,
        )
        y += question_height + 16
        self._draw_action_card(
            draw,
            y,
            108,
            "直接在群里发送 ###+内容 即可，\n"
            "糖糖会记录你说的话并转达。",
            title="反馈方式",
            title_size=18,
            text_size=17,
        )
        return self._save(image, "survey")

    def render_global_announcement(
        self,
        text: str,
        sticker: Path | None = None,
    ) -> Path:
        """Render a big-character poster whose width adapts to the longest line."""
        stamp = datetime.now(self.zone).strftime("%Y-%m-%d %H:%M")
        body_font = self._font(self._announcement_font_size(len(text)), True)
        lines = text.splitlines()
        line_height = self._line_height(body_font)
        line_step = max(1, round(line_height * 1.2))
        text_height = (
            max(1, len(lines)) * line_height
            + max(0, len(lines) - 1) * (line_step - line_height)
        )
        box_padding = 24
        box_height = box_padding + text_height + box_padding
        caption_height = 24
        body_height = box_height + 20 + caption_height + 12
        sticker_gutter = 190 if sticker else 0
        max_text_width = max(
            (self._text_width(line, body_font) for line in lines if line),
            default=0,
        )
        canvas_width = max(
            480,
            self.LEFT + sticker_gutter + 44 + max_text_width + 44 + self.RIGHT,
        )
        text_left = self.LEFT + sticker_gutter + 44 if sticker else self.LEFT + 44
        text_right = canvas_width - self.RIGHT - 44
        text_center = (text_left + text_right) // 2
        image, draw, y = self._new_report(
            "",
            "",
            body_height,
            compact=True,
            header_gradient=False,
            width=canvas_width,
        )
        paste_horizontal_gradient(
            image,
            (self.LEFT, y, canvas_width - self.RIGHT, y + box_height),
            "#ffd3e4",
            "#fff3f8",
            radius=18,
            outline="#edb7cb",
            outline_width=2,
        )
        if sticker:
            sticker_box = 176
            sticker_height = min(sticker_box, box_height - 24)
            sticker_top = y + (box_height - sticker_height) // 2
            self._paste_announcement_sticker(
                image,
                sticker,
                self.LEFT + 18,
                sticker_top,
                sticker_box,
                sticker_height,
            )
        line_y = y + box_padding
        for line in lines:
            self._draw_centered(
                draw,
                text_center,
                line_y + line_height // 2,
                line,
                body_font,
                self.INK,
            )
            line_y += line_step
        caption_top = y + box_height + 20
        self._draw_centered(
            draw,
            canvas_width // 2,
            caption_top + caption_height // 2,
            f"全局通告 · A 海岸全群广播 · {stamp}",
            self._font(16, True),
            self.MUTED,
        )
        return self._save(image, "global_announcement")

    @staticmethod
    def _paste_announcement_sticker(
        image: Image.Image,
        path: Path,
        left: int,
        top: int,
        width: int,
        height: int,
    ) -> None:
        """Paste a transparent sticker centered in a box; failures just skip it."""
        try:
            with Image.open(path) as source:
                sticker = source.convert("RGBA")
                sticker.thumbnail((width, height), Image.Resampling.LANCZOS)
                x = left + (width - sticker.width) // 2
                y = top + (height - sticker.height) // 2
                image.paste(sticker, (x, y), sticker)
        except OSError:
            return

    @staticmethod
    def _announcement_font_size(length: int) -> int:
        """Announcement posters always use the same big-character size."""
        return 40

    def render_activity_hall(self, rows: Sequence[Mapping[str, Any]]) -> Path:
        values = list(rows)
        card_heights = [228 for _ in values] or [130]
        body_height = sum(card_heights) + max(0, len(card_heights) - 1) * 16 + 18
        image, draw, y = self._new_report(
            "活动大厅", "跨群联动活动  |  查看详情后可直接复制 ID 报名", body_height,
            self.activity_help_hint,
        )
        if not values:
            self._draw_empty_card(draw, y, "当前没有未开始或进行中的活动")
            return self._save(image, "activity_hall")
        for row in values:
            self._draw_activity_hall_card(draw, y, row)
            y += 244
        return self._save(image, "activity_hall")

    def render_activity_detail(
        self,
        activity: Mapping[str, Any],
        groups: Sequence[Mapping[str, Any]] | str,
        prizes: Sequence[Mapping[str, Any]] | str,
        winners_text: str = "",
        notice_text: str = "",
        action_hint: str = "",
        group_avatar_paths: Mapping[int, Path] | None = None,
    ) -> Path:
        group_rows = self._normalise_groups(groups)
        prize_rows = self._normalise_prizes(prizes)
        description = str(activity.get("description") or "未填写活动说明")
        description_height = max(84, self._text_block_height(description, self._font(24), self._content_width - 48) + 32)
        group_height = max(1, len(group_rows)) * 112 + max(0, len(group_rows) - 1) * 8 + 58
        prize_height = max(1, len(prize_rows)) * 104 + max(0, len(prize_rows) - 1) * 8 + 58
        notice_height = 52 if notice_text else 0
        creator_height = 96
        result_height = 60 if activity.get("activity_type") == "lottery" and activity.get("status") == "ended" else 0
        action_height = max(
            120,
            self._text_block_height(action_hint, self._font(21), self._content_width - 48) + 76,
        ) if action_hint else 0
        body_height = notice_height + 146 + creator_height + 232 + group_height + description_height + prize_height + 108 + result_height + action_height + 18 + 7 * 16
        image, draw, y = self._new_report(
            str(activity.get("title") or "活动详情"),
            f"活动 #{activity.get('activity_id', '')}  |  {activity.get('status_label') or activity.get('status') or ''}",
            body_height,
            self.activity_help_hint,
        )
        if notice_text:
            self._draw_notice(draw, y, notice_text)
            y += notice_height + 16
        self._draw_activity_meta(draw, y, activity)
        y += 146
        self._draw_creator_row(draw, y, activity)
        y += creator_height + 16
        self._draw_time_cards(draw, y, str(activity.get("starts_text") or ""), str(activity.get("ends_text") or ""))
        y += 232
        self._draw_section_heading(draw, y, "参与群", f"{len(group_rows)} 个群")
        y += 42
        for index, row in enumerate(group_rows):
            self._draw_group_row(image, draw, y, row, (group_avatar_paths or {}), index)
            y += 120
        y += 8
        self._draw_section_heading(draw, y, "活动说明", "公开信息")
        y += 42
        self._draw_text_card(draw, y, description_height, description)
        y += description_height + 16
        self._draw_section_heading(draw, y, "奖项设置", "按奖项顺序自动编号")
        y += 42
        if prize_rows:
            for index, row in enumerate(prize_rows):
                self._draw_prize_row(draw, y, index + 1, row)
                y += 112
        else:
            self._draw_empty_card(draw, y, "此活动未设置奖项", height=72)
            y += 80
        y += 8
        self._draw_footer_note(draw, y, "报名成功会添加续标识并引用回复人数；取消报名会添加续标识和疑问表情。")
        y += 124
        if result_height:
            self._draw_result_hint(draw, y, int(activity.get("activity_id", 0) or 0))
            y += result_height + 16
        if winners_text:
            # Retained for compatibility; completed lottery pages use the dedicated winner report.
            self._draw_text_card(draw, y, max(84, self._text_block_height(winners_text, self._font(22), self._content_width - 48) + 32), winners_text)
        if action_hint:
            self._draw_action_card(
                draw,
                y,
                action_height,
                action_hint,
                title=f"操作示例    本活动 ID 是 {activity.get('activity_id', '')}。",
            )
        return self._save(image, "activity_detail")

    def render_activity_unsupported_notice(self, title: str) -> Path:
        """Render the minimal notice sent only to a group removed from an activity."""
        image, draw, y = self._new_report("活动范围变更", "本群通知", 180)
        self._draw_action_card(
            draw,
            y,
            120,
            f"活动「{title}」\n已不支持本群",
            title="通知",
        )
        return self._save(image, "activity_unsupported")

    def render_activity_help(self) -> Path:
        prefix = self.command_prefix
        sections = [
            ("浏览活动", f"{prefix}活动大厅\n{prefix}活动详情 ID\n{prefix}查看名单 ID\n{prefix}获奖名单 ID", "查看公开活动、报名名单与开奖结果"),
            ("参与活动", f"{prefix}报名 ID\n{prefix}取消报名 ID\n{prefix}我的活动", "统一使用 # 前缀；支持紧贴、空格、中文冒号和 # 作为活动 ID 分隔符"),
        ]
        heights = [self._help_card_height(commands, note) for _, commands, note in sections]
        body_height = sum(heights) + max(0, len(heights) - 1) * 16 + 124 + 18
        image, draw, y = self._new_report(
            "普通用户活动帮助", "活动开放群内可直接使用；可复制文字版会随本图放在合并消息内", body_height,
            self.activity_help_hint,
        )
        for index, ((title, commands, note), height) in enumerate(zip(sections, heights), 1):
            self._draw_help_card(draw, y, index, title, commands, note)
            y += height + 16
        self._draw_action_card(
            draw, y, 124,
            f"ID 示例：活动 ID 为 500 时，活动开放群内可直接发送 {prefix}报名500、{prefix}报名 500 或 {prefix}活动详情：500。",
            title="操作示例",
        )
        return self._save(image, "activity_help")

    def render_activity_participants(
        self,
        activity: Mapping[str, Any],
        rows: Sequence[Mapping[str, Any]],
        avatar_paths: Mapping[int, Path] | None = None,
    ) -> Path:
        values = list(rows)
        body_height = max(1, len(values)) * 150 + max(0, len(values) - 1) * self.ROW_GAP + 18
        image, draw, y = self._new_report(
            f"{activity.get('title') or '活动'}  |  报名名单",
            f"活动 #{activity.get('activity_id', '')}  |  当前报名 {activity.get('participant_count', len(values))} 人",
            body_height,
            self.activity_help_hint,
        )
        if not values:
            self._draw_empty_card(draw, y, "暂无报名")
            return self._save(image, "activity_participants")
        for index, row in enumerate(values):
            user_id = int(row.get("user_id", 0) or 0)
            name = str(row.get("nickname") or "未获取昵称")
            qq = str(row.get("display_user_id") or "")
            tag = f"#{row.get('registration_no') or '-'}  |  {row.get('group_label') or '未知群'}"
            self._draw_member_row(image, draw, y, 150, index, user_id, name, qq, tag, avatar_paths or {}, "报名信息")
            y += 150 + self.ROW_GAP
        return self._save(image, "activity_participants")

    def render_activity_winners(
        self,
        activity: Mapping[str, Any],
        rows: Sequence[Mapping[str, Any]],
        avatar_paths: Mapping[int, Path] | None = None,
        page: int = 1,
        total_pages: int = 1,
    ) -> Path:
        grouped: list[tuple[str, list[Mapping[str, Any]]]] = []
        for row in rows:
            prize_name = str(row.get("prize_name") or "未命名奖项")
            if not grouped or grouped[-1][0] != prize_name:
                grouped.append((prize_name, []))
            grouped[-1][1].append(row)
        if not grouped:
            grouped = [("开奖结果", [])]

        body_font = self._font(24)
        title_font = self._font(24, bold=True)
        content_width = self._content_width - 120
        heights: list[int] = []
        for _, winners in grouped:
            heights.append(48)
            for winner in winners:
                detail = f"QQ {winner.get('display_user_id') or ''}  |  报名序号 {winner.get('registration_no') or ''}"
                heights.append(max(78, self._text_block_height(detail, body_font, content_width - 20) + 42))
        subtitle = f"活动 #{activity.get('activity_id', '')}  |  按奖项分组  |  共 {len(rows)} 名获奖者"
        if total_pages > 1:
            subtitle += f"  |  第 {page}/{total_pages} 页"
        # Each rendered block adds spacing beyond its stored content height.
        # Include every one of those gaps here so long winner lists never draw
        # below the canvas and get clipped by QQ image viewers.
        body_height = 18
        height_index = 0
        for _prize_name, winners in grouped:
            body_height += heights[height_index] + 2
            height_index += 1
            if winners:
                for _winner in winners:
                    body_height += heights[height_index] + 8
                    height_index += 1
            else:
                body_height += 90
        image, draw, y = self._new_report(
            f"{activity.get('title') or '活动'}  |  获奖名单", subtitle, body_height,
            self.activity_help_hint,
        )
        offset = 0
        for prize_name, winners in grouped:
            prize_height = heights[offset]
            offset += 1
            draw.rounded_rectangle((self.LEFT, y, self.WIDTH - self.RIGHT, y + prize_height), radius=10, fill="#f3d8ea", outline="#deb1ce", width=1)
            draw.text((self.LEFT + 18, y + 11), prize_name, font=title_font, fill=self.LABEL_TEXT)
            self._draw_quantity_badge(draw, self.WIDTH - self.RIGHT - 88, y + 10, len(winners))
            y += prize_height + 2
            if not winners:
                self._draw_empty_card(draw, y, "暂无中奖者", height=82)
                y += 90
                continue
            for index, winner in enumerate(winners):
                row_height = heights[offset]
                offset += 1
                draw.rounded_rectangle((self.LEFT, y, self.WIDTH - self.RIGHT, y + row_height), radius=8, fill=self.PANEL if index % 2 == 0 else self.ROW_ALT, outline=self.TABLE_LINE, width=1)
                user_id = int(winner.get("user_id", 0) or 0)
                self._draw_avatar_at(image, draw, self.LEFT + 18, y + (row_height - 58) // 2, 58, (avatar_paths or {}).get(user_id), str(winner.get("display_user_id") or ""))
                draw.text((self.LEFT + 94, y + 14), str(winner.get("nickname") or "未获取昵称"), font=title_font, fill=self.TEXT)
                detail = f"QQ {winner.get('display_user_id') or ''}  |  报名序号 {winner.get('registration_no') or ''}"
                draw.text((self.LEFT + 94, y + 48), detail, font=body_font, fill=self.MUTED)
                y += row_height + 8
        return self._save(image, "activity_winners")

    # Poster primitives ---------------------------------------------------

    @property
    def _content_width(self) -> int:
        return self.WIDTH - self.LEFT - self.RIGHT

    def _new_report(
        self,
        title: str,
        subtitle: str,
        body_height: int,
        footer_hint: str = "",
        compact: bool = False,
        header_gradient: bool = True,
        width: int | None = None,
    ):
        """Create the shared paper-card frame for local reports."""
        canvas_width = width or self.WIDTH
        scratch = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        label_top, label_height, inner_padding = 68, 28, 13
        subtitle_font = self._font(17)
        title_width = canvas_width - self.LEFT - self.RIGHT - 148
        subtitle_box = scratch.textbbox((0, 0), subtitle or "Ag", font=subtitle_font)
        if compact:
            corner_font = self._font(16, True)
            corner_top = label_top + label_height + 6
            corner_height = 28
            corner_text = self._ellipsize(title or "", corner_font, 240)
            corner_width = self._text_width(corner_text, corner_font) + 28
            subtitle_y = corner_top + corner_height + 8
            if header_gradient:
                header_bottom = max(150, subtitle_y + subtitle_box[3] + 22)
            else:
                # The announcement card has no header band: squeeze content up
                # to just below the top-left label instead of reserving the band.
                header_bottom = label_top + label_height
        else:
            title_size = 48
            title_font = self._font(title_size, True)
            # The hanging tag owns the top-right corner, so long titles have a
            # measured safe area instead of colliding with decoration.
            while title_size > 32 and self._text_width(title, title_font) > title_width:
                title_size -= 2
                title_font = self._font(title_size, True)
            title_y = label_top + label_height + inner_padding
            title_box = scratch.textbbox((0, 0), title or "Ag", font=title_font)
            subtitle_y = title_y + title_box[3] - subtitle_box[1] + 10
            header_bottom = max(self.HEADER_HEIGHT, subtitle_y + subtitle_box[3] + 30)
        panel_outer_top = header_bottom + 8
        content_top = panel_outer_top + 22
        footer_top = content_top + body_height + 18
        panel_bottom = footer_top - 8
        height = footer_top + self.FOOTER_HEIGHT
        image = Image.new("RGB", (canvas_width, height), self.BACKGROUND)
        draw = EmojiTextDraw(image)
        self._draw_outer_frame(draw, height, canvas_width)
        if header_gradient:
            paste_horizontal_gradient(
                image,
                (38, 44, canvas_width - 38, header_bottom),
                "#f3a2bd",
                "#fff5f9",
                radius=18,
            )
        draw.rounded_rectangle((self.LEFT, label_top, self.LEFT + 118, label_top + label_height), radius=14, fill="#ffffff")
        draw.text((self.LEFT + 18, label_top + 5), "A-SOUL BOT", font=self._font(14, True), fill=self.ACCENT)
        if compact:
            if title:
                draw.rounded_rectangle(
                    (
                        self.LEFT,
                        corner_top,
                        self.LEFT + corner_width,
                        corner_top + corner_height,
                    ),
                    radius=14,
                    fill="#ffffff",
                    outline="#f5dce7",
                    width=1,
                )
                draw.text(
                    (self.LEFT + 14, corner_top + 6),
                    corner_text,
                    font=corner_font,
                    fill=self.ACCENT,
                )
        else:
            draw.text(
                (self.LEFT, title_y),
                self._ellipsize(title, title_font, title_width),
                font=title_font,
                fill=self.TEXT,
            )
        draw.text((self.LEFT, subtitle_y), self._ellipsize(subtitle, subtitle_font, title_width), font=subtitle_font, fill=self.HEADER_SUBTITLE)
        draw_stitched_mascot(draw, canvas_width - 126, 24, 52)
        self._draw_heart(draw, canvas_width - 166, 48, 14, "#f7a4bd")
        self._draw_cross(draw, canvas_width - 46, 52, 9)
        self._draw_bandage(draw, canvas_width - 184, header_bottom - 19)
        draw.rounded_rectangle(
            (
                self.LEFT - self.CONTENT_FRAME_GAP,
                panel_outer_top,
                canvas_width - self.RIGHT + self.CONTENT_FRAME_GAP,
                panel_bottom,
            ),
            radius=18,
            fill="#fffefd",
            outline=self.TABLE_LINE,
            width=1,
        )
        self._draw_corner_ribbons(draw, height, canvas_width)
        del footer_hint
        footer = "A海岸 Bot · Generated by Secmon"
        self._draw_centered(draw, canvas_width // 2, height - 29, footer, self._font(15), self.MUTED)
        self._draw_bunny(draw, canvas_width - 64, height - 61, 17)
        return image, draw, content_top

    def _draw_corner_ribbons(
        self,
        draw: ImageDraw.ImageDraw,
        height: int,
        width: int | None = None,
    ) -> None:
        """Draw the small pink registration marks from the paper-card system."""
        canvas_width = width or self.WIDTH
        band = self.FRAME_BAND
        inset = 18
        span = self.FRAME_SPAN
        color = "#ff91b4"
        muted = "#f6c6d7"
        draw.line((inset, inset + span, inset, inset, inset + span, inset), fill=color, width=band, joint="curve")
        draw.line((canvas_width - inset - 48, inset, canvas_width - inset, inset, canvas_width - inset, inset + 28), fill=muted, width=3, joint="curve")
        draw.line((inset, height - inset - 28, inset, height - inset, inset + 52, height - inset), fill=muted, width=3, joint="curve")
        draw_heart_tail(draw, 78, height - 42)

    def _draw_outer_frame(
        self,
        draw: ImageDraw.ImageDraw,
        height: int,
        width: int | None = None,
    ) -> None:
        """Keep the registration marks as the only treatment on three outer corners."""
        canvas_width = width or self.WIDTH
        left, top, right, bottom = 18, 18, canvas_width - 18, height - 18
        radius, mark_span, line_width = 26, self.FRAME_SPAN, 2
        outline = "#f5dce7"

        # A square fill prevents a second, pale rounded silhouette behind the marks.
        draw.rectangle((left, top, right, bottom), fill=self.PANEL)
        draw.line((left + mark_span, top, right - 48, top), fill=outline, width=line_width)
        draw.line((left, top + mark_span, left, bottom - 28), fill=outline, width=line_width)
        draw.line((left + 52, bottom, right - radius, bottom), fill=outline, width=line_width)
        draw.line((right, top + 28, right, bottom - radius), fill=outline, width=line_width)
        draw.arc((right - radius * 2, bottom - radius * 2, right, bottom), 0, 90, fill=outline, width=line_width)

    def _draw_bunny(self, draw: ImageDraw.ImageDraw, center_x: int, center_y: int, size: int) -> None:
        """Small line-art rabbit, intentionally quiet enough for dense reports."""
        ink = "#dc789c"
        face = size * 2
        draw.ellipse((center_x - size, center_y - size // 2, center_x + size, center_y + face - size // 2), fill="#fffafd", outline=ink, width=2)
        draw.ellipse((center_x - size + 2, center_y - size * 2, center_x - 1, center_y + 1), fill="#fffafd", outline=ink, width=2)
        draw.ellipse((center_x + 1, center_y - size * 2, center_x + size - 2, center_y + 1), fill="#fffafd", outline=ink, width=2)
        draw.ellipse((center_x - size // 2 - 2, center_y + size // 2, center_x - size // 2 + 2, center_y + size // 2 + 4), fill=ink)
        draw.ellipse((center_x + size // 2 - 2, center_y + size // 2, center_x + size // 2 + 2, center_y + size // 2 + 4), fill=ink)
        draw.arc((center_x - 5, center_y + size, center_x + 5, center_y + size + 8), 0, 180, fill=ink, width=1)

    @staticmethod
    def _draw_cross(draw: ImageDraw.ImageDraw, center_x: int, center_y: int, size: int) -> None:
        draw.line((center_x - size, center_y - size, center_x + size, center_y + size), fill="#f081a5", width=3)
        draw.line((center_x + size, center_y - size, center_x - size, center_y + size), fill="#f081a5", width=3)

    @staticmethod
    def _draw_bandage(draw: ImageDraw.ImageDraw, center_x: int, center_y: int) -> None:
        """A tiny paper-plaster mark, used once per page as an accent."""
        draw.polygon(
            [(center_x - 22, center_y - 7), (center_x - 14, center_y - 15), (center_x + 22, center_y + 7), (center_x + 14, center_y + 15)],
            fill="#ffd5e2",
        )
        draw.rounded_rectangle((center_x - 5, center_y - 5, center_x + 5, center_y + 5), radius=3, fill="#f291b1")

    def _draw_member_row(self, image: Image.Image, draw: ImageDraw.ImageDraw, y: int, height: int, index: int, user_id: int, nickname: str, qq: str, detail: str, avatar_paths: Mapping[int, Path], right_label: str) -> None:
        fill = self.PANEL if index % 2 == 0 else self.ROW_ALT
        draw.rounded_rectangle((self.LEFT, y, self.WIDTH - self.RIGHT, y + height), radius=18, fill=fill, outline=self.TABLE_LINE, width=2)
        self._draw_avatar_at(image, draw, self.LEFT + 20, y + 20, 56, avatar_paths.get(user_id), qq)
        name_x = self.LEFT + 96
        draw.text((name_x, y + 18), self._ellipsize(nickname, self._font(23, True), 360), font=self._font(23, True), fill=self.TEXT)
        draw.text((name_x, y + 53), f"QQ {qq}" if qq else "", font=self._font(17), fill=self.MUTED)
        draw.text((name_x, y + 83), right_label, font=self._font(16, True), fill=self.LABEL_TEXT)
        self._draw_wrapped(draw, name_x, y + 108, detail, self._font(18), self._content_width - 116, self.MUTED, max_lines=max(1, (height - 112) // self._line_height(self._font(18))))

    def _whitelist_row_height(self, note: str) -> int:
        note_height = self._text_block_height(note, self._font(16), 196)
        return max(96, 68 + note_height)

    def _draw_whitelist_row(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        y: int,
        height: int,
        index: int,
        user_id: int,
        nickname: str,
        note: str,
        avatar_paths: Mapping[int, Path],
    ) -> None:
        fill = self.PANEL if index % 2 == 0 else self.ROW_ALT
        draw.rounded_rectangle((self.LEFT, y, self.WIDTH - self.RIGHT, y + height), radius=16, fill=fill, outline=self.TABLE_LINE, width=2)
        avatar_size = 48
        avatar_x = self.LEFT + 20
        self._draw_avatar_at(image, draw, avatar_x, y + (height - avatar_size) // 2, avatar_size, avatar_paths.get(user_id), str(user_id))
        name_x = avatar_x + avatar_size + 18
        note_width = 224
        note_x = self.WIDTH - self.RIGHT - 18 - note_width
        draw.text((name_x, y + 18), self._ellipsize(nickname, self._font(21, True), note_x - name_x - 18), font=self._font(21, True), fill=self.TEXT)
        draw.text((name_x, y + 53), f"QQ {user_id}" if user_id else "", font=self._font(16), fill=self.MUTED)
        draw.rounded_rectangle((note_x, y + 12, self.WIDTH - self.RIGHT - 18, y + height - 12), radius=14, fill="#fff0f5")
        draw.text((note_x + 14, y + 18), "备注", font=self._font(15, True), fill=self.LABEL_TEXT)
        self._draw_wrapped(draw, note_x + 14, y + 42, note, self._font(16), note_width - 28, self.MUTED)

    def _draw_ranking_row(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        y: int,
        rank: int,
        user_id: int,
        nickname: str,
        count: str,
        avatar_paths: Mapping[int, Path],
        group_labels: str = "",
        group_layout: bool = False,
    ) -> None:
        height = 100 if group_labels or group_layout else 80
        fill = self.PANEL if rank % 2 else self.ROW_ALT
        draw.rounded_rectangle((self.LEFT, y, self.WIDTH - self.RIGHT, y + height), radius=16, fill=fill, outline=self.TABLE_LINE, width=2)
        medal = ("#e3b14d", "#bfc7d1", "#cf936e")[rank - 1] if rank <= 3 else self.SOFT_LAVENDER
        rank_size = 34
        rank_top = y + (height - rank_size) // 2
        draw.ellipse((self.LEFT + 20, rank_top, self.LEFT + 20 + rank_size, rank_top + rank_size), fill=medal, outline="#ffffff", width=2)
        rank_text = str(rank)
        self._draw_centered(draw, self.LEFT + 20 + rank_size // 2, rank_top + rank_size // 2, rank_text, self._font(16, True), "#fffafd" if rank <= 3 else self.LABEL_TEXT)
        avatar_size = 42
        avatar_x = self.LEFT + 72
        self._draw_avatar_at(image, draw, avatar_x, y + (height - avatar_size) // 2, avatar_size, avatar_paths.get(user_id), str(user_id))
        name_x = avatar_x + avatar_size + 18
        badge_width = 112
        badge_x = self.WIDTH - self.RIGHT - 18 - badge_width
        title_y = y + (15 if group_labels or group_layout else 25)
        draw.text((name_x, title_y), self._ellipsize(nickname, self._font(20, True), badge_x - name_x - 16), font=self._font(20, True), fill=self.TEXT)
        self._draw_count_badge(
            draw,
            badge_x,
            y + (height - 36) // 2,
            count,
            "条",
        )
        if group_labels:
            detail = f"发言最多群聊：{group_labels}"
            draw.text((name_x, y + 57), self._ellipsize(detail, self._font(15), badge_x - name_x - 16), font=self._font(15), fill=self.MUTED)

    def _draw_info_row(self, draw: ImageDraw.ImageDraw, y: int, height: int, label: str, value: str, state: str) -> None:
        draw.rounded_rectangle((self.LEFT, y, self.WIDTH - self.RIGHT, y + height), radius=18, fill=self.PANEL, outline=self.TABLE_LINE, width=2)
        draw.rectangle((self.LEFT + 20, y + 22, self.LEFT + 28, y + 58), fill=self.ACCENT)
        draw.text((self.LEFT + 46, y + 20), label, font=self._font(22, True), fill=self.TEXT)
        if state:
            fill = self.SOFT_MINT if state == "统计开启" else self.KEY_FILL
            state_font = self._font(16, True)
            state_width = self._text_width(state, state_font) + 28
            draw.rounded_rectangle((self.WIDTH - self.RIGHT - state_width - 18, y + 18, self.WIDTH - self.RIGHT - 18, y + 48), radius=15, fill=fill)
            draw.text((self.WIDTH - self.RIGHT - state_width - 4, y + 24), state, font=state_font, fill=self.LABEL_TEXT)
        self._draw_wrapped(draw, self.LEFT + 46, y + 70, value, self._font(18), self._content_width - 70, self.MUTED, max_lines=max(1, (height - 76) // self._line_height(self._font(18))))

    def _draw_group_legend(self, draw: ImageDraw.ImageDraw, y: int, labels: Sequence[tuple[int, str]], mode: ReportComparisonMode) -> int:
        height = self._legend_height(labels, mode)
        draw.rounded_rectangle((self.LEFT, y, self.WIDTH - self.RIGHT, y + height), radius=10, fill="#fff9fb", outline=self.TABLE_LINE, width=1)
        draw.text((self.LEFT + 18, y + 13), "群编号图例", font=self._font(21, True), fill=self.LABEL_TEXT)
        for chip_x, line, chip_width, text, index in self._legend_chip_layout(labels, mode):
            chip_y = y + 48 + line * 34
            draw.rounded_rectangle(
                (chip_x, chip_y, chip_x + chip_width, chip_y + 27),
                radius=13,
                fill=self.KEY_FILL if index == 1 and mode == "source" else self.SOFT_LAVENDER,
            )
            draw.text((chip_x + 14, chip_y + 4), text, font=self._font(19), fill=self.TEXT)
        return y + height + self.ROW_GAP

    def _draw_activity_hall_card(self, draw: ImageDraw.ImageDraw, y: int, row: Mapping[str, Any]) -> None:
        draw.rounded_rectangle((self.LEFT, y, self.WIDTH - self.RIGHT, y + 228), radius=20, fill=self.PANEL, outline=self.TABLE_LINE, width=2)
        status = str(row.get("status_label") or row.get("status") or "")
        status_fill = self.SOFT_MINT if status == "进行中" else self.KEY_FILL
        self._draw_chip(draw, self.LEFT + 22, y + 20, status or "活动", status_fill, self.LABEL_TEXT)
        activity_id = f"#{int(row.get('activity_id', 0) or 0)}"
        id_font = self._font(22, True)
        draw.text((self.WIDTH - self.RIGHT - self._text_width(activity_id, id_font) - 22, y + 22), activity_id, font=id_font, fill=self.LAVENDER)
        self._draw_wrapped(draw, self.LEFT + 22, y + 65, str(row.get("title") or "未命名活动"), self._font(27, True), self._content_width - 44, self.TEXT, max_lines=2)
        heat = f"{row.get('participant_count', 0)} 人报名  |  来自 {row.get('group_count', 0)} 个群"
        draw.text((self.LEFT + 22, y + 132), heat, font=self._font(18), fill=self.MUTED)
        band_top = y + 164
        draw.rounded_rectangle((self.LEFT + 18, band_top, self.WIDTH - self.RIGHT - 18, band_top + 48), radius=14, fill="#fff1f8")
        draw.text((self.LEFT + 36, band_top + 7), f"开始  {str(row.get('starts_text') or '')}", font=self._font(16), fill=self.LABEL_TEXT)
        draw.text((self.LEFT + 36, band_top + 27), f"结束  {str(row.get('ends_text') or '')}", font=self._font(16), fill=self.LABEL_TEXT)

    def _draw_activity_meta(self, draw: ImageDraw.ImageDraw, y: int, activity: Mapping[str, Any]) -> None:
        draw.rounded_rectangle((self.LEFT, y, self.WIDTH - self.RIGHT, y + 130), radius=18, fill="#fff8fb", outline=self.TABLE_LINE, width=2)
        type_text = "抽奖活动" if activity.get("activity_type") == "lottery" else "团建 / 通报"
        self._draw_chip(draw, self.LEFT + 18, y + 18, type_text, self.SOFT_LAVENDER, self.LABEL_TEXT)
        visibility = str(activity.get("visibility_label") or ("公开参与" if activity.get("visibility") == "public" else "脱敏参与"))
        self._draw_chip(draw, self.LEFT + 18, y + 58, visibility, self.KEY_FILL, self.LABEL_TEXT)
        heat = f"当前 {activity.get('participant_count', 0)} 人报名  |  {activity.get('group_count', 0)} 个群参与"
        draw.text((self.LEFT + 210, y + 25), heat, font=self._font(17), fill=self.TEXT)
        activity_id = f"ID #{activity.get('activity_id', '')}"
        draw.text((self.LEFT + 210, y + 62), activity_id, font=self._font(18, True), fill=self.LAVENDER)

    def _draw_creator_row(self, draw: ImageDraw.ImageDraw, y: int, activity: Mapping[str, Any]) -> None:
        """Keep the organizer reachable without competing with activity rules."""
        draw.rounded_rectangle(
            (self.LEFT, y, self.WIDTH - self.RIGHT, y + 96),
            radius=18,
            fill="#fbf7fc",
            outline="#e7ddef",
            width=1,
        )
        draw.text((self.LEFT + 18, y + 16), "活动发起人", font=self._font(18, True), fill=self.LAVENDER)
        nickname = str(activity.get("creator_nickname") or "活动发起人")
        creator_id = activity.get("creator_id") or "未知"
        text = f"{nickname}  |  QQ {creator_id}  |  领奖请联系发起人"
        self._draw_wrapped(draw, self.LEFT + 18, y + 48, text, self._font(18), self._content_width - 36, self.MUTED, max_lines=2)

    def _draw_time_cards(self, draw: ImageDraw.ImageDraw, y: int, starts: str, ends: str) -> None:
        gap = 12
        width = self._content_width
        for index, (label, value, fill, accent) in enumerate((("开始时间", starts, "#fff0f5", self.ACCENT), ("结束时间", ends, "#f4effa", self.LAVENDER))):
            top = y + index * (108 + gap)
            draw.rounded_rectangle((self.LEFT, top, self.LEFT + width, top + 108), radius=18, fill=fill, outline=self.TABLE_LINE, width=2)
            draw.rectangle((self.LEFT + 20, top + 20, self.LEFT + 28, top + 53), fill=accent)
            draw.text((self.LEFT + 46, top + 16), label, font=self._font(19, True), fill=self.LABEL_TEXT)
            self._draw_wrapped(draw, self.LEFT + 46, top + 48, value, self._font(21, True), width - 68, self.TEXT, max_lines=2)

    def _draw_section_heading(self, draw: ImageDraw.ImageDraw, y: int, title: str, trailing: str = "") -> None:
        draw.rectangle((self.LEFT, y + 8, self.LEFT + 7, y + 34), fill=self.ACCENT)
        draw.text((self.LEFT + 18, y + 2), title, font=self._font(25, True), fill=self.TEXT)
        if trailing:
            font = self._font(18)
            draw.text((self.WIDTH - self.RIGHT - self._text_width(trailing, font), y + 10), trailing, font=font, fill=self.MUTED)

    def _draw_group_row(self, image: Image.Image, draw: ImageDraw.ImageDraw, y: int, row: Mapping[str, Any], avatar_paths: Mapping[int, Path], index: int) -> None:
        group_id = int(row.get("group_id", 0) or 0)
        draw.rounded_rectangle((self.LEFT, y, self.WIDTH - self.RIGHT, y + 112), radius=18, fill=self.PANEL if index % 2 == 0 else self.ROW_ALT, outline=self.TABLE_LINE, width=2)
        self._draw_avatar_at(image, draw, self.LEFT + 16, y + 9, 58, avatar_paths.get(group_id), str(group_id))
        name = str(row.get("group_name") or f"群 {group_id}")
        draw.text((self.LEFT + 94, y + 13), self._ellipsize(name, self._font(21, True), 330), font=self._font(21, True), fill=self.TEXT)
        draw.text((self.LEFT + 94, y + 45), f"群号 {group_id}", font=self._font(19), fill=self.MUTED)
        self._draw_chip(draw, self.LEFT + 94, y + 74, f"参与群 {index + 1}", self.KEY_FILL, self.LABEL_TEXT)

    def _draw_group_overview_row(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        y: int,
        row: Mapping[str, Any],
        avatar_paths: Mapping[int, Path],
        index: int,
        row_height: int,
    ) -> None:
        group_id = int(row.get("group_id", 0) or 0)
        draw.rounded_rectangle(
            (self.LEFT, y, self.WIDTH - self.RIGHT, y + row_height),
            radius=18,
            fill=self.PANEL if index % 2 == 0 else self.ROW_ALT,
            outline=self.TABLE_LINE,
            width=1,
        )
        self._draw_avatar_at(image, draw, self.LEFT + 16, y + 11, 58, avatar_paths.get(group_id), str(group_id))
        name = str(row.get("group_name") or f"群 {group_id}")
        tag = str(row.get("tag") or "管理群")
        tag_font = self._font(18, True)
        tag_width = self._text_width(tag, tag_font) + 28
        tag_x = self.WIDTH - self.RIGHT - tag_width
        draw.text(
            (self.LEFT + 94, y + 12),
            self._ellipsize(name, self._font(21, True), tag_x - self.LEFT - 112),
            font=self._font(21, True),
            fill=self.TEXT,
        )
        draw.text((self.LEFT + 94, y + 45), f"群号 {group_id}", font=self._font(19), fill=self.MUTED)
        self._draw_chip(draw, tag_x, y + 12, tag, self.KEY_FILL, self.LABEL_TEXT)
        detail = str(row.get("detail") or "")
        if detail:
            self._draw_wrapped(
                draw,
                self.LEFT + 94,
                y + 76,
                detail,
                self._font(17),
                self._content_width - 120,
                self.MUTED,
                max_lines=3,
            )

    def _group_overview_row_height(self, row: Mapping[str, Any]) -> int:
        detail = str(row.get("detail") or "")
        if not detail:
            return self.GROUP_OVERVIEW_ROW_HEIGHT
        font = self._font(17)
        width = self._content_width - 120
        visible_lines = min(3, len(self._wrap_text(detail, font, width)))
        return max(
            self.GROUP_OVERVIEW_ROW_HEIGHT,
            76 + visible_lines * self._line_height(font) + 20,
        )

    def _draw_prize_row(self, draw: ImageDraw.ImageDraw, y: int, rank: int, row: Mapping[str, Any]) -> None:
        draw.rounded_rectangle((self.LEFT, y, self.WIDTH - self.RIGHT, y + 104), radius=18, fill="#fffafd" if rank % 2 else "#fff6fa", outline=self.TABLE_LINE, width=2)
        rank_name = self._prize_rank_name(rank)
        badge_fill = ("#f6d69a", "#d7d9de", "#ebbc9c")[rank - 1] if rank <= 3 else self.SOFT_LAVENDER
        self._draw_number_badge(draw, self.LEFT + 20, y + 17, rank, badge_fill)
        draw.text((self.LEFT + 78, y + 13), rank_name, font=self._font(20, True), fill=self.LABEL_TEXT)
        prize_name = str(row.get("prize_name") or "未命名奖项")
        self._draw_wrapped(draw, self.LEFT + 78, y + 47, prize_name, self._font(20), self._content_width - 160, self.TEXT, max_lines=2)
        self._draw_quantity_badge(draw, self.WIDTH - self.RIGHT - 86, y + 17, int(row.get("quantity", 0) or 0))

    def _draw_notice(self, draw: ImageDraw.ImageDraw, y: int, text: str) -> None:
        draw.rounded_rectangle((self.LEFT, y, self.WIDTH - self.RIGHT, y + 78), radius=18, fill="#fde1eb", outline="#edb7cb", width=2)
        self._draw_wrapped(draw, self.LEFT + 18, y + 16, text, self._font(19, True), self._content_width - 36, self.LABEL_TEXT, max_lines=2)

    def _draw_footer_note(self, draw: ImageDraw.ImageDraw, y: int, text: str) -> None:
        draw.rounded_rectangle((self.LEFT, y, self.WIDTH - self.RIGHT, y + 108), radius=18, fill="#fbf7fc", outline="#e7ddef", width=2)
        draw.text((self.LEFT + 18, y + 16), "报名确认", font=self._font(18, True), fill=self.LAVENDER)
        self._draw_wrapped(draw, self.LEFT + 18, y + 48, text, self._font(17), self._content_width - 36, self.MUTED, max_lines=2)

    def _draw_result_hint(self, draw: ImageDraw.ImageDraw, y: int, activity_id: int) -> None:
        draw.rounded_rectangle((self.LEFT, y, self.WIDTH - self.RIGHT, y + 60), radius=10, fill=self.SOFT_MINT, outline="#c8e3d6", width=1)
        draw.text((self.LEFT + 18, y + 18), "已开奖", font=self._font(22, True), fill="#4c806e")
        hint = f"发送 {self.command_prefix}获奖名单 {activity_id} 查看完整名单"
        draw.text((self.LEFT + 170, y + 19), hint, font=self._font(21), fill=self.TEXT)

    def _draw_text_card(self, draw: ImageDraw.ImageDraw, y: int, height: int, text: str) -> None:
        draw.rounded_rectangle((self.LEFT, y, self.WIDTH - self.RIGHT, y + height), radius=10, fill="#fffafd", outline=self.TABLE_LINE, width=1)
        self._draw_wrapped(draw, self.LEFT + 22, y + 16, text, self._font(24), self._content_width - 44, self.TEXT)

    def _draw_action_card(
        self,
        draw: ImageDraw.ImageDraw,
        y: int,
        height: int,
        text: str,
        title: str = "操作示例",
        title_size: int = 22,
        text_size: int = 21,
    ) -> None:
        draw.rounded_rectangle((self.LEFT, y, self.WIDTH - self.RIGHT, y + height), radius=11, fill="#f3ecf8", outline="#d8c8e6", width=1)
        compact = title_size != 22 or text_size != 21
        draw.text(
            (self.LEFT + 20, y + (14 if compact else 16)),
            title,
            font=self._font(title_size, True),
            fill="#76558a",
        )
        self._draw_wrapped(
            draw,
            self.LEFT + 20,
            y + (44 if compact else 52),
            text,
            self._font(text_size),
            self._content_width - 40,
            self.TEXT,
        )

    def _draw_help_card(self, draw: ImageDraw.ImageDraw, y: int, index: int, title: str, commands: str, note: str) -> None:
        height = self._help_card_height(commands, note)
        draw.rounded_rectangle((self.LEFT, y, self.WIDTH - self.RIGHT, y + height), radius=11, fill=self.PANEL if index % 2 else "#fff9fb", outline=self.TABLE_LINE, width=1)
        self._draw_number_badge(draw, self.LEFT + 22, y + 20, index, self.KEY_FILL)
        draw.text((self.LEFT + 80, y + 17), title, font=self._font(25, True), fill=self.TEXT)
        if self._help_card_stacked(commands, note):
            command_x = self.LEFT + 80
            command_width = self.WIDTH - self.RIGHT - 18 - command_x
            note_left = self.LEFT + 18
            note_right = self.WIDTH - self.RIGHT - 18
            commands_bottom = self._draw_wrapped(
                draw, command_x, y + 55, commands, self._font(22), command_width, self.DARK
            )
            note_top = commands_bottom + 12
            draw.rounded_rectangle(
                (note_left, note_top, note_right, y + height - 14),
                radius=9,
                fill="#fff0f5",
            )
            self._draw_wrapped(
                draw,
                note_left + 14,
                note_top + 14,
                note,
                self._font(18),
                note_right - note_left - 28,
                self.LABEL_TEXT,
            )
            return
        note_x = self.LEFT + self._content_width - 205
        command_x = self.LEFT + 80
        command_width = note_x - command_x - 24
        self._draw_wrapped(draw, command_x, y + 55, commands, self._font(22), command_width, self.DARK)
        draw.rounded_rectangle((note_x, y + 18, self.WIDTH - self.RIGHT - 18, y + height - 18), radius=9, fill="#fff0f5")
        self._draw_wrapped(draw, note_x + 14, y + 32, note, self._font(18), self.WIDTH - self.RIGHT - 18 - note_x - 28, self.LABEL_TEXT)

    def _help_card_stacked(self, commands: str, note: str) -> bool:
        """Use a full-width note below the commands when the side note would tower over them."""
        note_x = self.LEFT + self._content_width - 205
        command_width = note_x - (self.LEFT + 80) - 24
        note_width = self.WIDTH - self.RIGHT - 18 - note_x - 28
        command_lines = len(self._wrap_text(commands, self._font(22), command_width))
        note_lines = len(self._wrap_text(note, self._font(18), note_width))
        return note_lines > command_lines + 6

    def _help_card_height(self, commands: str, note: str) -> int:
        if self._help_card_stacked(commands, note):
            command_width = self.WIDTH - self.RIGHT - 18 - (self.LEFT + 80)
            note_width = self.WIDTH - self.RIGHT - 18 - (self.LEFT + 18) - 28
            command_block = self._text_block_height(commands, self._font(22), command_width)
            note_block = self._text_block_height(note, self._font(18), note_width)
            return 55 + command_block + 12 + 14 + note_block + 18
        note_x = self.LEFT + self._content_width - 205
        command_width = note_x - (self.LEFT + 80) - 24
        note_width = self.WIDTH - self.RIGHT - 18 - note_x - 28
        command_height = 55 + self._text_block_height(commands, self._font(22), command_width) + 18
        note_height = 32 + self._text_block_height(note, self._font(18), note_width) + 22
        return max(130, command_height, note_height)

    def _user_help_category_height(self, note: str, entries: Sequence[tuple[str, str, str]]) -> int:
        card_heights = [self._user_help_card_height(commands, note) for _, commands, note in entries]
        header_height = 82 if note else 64
        return header_height + sum(card_heights) + max(0, len(card_heights) - 1) * 12 + 16

    def _user_help_card_height(self, commands: str, note: str) -> int:
        command_width = 350
        note_width = 178
        command_height = 48 + self._text_block_height(commands, self._font(21), command_width) + 18
        note_height = 28 + self._text_block_height(note, self._font(18), note_width) + 20
        return max(116, command_height, note_height)

    def _draw_user_help_category(
        self,
        draw: ImageDraw.ImageDraw,
        y: int,
        height: int,
        heading: str,
        note: str,
        entries: Sequence[tuple[str, str, str]],
        first_number: int,
    ) -> None:
        left = self.LEFT - 6
        right = self.WIDTH - self.RIGHT + 6
        draw.rounded_rectangle(
            (left, y, right, y + height),
            radius=14,
            fill="#fff7fb",
            outline="#e6c5d8",
            width=2,
        )
        draw.rounded_rectangle(
            (left + 16, y + 14, left + 28, y + 48),
            radius=6,
            fill=self.ACCENT,
        )
        draw.text((left + 42, y + 17), heading, font=self._font(26, True), fill=self.TEXT)
        if note:
            draw.text((left + 42, y + 48), note, font=self._font(16), fill=self.MUTED)
        cursor = y + (82 if note else 62)
        for index, (title, commands, note) in enumerate(entries, first_number):
            card_height = self._user_help_card_height(commands, note)
            self._draw_user_help_card(draw, cursor, card_height, index, title, commands, note)
            cursor += card_height + 12

    def _draw_user_help_card(
        self,
        draw: ImageDraw.ImageDraw,
        y: int,
        height: int,
        index: int,
        title: str,
        commands: str,
        note: str,
    ) -> None:
        left = self.LEFT + 6
        right = self.WIDTH - self.RIGHT - 6
        note_left = right - 196
        draw.rounded_rectangle(
            (left, y, right, y + height),
            radius=10,
            fill=self.PANEL if index % 2 else "#fff9fb",
            outline=self.TABLE_LINE,
            width=1,
        )
        self._draw_number_badge(draw, left + 18, y + 17, index, self.KEY_FILL)
        command_left = left + 74
        draw.text((command_left, y + 13), title, font=self._font(23, True), fill=self.TEXT)
        self._draw_wrapped(draw, command_left, y + 48, commands, self._font(21), note_left - command_left - 18, self.DARK)
        draw.rounded_rectangle(
            (note_left, y + 13, right - 14, y + height - 13),
            radius=8,
            fill="#fff0f5",
        )
        self._draw_wrapped(draw, note_left + 12, y + 27, note, self._font(18), right - note_left - 38, self.LABEL_TEXT)

    def _draw_empty_card(self, draw: ImageDraw.ImageDraw, y: int, text: str, height: int = 92) -> None:
        draw.rounded_rectangle((self.LEFT, y, self.WIDTH - self.RIGHT, y + height), radius=10, fill="#fffafd", outline=self.TABLE_LINE, width=1)
        self._draw_centered(draw, self.WIDTH // 2, y + height // 2, text, self._font(23), self.MUTED)

    # Small drawing and data helpers -------------------------------------

    def _legend_chip_layout(
        self, labels: Sequence[tuple[int, str]], mode: ReportComparisonMode
    ) -> list[tuple[int, int, int, str, int]]:
        """Return wrapped (x, line, width, text, index) for every legend chip.

        Both legend sizing and drawing share this layout so the box height
        always matches the actual number of groups and their wrapping.
        """
        font = self._font(19)
        chips: list[tuple[int, int, int, str, int]] = []
        x, line = self.LEFT + 18, 0
        for index, (group_id, name) in enumerate(labels, 1):
            suffix = "全部比较" if mode == "all" else ("起点" if index == 1 else "目标")
            text = f"{report_group_marker(index)} {name or group_id} ({suffix})"
            width = self._text_width(text, font) + 30
            if chips and x + width > self.WIDTH - self.RIGHT - 18:
                line += 1
                x = self.LEFT + 18
            chips.append((x, line, width, text, index))
            x += width + 10
        return chips

    def _legend_height(self, labels: Sequence[tuple[int, str]], mode: ReportComparisonMode = "source") -> int:
        if not labels:
            return 0
        chips = self._legend_chip_layout(labels, mode)
        lines = chips[-1][1] + 1 if chips else 0
        return 48 + lines * 34 + 12

    def _table_header(
        self,
        draw: ImageDraw.ImageDraw,
        y: int,
        columns: Sequence[tuple[str, int]],
    ) -> None:
        """Deprecated layout anchor retained for downstream report extensions.

        New reports deliberately render card sections instead of a table.
        """
        del draw, y, columns

    def _duplicate_row_height(self, nickname: str, groups: str) -> int:
        return max(150, self._text_block_height(groups, self._font(18), self._content_width - 116) + 118, self._text_block_height(nickname, self._font(23, True), 360) + 112)

    def _group_markers(self, groups: Any, labels: Sequence[tuple[int, str]]) -> str:
        if labels:
            present = {int(row.get("group_id", 0)) for row in groups}
            return "  ".join(report_group_marker(index) for index, (group_id, _name) in enumerate(labels, 1) if group_id in present) or "未知"
        return "  ".join(str(row.get("group_name") or row.get("group_id") or "未知") for row in groups) or "未知"

    def _normalise_groups(self, groups: Sequence[Mapping[str, Any]] | str) -> list[dict[str, Any]]:
        if isinstance(groups, str):
            return [{"group_id": 0, "group_name": item.strip()} for item in groups.split("、") if item.strip()]
        return [dict(row) for row in groups]

    def _normalise_prizes(self, prizes: Sequence[Mapping[str, Any]] | str) -> list[dict[str, Any]]:
        if isinstance(prizes, str):
            if not prizes or prizes == "无":
                return []
            return [{"prize_name": item.strip(), "quantity": 1} for item in prizes.split("；") if item.strip()]
        return [dict(row) for row in prizes]

    @staticmethod
    def _prize_rank_name(rank: int) -> str:
        names = ("一等奖", "二等奖", "三等奖")
        return names[rank - 1] if rank <= len(names) else f"第 {rank} 奖"

    def _draw_quantity_badge(self, draw: ImageDraw.ImageDraw, x: int, y: int, value: int) -> None:
        draw.rounded_rectangle((x, y, x + 68, y + 38), radius=19, fill="#f8d9e6", outline="#e8abc3", width=1)
        self._draw_centered(draw, x + 34, y + 19, str(value), self._font(20, True), self.LABEL_TEXT)

    def _draw_count_badge(self, draw: ImageDraw.ImageDraw, x: int, y: int, count: str, suffix: str) -> None:
        draw.rounded_rectangle((x, y, x + 112, y + 36), radius=18, fill=self.KEY_FILL, outline="#edb8cc", width=1)
        self._draw_centered(draw, x + 56, y + 18, f"{count} {suffix}", self._font(18, True), self.LABEL_TEXT)

    def _draw_number_badge(self, draw: ImageDraw.ImageDraw, x: int, y: int, number: int, fill: str) -> None:
        draw.ellipse((x, y, x + 42, y + 42), fill=fill, outline="#ffffff", width=2)
        self._draw_centered(draw, x + 21, y + 21, str(number), self._font(19, True), self.LABEL_TEXT)

    def _draw_chip(self, draw: ImageDraw.ImageDraw, x: int, y: int, text: str, fill: str, color: str) -> None:
        font = self._font(18, True)
        width = self._text_width(text, font) + 28
        draw.rounded_rectangle((x, y, x + width, y + 31), radius=15, fill=fill)
        draw.text((x + 14, y + 5), text, font=font, fill=color)

    def _draw_avatar_at(self, image: Image.Image, draw: ImageDraw.ImageDraw, left: int, top: int, size: int, avatar_path: Path | None, label: str) -> None:
        avatar = None
        if avatar_path and avatar_path.exists():
            try:
                with Image.open(avatar_path) as source:
                    avatar = ImageOps.fit(source.convert("RGB"), (size, size), method=Image.Resampling.LANCZOS)
            except (OSError, ValueError):
                avatar = None
        if avatar is not None:
            mask = Image.new("L", (size, size), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
            image.paste(avatar, (left, top), mask)
            draw.ellipse((left, top, left + size - 1, top + size - 1), outline=self.TABLE_LINE, width=1)
            return
        fill = self.SOFT_LAVENDER if label and label[-1:].isdigit() and int(label[-1]) % 2 else "#fde1ec"
        draw.ellipse((left, top, left + size - 1, top + size - 1), fill=fill, outline="#dfa1b7", width=1)
        self._draw_centered(draw, left + size // 2, top + size // 2, label[-2:] or "?", self._font(max(14, size // 3), True), self.ACCENT)

    def _draw_wrapped(self, draw: ImageDraw.ImageDraw, x: int, y: int, value: str, font: ImageFont.ImageFont, width: int, fill: str, max_lines: int | None = None) -> int:
        lines = self._wrap_text(value, font, width)
        if max_lines is not None and len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = self._ellipsize(lines[-1], font, width)
        line_height = self._line_height(font)
        for line in lines:
            draw.text((x, y), line, font=font, fill=fill)
            y += line_height
        return y

    def _draw_centered(self, draw: ImageDraw.ImageDraw, center_x: int, center_y: int, text: str, font: ImageFont.ImageFont, fill: str) -> None:
        box = draw.textbbox((0, 0), text, font=font)
        draw.text((center_x - (box[2] - box[0]) // 2, center_y - (box[3] - box[1]) // 2 - box[1]), text, font=font, fill=fill)

    def _text_block_height(self, value: str, font: ImageFont.ImageFont, width: int) -> int:
        return len(self._wrap_text(value, font, width)) * self._line_height(font)

    def _ellipsize(self, value: str, font: ImageFont.ImageFont, width: int) -> str:
        if self._text_width(value, font) <= width:
            return value
        suffix = "..."
        result = value
        while result and self._text_width(result + suffix, font) > width:
            result = result[:-1]
        return result + suffix

    def _row_height(self, value: str, font: ImageFont.ImageFont, width: int) -> int:
        return max(52, self._text_block_height(value, font, width - 32) + 20)

    @staticmethod
    def _line_height(font: ImageFont.ImageFont) -> int:
        bbox = font.getbbox("Ag")
        return max(24, bbox[3] - bbox[1] + 7)

    @staticmethod
    def _draw_heart(draw: ImageDraw.ImageDraw, center_x: int, center_y: int, size: int, fill: str) -> None:
        radius = max(3, size // 4)
        draw.ellipse((center_x - radius * 2, center_y - radius, center_x, center_y + radius), fill=fill)
        draw.ellipse((center_x, center_y - radius, center_x + radius * 2, center_y + radius), fill=fill)
        draw.polygon([(center_x - radius * 2, center_y), (center_x + radius * 2, center_y), (center_x, center_y + size)], fill=fill)

    @staticmethod
    def _draw_bow(draw: ImageDraw.ImageDraw, center_x: int, center_y: int, size: int, fill: str, center_fill: str) -> None:
        half = size // 2
        draw.ellipse((center_x - size, center_y - half, center_x - 4, center_y + half), fill=fill)
        draw.ellipse((center_x + 4, center_y - half, center_x + size, center_y + half), fill=fill)
        draw.polygon([(center_x - half, center_y + 8), (center_x - 4, center_y + half + 20), (center_x, center_y + 4)], fill=fill)
        draw.polygon([(center_x + half, center_y + 8), (center_x + 4, center_y + half + 20), (center_x, center_y + 4)], fill=fill)
        draw.rounded_rectangle((center_x - 8, center_y - 11, center_x + 8, center_y + 11), radius=5, fill=center_fill)

    def _wrap_text(self, value: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
        if not value:
            return [""]
        lines: list[str] = []
        for paragraph in value.splitlines() or [""]:
            if not paragraph:
                lines.append("")
                continue
            current = ""
            for character in paragraph:
                candidate = current + character
                if current and self._text_width(candidate, font) > max_width:
                    lines.append(current)
                    current = character
                else:
                    current = candidate
            if current:
                lines.append(current)
        return lines or [""]

    @staticmethod
    def _text_width(value: str, font: ImageFont.ImageFont) -> int:
        bbox = font.getbbox(value)
        return bbox[2] - bbox[0]

    @staticmethod
    def _value(row: Any, key: str, default: Any = None) -> Any:
        if isinstance(row, Mapping):
            return row.get(key, default)
        try:
            return row[key]
        except (IndexError, KeyError, TypeError):
            return default

    def _font(self, size: int, bold: bool = False):
        key = (size, bold)
        if key in self._font_cache:
            return self._font_cache[key]
        candidates: list[Path] = []
        if self.font_path:
            candidates.append(self.font_path)
        if bold:
            candidates.extend([Path(r"C:\Windows\Fonts\msyhbd.ttc"), Path(r"C:\Windows\Fonts\Dengb.ttf"), Path(r"C:\Windows\Fonts\simhei.ttf")])
        candidates.extend([Path(r"C:\Windows\Fonts\msyh.ttc"), Path(r"C:\Windows\Fonts\Deng.ttf"), Path(r"C:\Windows\Fonts\simhei.ttf"), Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"), Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")])
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

    def _save(self, image: Image.Image, prefix: str) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        cutoff = datetime.now(self.zone) - timedelta(hours=self.retention_hours)
        for path in self.output_dir.glob("*.png"):
            try:
                if datetime.fromtimestamp(path.stat().st_mtime, self.zone) < cutoff:
                    path.unlink(missing_ok=True)
            except OSError:
                continue
        path = self.output_dir / f"{prefix}_{uuid4().hex}.png"
        image.save(path, format="PNG", optimize=True)
        return path
