from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from PIL import ImageDraw

from bot.services.reports import ReportRenderer
from bot.services.zhijiang_live_guard import LiveGuardStatus, LiveSchedule


class ZhijiangLiveReportRenderer(ReportRenderer):
    """Render the Zhijiang live schedule and guard state as local PNG cards."""

    _WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
    _MEMBER_COLORS = (
        ("#fbe5ed", "#a63d65"),
        ("#e3f4ee", "#28715f"),
        ("#e8eefb", "#3f6399"),
        ("#fff0db", "#9a5b1f"),
        ("#f0eafa", "#705698"),
    )

    def render_schedule(
        self,
        status: LiveGuardStatus,
        now: datetime,
        lookahead_days: int,
    ) -> Path:
        entries = list(status.upcoming[:20])
        title = "枝江直播日程"
        subtitle = f"未来 {lookahead_days} 天  |  截至 {now:%Y-%m-%d %H:%M}"
        if not entries:
            image, draw, y = self._new_report(title, subtitle, 132, "仅显示可识别的 Bilibili 直播")
            self._draw_empty_card(draw, y, "缓存中暂无未来直播日程", 92)
            return self._save(image, "zhijiang_schedule")

        grouped: dict[date, list[LiveSchedule]] = defaultdict(list)
        for entry in entries:
            grouped[entry.starts_at.date()].append(entry)
        more_count = len(status.upcoming) - len(entries)
        body_height = (
            18
            + sum(58 + len(day_entries) * 160 for day_entries in grouped.values())
            + (52 if more_count else 0)
        )
        image, draw, y = self._new_report(
            title,
            subtitle,
            body_height,
            "仅显示可识别的 Bilibili 直播",
        )
        for date_value, day_entries in grouped.items():
            self._draw_date_heading(draw, y, date_value)
            y += 58
            for index, entry in enumerate(day_entries):
                self._draw_schedule_card(draw, y, entry, index)
                y += 160
        if more_count:
            self._draw_notice(draw, y, f"还有 {more_count} 条日程未显示")
        return self._save(image, "zhijiang_schedule")

    def render_status(self, status: LiveGuardStatus, now: datetime) -> Path:
        rows = [
            (
                "直播防护",
                "已开启，直播开始时会自动关闭小游戏。" if status.enabled else "未开启，不会自动关闭小游戏。",
                "开启" if status.enabled else "关闭",
                "success" if status.enabled else "danger",
            ),
            (
                "小游戏总开关",
                "当前允许使用小游戏。" if status.global_game_enabled else "当前已关闭小游戏入口。",
                "开启" if status.global_game_enabled else "关闭",
                "success" if status.global_game_enabled else "danger",
            ),
            (
                "自动暂停",
                f"将在 {status.paused_until:%Y-%m-%d %H:%M} 后自动恢复。"
                if status.paused_until
                else "当前没有自动恢复倒计时。",
                status.paused_until.strftime("%H:%M") if status.paused_until else "无",
                "warning" if status.paused_until else "neutral",
            ),
            (
                "日程缓存",
                f"未来可识别直播 {len(status.upcoming)} 条。",
                "已刷新" if status.last_refresh else "未刷新",
                "success" if status.last_refresh else "warning",
            ),
        ]
        if status.last_refresh:
            rows[-1] = (
                "日程缓存",
                f"最近刷新 {status.last_refresh}；未来可识别直播 {len(status.upcoming)} 条。",
                "已刷新",
                "success",
            )
        if status.last_error:
            rows.append(("最近错误", status.last_error, "需检查", "danger"))

        body_height = 18 + sum(self._status_row_height(label, value) for label, value, _, _ in rows)
        image, draw, y = self._new_report(
            "枝江直播状态",
            f"防护运行快照  |  {now:%Y-%m-%d %H:%M}",
            body_height,
            "日程源和状态均保留在本地缓存",
        )
        for label, value, badge, tone in rows:
            height = self._status_row_height(label, value)
            self._draw_status_row(draw, y, height, label, value, badge, tone)
            y += height
        return self._save(image, "zhijiang_status")

    def render_refresh(self, status: LiveGuardStatus, now: datetime) -> Path:
        if status.last_error:
            return self.render_notice(
                "枝江直播刷新失败",
                f"未能获取最新日程，已保留本地缓存。\n{status.last_error}",
                level="error",
                subtitle=f"尝试时间  |  {now:%Y-%m-%d %H:%M}",
            )
        return self.render_notice(
            "枝江直播已刷新",
            f"未来可识别直播 {len(status.upcoming)} 条。\n小游戏总开关当前{'开启' if status.global_game_enabled else '关闭'}。",
            level="success",
            subtitle=f"完成时间  |  {now:%Y-%m-%d %H:%M}",
        )

    def render_notice(
        self,
        title: str,
        message: str,
        *,
        level: Literal["success", "error", "warning"] = "warning",
        subtitle: str = "枝江直播服务",
    ) -> Path:
        lines = self._wrap_text(message, self._font(25), self._content_width - 84)
        height = max(156, 94 + len(lines) * self._line_height(self._font(25)))
        image, draw, y = self._new_report(title, subtitle, height + 18)
        palette = {
            "success": ("#e3f4ee", "#b9dfd2", "#28715f", "完成"),
            "error": ("#fbe8e9", "#efc5c9", "#a3434b", "失败"),
            "warning": ("#fff2dc", "#efd7ad", "#955d1b", "提示"),
        }
        fill, outline, color, tag = palette[level]
        draw.rounded_rectangle(
            (self.LEFT, y, self.WIDTH - self.RIGHT, y + height),
            radius=10,
            fill=fill,
            outline=outline,
            width=1,
        )
        self._draw_chip(draw, self.LEFT + 24, y + 22, tag, "#ffffff", color)
        self._draw_wrapped(
            draw,
            self.LEFT + 24,
            y + 74,
            message,
            self._font(25),
            self._content_width - 48,
            self.TEXT,
        )
        return self._save(image, "zhijiang_notice")

    def _draw_date_heading(self, draw: ImageDraw.ImageDraw, y: int, date_value: date) -> None:
        weekday = self._WEEKDAYS[date_value.weekday()]
        draw.rounded_rectangle(
            (self.LEFT, y, self.WIDTH - self.RIGHT, y + 46),
            radius=16,
            fill="#e7f4ef",
            outline="#c8e3d6",
            width=1,
        )
        draw.text(
            (self.LEFT + 18, y + 10),
            f"{date_value:%m 月 %d 日}  {weekday}",
            font=self._font(21, True),
            fill="#28715f",
        )

    def _draw_schedule_card(
        self,
        draw: ImageDraw.ImageDraw,
        y: int,
        entry: LiveSchedule,
        index: int,
    ) -> None:
        fill, color = self._MEMBER_COLORS[index % len(self._MEMBER_COLORS)]
        draw.rounded_rectangle(
            (self.LEFT, y, self.WIDTH - self.RIGHT, y + 144),
            radius=18,
            fill="#fffefd",
            outline="#e4e9e7",
            width=1,
        )
        draw.rounded_rectangle(
            (self.LEFT + 18, y + 18, self.LEFT + 164, y + 70),
            radius=16,
            fill=fill,
        )
        self._draw_centered(
            draw,
            self.LEFT + 91,
            y + 44,
            f"{entry.starts_at:%H:%M}",
            self._font(24, True),
            color,
        )
        title_x = self.LEFT + 188
        self._draw_wrapped(draw, title_x, y + 20, entry.title, self._font(22, True), 300, self.TEXT, max_lines=2)
        draw.text((title_x, y + 88), self._ellipsize(entry.event_type or "直播", self._font(17), 280), font=self._font(17), fill=self.MUTED)
        member = entry.category or "其他"
        member_font = self._font(19, True)
        member_width = self._text_width(member, member_font) + 30
        self._draw_chip(
            draw,
            title_x,
            y + 112,
            member,
            fill,
            color,
        )

    def _status_row_height(self, label: str, value: str) -> int:
        """Measure the complete card before drawing so text never meets its edge."""
        title_font = self._font(22, True)
        body_font = self._font(18)
        title_box = title_font.getbbox(label or "Ag")
        body_box = body_font.getbbox("Ag")
        body_y = 18 + title_box[3] - body_box[1] + 16
        body_height = self._text_block_height(value, body_font, self._content_width - 70)
        # The visual card stops 10px before the row's layout slot. Keep an
        # additional 22px of breathing room below the final glyph.
        return max(150, body_y + body_height + 32)

    def _draw_status_row(
        self,
        draw: ImageDraw.ImageDraw,
        y: int,
        height: int,
        label: str,
        value: str,
        badge: str,
        tone: Literal["success", "danger", "warning", "neutral"],
    ) -> None:
        colors = {
            "success": ("#e3f4ee", "#28715f"),
            "danger": ("#fbe8e9", "#a3434b"),
            "warning": ("#fff2dc", "#955d1b"),
            "neutral": ("#edf1f5", "#5b6875"),
        }
        fill, color = colors[tone]
        draw.rounded_rectangle(
            (self.LEFT, y, self.WIDTH - self.RIGHT, y + height - 10),
            radius=18,
            fill="#fffefd",
            outline="#e4e9e7",
            width=1,
        )
        title_font = self._font(22, True)
        body_font = self._font(18)
        title_box = title_font.getbbox(label or "Ag")
        body_box = body_font.getbbox("Ag")
        title_y = y + 18
        body_y = title_y + title_box[3] - body_box[1] + 16
        draw.rectangle((self.LEFT + 20, y + 21, self.LEFT + 28, y + 55), fill=color)
        draw.text((self.LEFT + 46, title_y), label, font=title_font, fill=self.TEXT)
        self._draw_wrapped(
            draw,
            self.LEFT + 46,
            body_y,
            value,
            body_font,
            self._content_width - 70,
            self.MUTED,
        )
        badge_font = self._font(19, True)
        badge_width = self._text_width(badge, badge_font) + 30
        self._draw_chip(
            draw,
            self.WIDTH - self.RIGHT - badge_width - 18,
            y + 18,
            badge,
            fill,
            color,
        )
