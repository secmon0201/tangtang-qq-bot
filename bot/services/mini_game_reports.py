from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any, Mapping

from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont

from bot.services.image_style import (
    AURORA_SIGNAL_COLORS,
    paste_horizontal_gradient,
    paste_multistop_gradient,
)
from bot.services.reports import ReportRenderer


GAME_DETAIL_PAGES = {
    "roulette": {
        "title": "俄罗斯转盘",
        "command": "#装填  ->  #开枪",
        "rule": "一把枪六格一发；同一玩家可连续开枪；每局随机 2-3 个事件。",
        "ranking": "榜单：#转盘榜 / #转盘总榜 · 按阵亡次数排序。",
        "fill": "#fff1f6",
        "events": (
            ("同生共死", "随机标记已开枪者；之后有人中枪时，标记者也会一同阵亡。"),
            ("反弹", "命中伤害转移给上一位开枪者；没有上一位时仍由当前玩家承担。"),
            ("弹仓偏移", "真弹会在尚未扣动的弹仓中悄悄换到另一格。"),
            ("意外失误", "触发式：下一位开枪者记一次阵亡，但弹仓不前进，游戏继续。"),
            ("手指抽筋", "触发式：下一次开枪连续两枪；反弹优先，任一枪命中即按实际命中者结算。"),
            ("连发左轮", "仅第 4 枪起可能触发；触发式：下一次互动改用独立弹仓，按当前参与人数倒序连开，最多六枪；副枪命中会记死亡，但不推进原弹仓，游戏继续。"),
            ("连发 AK", "仅第 4 枪起可能触发；触发式：独立弹匣随机装入 1-6 发子弹，按参与人数倒序连开，最多六枪；可无人中弹或全员阵亡，主弹仓不推进。"),
            ("手抖偏枪", "触发式：下一次开枪时枪口随机甩向此前参与者；空枪无事，真弹则由被甩到的人中弹。"),
            ("枪口炸膛", "触发式：直到原本的致命一枪才揭晓；真弹未能射出，原定受害者不记死亡、不禁言，本局平安结束。"),
            ("异常真弹", "触发式：当前开枪者命中真弹时引发范围爆炸，所有参与者均记阵亡；触发者正常禁言，其他参与者禁言时长减半，本局结束。"),
        ),
    },
    "bomb": {
        "title": "定时炸弹",
        "command": "#装弹  ->  #丢给 @成员",
        "rule": "固定 120 秒；仅当前持有者可传，须真实 @。成语接龙 DLC 见本合并消息第六张图。",
        "ranking": "榜单：#炸弹榜 / #炸弹总榜 · 记录被炸次数和传递次数。",
        "fill": "#fff8fb",
        "events": (
            ("烫手加速", "炸弹倒计时缩短，剩余时间更紧张。"),
            ("时间失控", "倒计时随机加快或放缓，但不会超过本局开局设置的时长。"),
            ("定点追踪", "炸弹自动落到一名已参与玩家手中。"),
            ("惯性反弹", "触发式：下一次传递后，炸弹立刻弹回传递者手中。"),
            ("黑洞投递", "触发式：下一次传递无视原本 @ 的对象，改飞向另一名已参与者。"),
            ("逆向快递", "触发式：下一次传递沿原路回到上一位持有者手中。"),
        ),
    },
    "bomb_idiom": {
        "title": "定时炸弹 · 成语接龙",
        "command": "#装弹成语[专业/娱乐] [60-600]",
        "rule": "默认娱乐模式接受成语和四字词；加“专业”后仅收录成语。总时长默认 120 秒，可设 60-600 秒；须真实 @。",
        "ranking": "共用榜单：#炸弹榜 / #炸弹总榜 · 记录被炸次数和传递次数。",
        "fill": "#fff6fa",
        "events": (
            ("接龙规则", "专业模式仅有效成语；娱乐模式额外接受离线四字词。首句可任意有效词；之后首字接上句末字；每局词条默认不可重复，必须真实 @ 其他成员。"),
            ("双计时", "全局倒计时与持有人倒计时同时生效；炸弹实际换手后，下一位持有人的 60 秒重新开始。"),
            ("继承传统事件", "本模式完整继承烫手加速、时间失控、定点追踪、惯性反弹、黑洞投递、逆向快递，具体效果见上一张定时炸弹图。"),
            ("旧词回响", "成语达到一定数量后，接龙锚点回到一条历史成语；只借末字接龙，该历史词本身仍不能重复。"),
            ("首字回环", "触发式：当前成语接上后，下一句改从它的首字接龙；没有可接成语时不生效。"),
            ("词条作废", "触发式：当前成功接上的成语被划掉，炸弹留在当前玩家手里，必须换一条成语重接。"),
            ("自由开篇", "幸运事件：下一手可任选从未出现的有效成语开局，无须衔接当前末字。"),
            ("孤立开篇", "下一手可任选未出现成语，但首字不得等于任何历史成语的末字。"),
            ("成语事件节奏", "首次事件维持开局后 15-35 秒；之后须间隔至少 30 秒且经过一次有效传递。总数最多为开局时长 ÷ 30。"),
        ),
    },
    "dice": {
        "title": "幸运骰局",
        "command": "#骰子",
        "rule": "点数范围 1-120 且不重复；每人一投，后悔药例外；每局随机 5-10 个事件。",
        "ranking": "榜单：#骰子榜 / #骰子总榜 · 分别记录欧皇与非酋次数。",
        "fill": "#fff3f8",
        "events": (
            ("后悔药", "随机已投玩家可再发一次 #骰子 重投；不操作则保留原点数。"),
            ("滋蹦的大手", "随机已投玩家被强制重投，机器人会展示旧点数与新点数。"),
            ("命运互换", "两名已投玩家交换点数，机器人会展示双方的点数变化。"),
            ("大小反转", "所有已投点数变为 121-原点数，并逐一展示每人的新点数。"),
            ("王座易主", "触发式：下一位投骰者与当前最高分玩家交换点数。"),
            ("双倍好运", "触发式：下一位投骰会掷两次，取其中较高的唯一有效点数。"),
            ("双倍倒霉", "触发式：下一位投骰会掷两次，取其中较低的唯一有效点数。"),
            ("镜像骰面", "随机一名已投玩家的点数变为 121-原点数，并展示前后数值。"),
            ("连号追击", "随机一名已投玩家向相邻空位移动一格，并展示旧点数与新点数。"),
            ("非酋救济", "当前最低分玩家重投到更高的空闲点数，并展示前后数值。"),
            ("欧皇税", "当前最高分玩家重投到更低的空闲点数，并展示前后数值。"),
            ("高台跃迁", "触发式：下一位投骰从 91-120 的高分区掷出唯一有效点数。"),
            ("深渊试炼", "触发式：下一位投骰从 1-30 的低分区掷出唯一有效点数。"),
            ("幸运折半", "随机一名已投玩家向原点数的一半靠近，并展示前后数值。"),
            ("末位逆袭", "当前最低分玩家与随机其他玩家交换点数，并展示双方变化。"),
            ("双生骰面", "触发式：下一位骰子先正常掷出，再翻成其镜像点数 121-原点数。"),
        ),
    },
    "guess": {
        "title": "猜数字",
        "command": "#猜数  ->  #猜 123",
        "rule": "目标为 0-999，恶魔数字永不成为答案；猜到恶魔数字会撤回消息并受诅咒禁言。十次未中或 120 秒超时，所有参与者接受禁言。差距小于 100 会提示接近，远距离且同位正确也会提示。",
        "ranking": "榜单：#猜数榜 / #猜数总榜 · 分别记录猜中和猜错次数。",
        "fill": "#fff7fb",
        "events": (
            ("发散思维", "触发式：下一次有效猜测只改答案的十位或个位，百位不变；本次不计入十次限制。"),
            ("温差提示", "根据最近一次错误猜测，提示与答案距离不足 50 或超过 50。"),
            ("数字回声", "给出弱线索，例如答案奇偶、三位数字之和或个位奇偶。"),
            ("数位透视", "随机公开答案的百位、十位或个位中的一位数字。"),
        ),
    },
}

class MiniGameReportRenderer(ReportRenderer):
    """Local PNG reports for mini-game menus and privacy-safe scoreboards."""

    # 今日老婆 -------------------------------------------------------------

    FATE_ASPECT_RATIO = 16 / 9
    FATE_TEXT = "#3f3e56"
    FATE_MUTED = "#7e7b91"
    FATE_ACCENT = "#7568c7"
    FATE_SOFT = "#edf9f7"
    FATE_BORDER = "#e6e1ef"
    FATE_CORAL = "#f26f82"
    FATE_PINK = "#f26f82"
    FATE_PURPLE = "#8d67ce"
    FATE_MINT = "#47c9b5"
    FATE_YELLOW = "#f2ce63"
    FATE_PANEL = "#ffffff"
    FATE_PANEL_ALT = "#fbfaff"
    FATE_EDGE_STYLES = {
        "ordinary": ("普通", "#6f9fd2"),
        "relay": ("接力", "#9a7bc8"),
        "contested": ("撞车", "#df9b3f"),
        "cycle": ("闭环", "#54a98b"),
        "mutual": ("双向", "#e85c91"),
    }
    FATE_DIVORCED_EDGE_STYLE = ("已离婚", "#aeb5be")

    def render_today_wife_intro_card(self) -> Path:
        width, height = 960, 540
        image, draw = self._new_fate_canvas(width, height)

        self._draw_fate_masthead(image, draw, "今日", "老婆", seal="FATE", left=54)
        paste_horizontal_gradient(
            image,
            (54, 112, 224, 148),
            "#ffe1ea",
            "#d9f4ef",
            radius=18,
        )
        self._draw_centered(draw, 139, 130, "群内随机缘分", self._font(18, True), self.FATE_ACCENT)

        intro_font = self._font(25)
        y = 180
        for line in (
            "发送 #今日老婆随机抽取，",
            "让全群共同写出一段关系故事。",
        ):
            draw.text((54, y), line, font=intro_font, fill=self.FATE_TEXT)
            y += 43

        chip_font = self._font(18, True)
        chip_x = 54
        chip_width = 126
        for label in ("#我的缘分", "#群缘分", "#离婚"):
            draw.rounded_rectangle(
                (chip_x, 378, chip_x + chip_width, 416),
                radius=19,
                fill="#ffffff",
                outline=self.FATE_BORDER,
                width=1,
            )
            self._draw_centered(draw, chip_x + chip_width // 2, 397, label, chip_font, self.FATE_ACCENT)
            chip_x += chip_width + 12
        draw.text(
            (54, 444),
            "每日一抽 · 离婚后可重新抽取一次",
            font=self._font(18),
            fill=self.FATE_MUTED,
        )

        centers = {
            "小明": (588, 208),
            "小夏": (826, 208),
            "小青": (706, 398),
        }
        avatar_size = 92
        avatar_radius = avatar_size / 2 + 5
        self._draw_fate_arrow(
            draw,
            centers["小明"],
            centers["小夏"],
            avatar_radius,
            curved=True,
            color=self.FATE_EDGE_STYLES["mutual"][1],
        )
        self._draw_fate_arrow(
            draw,
            centers["小夏"],
            centers["小明"],
            avatar_radius,
            curved=True,
            color=self.FATE_EDGE_STYLES["mutual"][1],
        )
        self._draw_fate_arrow(
            draw,
            centers["小青"],
            centers["小夏"],
            avatar_radius,
            curved=False,
            color=self.FATE_EDGE_STYLES["contested"][1],
        )

        avatar_fills = {
            "小明": "#cdeff2",
            "小夏": "#e5f4bd",
            "小青": "#ffd9df",
        }
        for name, (center_x, center_y) in centers.items():
            left = int(center_x - avatar_size / 2)
            top = int(center_y - avatar_size / 2)
            draw.ellipse(
                (left, top, left + avatar_size, top + avatar_size),
                fill=avatar_fills[name],
                outline="#ffffff",
                width=4,
            )
            draw.ellipse(
                (left - 2, top - 2, left + avatar_size + 2, top + avatar_size + 2),
                outline=self.FATE_BORDER,
                width=2,
            )
            self._draw_centered(
                draw, center_x, center_y, name[-1], self._font(28, True), self.FATE_TEXT
            )
            self._draw_centered(
                draw, center_x, top + avatar_size + 24, name, self._font(19, True), self.FATE_TEXT
            )

        self._draw_intro_relation_label(draw, 707, 154, "双向奔赴", "#e7f7f8", self.FATE_ACCENT)
        self._draw_intro_relation_label(draw, 786, 380, "缘分撞车", "#fff0dc", "#bd7624")
        draw.text((694, 488), "随机相遇，也会连成群像故事", font=self._font(17), fill=self.FATE_MUTED)
        return self._save(image, "today_wife_intro")

    def _draw_intro_relation_label(
        self,
        draw: ImageDraw.ImageDraw,
        center_x: int,
        center_y: int,
        text: str,
        fill: str,
        color: str,
    ) -> None:
        font = self._font(17, True)
        width = self._text_width(text, font) + 28
        draw.rounded_rectangle(
            (center_x - width // 2, center_y - 17, center_x + width // 2, center_y + 17),
            radius=17,
            fill=fill,
        )
        self._draw_centered(draw, center_x, center_y, text, font, color)

    def render_today_wife(
        self, record: Mapping[str, Any], story_lines: tuple[str, ...], context_lines: tuple[str, ...], avatar_paths: Mapping[int, Path]
    ) -> Path:
        width = 840
        padding = 58
        avatar_size = 240
        target_name = str(record.get("target_nickname") or "这位群友")
        intro_line = str(record.get("intro_line") or "").strip()
        if not intro_line:
            raise ValueError("today-wife cards require a randomized intro line")
        paragraphs = [intro_line, *story_lines, *context_lines]
        font = self._font(27)
        body_width = width - padding * 2
        line_count = sum(len(self._wrap_text(paragraph, font, body_width)) for paragraph in paragraphs)
        paragraph_gaps = 12 * max(0, len(paragraphs) - 1)
        metadata_height = 104
        header_height = 74
        height = (
            padding
            + header_height
            + avatar_size
            + metadata_height
            + line_count * self._line_height(font)
            + paragraph_gaps
            + padding
            + 24
        )
        image, draw = self._new_fate_canvas(width, height)
        self._draw_fate_masthead(image, draw, "今日", "老婆", seal="DRAW", left=padding)
        avatar_x = (width - avatar_size) // 2
        avatar_y = padding + header_height
        self._draw_fate_avatar_at(image, draw, avatar_x, avatar_y, avatar_size, avatar_paths.get(int(record.get("target_id") or 0)), target_name)
        y = avatar_y + avatar_size + 28
        episode_title = str(record.get("episode_title") or "今日篇章")
        relationship = str(record.get("relationship_key") or "今日同行者")
        self._draw_centered(draw, width // 2, y + 13, f"今日篇章 · {episode_title}", self._font(20, True), self.FATE_MUTED)
        self._draw_centered(draw, width // 2, y + 48, relationship, self._font(25, True), self.FATE_ACCENT)
        tags = tuple(str(tag) for tag in record.get("story_tags", ()) if str(tag))
        if tags:
            self._draw_centered(draw, width // 2, y + 80, " · ".join(tags), self._font(17, True), self.FATE_CORAL)
        y += metadata_height
        for index, paragraph in enumerate(paragraphs):
            y = self._draw_fate_wrapped(
                draw,
                padding,
                y,
                paragraph,
                font,
                body_width,
                (
                    target_name,
                    str(record.get("context_nickname") or ""),
                    str(record.get("taken_by_nickname") or ""),
                ),
            )
            if index < len(paragraphs) - 1:
                y += 12
        return self._save(image, "today_wife")

    def render_divorce(
        self,
        record: Mapping[str, Any],
        lines: tuple[str, str],
        avatar_paths: Mapping[int, Path],
        relation: Mapping[str, Any] | None = None,
    ) -> Path:
        relation = relation or {}
        actor_name = str(record.get("actor_nickname") or "这位群友")
        target_name = str(record.get("target_nickname") or "这位群友")
        width = 840
        padding = 58
        avatar_size = 188
        paragraphs = lines
        font = self._font(24)
        body_width = width - padding * 2
        line_count = sum(len(self._wrap_text(paragraph, font, body_width)) for paragraph in paragraphs)
        # The relationship is retained, so the farewell needs room for its
        # frozen score and the common command tray instead of behaving like a
        # terminal error card.
        height = 76 + avatar_size + 122 + line_count * self._line_height(font) + 126 + 94
        image, draw = self._new_fate_canvas(width, height)
        self._draw_fate_masthead(image, draw, "今日", "关系留档", seal="PAST", left=padding)
        draw.text((padding, 96), f"{actor_name} 与 {target_name} · 今日离婚", font=self._font(20, True), fill=self.FATE_CORAL)
        avatar_x = (width - avatar_size) // 2
        self._draw_fate_avatar_at(image, draw, avatar_x, 126, avatar_size, avatar_paths.get(int(record.get("target_id") or 0)), target_name)
        frozen = int(relation.get("frozen_affection") if relation.get("frozen_affection") is not None else relation.get("affection") or 0)
        self._draw_centered(draw, width // 2, 338, f"曾经好感 {frozen}", self._font(20, True), self.FATE_ACCENT)
        y = 374
        for index, paragraph in enumerate(paragraphs):
            y = self._draw_fate_wrapped(
                draw, padding, y, paragraph, font, body_width, (actor_name, target_name)
            )
            if index < len(paragraphs) - 1:
                y += 12
        y += 10
        self._draw_relation_note(draw, padding, y, width - padding, relation, "这段关系留下的印记")
        self._draw_game_toolbar(draw, padding, height - 78, width - padding, interaction_remaining=None)
        return self._save(image, "today_wife_divorce")

    def render_today_wife_history(
        self,
        rows: list[Mapping[str, Any]],
        avatar_paths: Mapping[int, Path],
        empty_message: str,
    ) -> Path:
        width = 840
        padding = 52
        row_height = 138
        title_height = 124
        empty_height = 180
        height = title_height + (len(rows) * (row_height + 14) if rows else empty_height) + padding
        image, draw = self._new_fate_canvas(width, height)
        self._draw_fate_masthead(image, draw, "我的", "缘分", seal="MEMORY", left=padding)
        if not rows:
            if not empty_message.strip():
                raise ValueError("empty today-wife history requires a randomized message")
            self._draw_wrapped(
                draw,
                padding,
                title_height + 42,
                empty_message,
                self._font(23),
                width - padding * 2,
                self.FATE_MUTED,
                max_lines=4,
            )
            return self._save(image, "today_wife_history")
        y = title_height
        for row in rows:
            status = "已离婚" if str(row.get("status")) == "divorced" else "结缘"
            self._paste_fate_panel_gradient(
                image,
                (padding, y, width - padding, y + row_height),
                tone="#eef9f7" if status == "结缘" else "#f3f0f7",
            )
            target_id = int(row.get("target_id") or 0)
            target_name = str(row.get("target_nickname") or "这位群友")
            self._draw_fate_avatar_at(image, draw, padding + 18, y + 29, 76, avatar_paths.get(target_id), target_name)
            day = str(row.get("day") or "")
            draw.text((padding + 116, y + 15), target_name, font=self._font(25, True), fill=self.FATE_ACCENT)
            relationship = str(row.get("relationship_key") or "今日同行者")
            draw.text((padding + 116, y + 50), relationship, font=self._font(18), fill=self.FATE_ACCENT)
            tags = tuple(str(tag) for tag in row.get("story_tags", ()) if str(tag))
            note = " · ".join(tags) if tags else str(row.get("episode_title") or "随机相遇")
            draw.text((padding + 116, y + 78), self._ellipsize(note, self._font(17), 430), font=self._font(17), fill=self.FATE_MUTED)
            draw.text((padding + 116, y + 105), day, font=self._font(17), fill=self.FATE_MUTED)
            badge_fill = "#f1eef6" if status == "已离婚" else "#e1f6f2"
            badge_text = self.FATE_MUTED if status == "已离婚" else self.FATE_ACCENT
            font = self._font(19, True)
            badge_width = self._text_width(status, font) + 32
            left = width - padding - badge_width - 20
            draw.rounded_rectangle((left, y + 51, left + badge_width, y + 86), radius=17, fill=badge_fill)
            draw.text((left + 16, y + 59), status, font=font, fill=badge_text)
            y += row_height + 14
        return self._save(image, "today_wife_history")

    def render_today_wife_game_draw(
        self,
        record: Mapping[str, Any],
        story_lines: tuple[str, ...],
        context_lines: tuple[str, ...],
        avatar_paths: Mapping[int, Path],
        day_state: Mapping[str, Any],
        relation: Mapping[str, Any],
        *,
        draw_reveal: Mapping[str, Any] | None = None,
        available_actions: Any | None = None,
    ) -> Path:
        """The draw reveal doubles as an entry ticket to the shared story."""
        width, padding, avatar_size = 840, 52, 126
        target_name = str(record.get("target_nickname") or "这位群友")
        actor_name = str(record.get("actor_nickname") or "你")
        reveal = draw_reveal if isinstance(draw_reveal, Mapping) else {}
        reveal_blocks = tuple(item for item in reveal.get("blocks", ()) if isinstance(item, Mapping))
        paragraphs = [str(item.get("text") or "") for item in reveal_blocks if str(item.get("text") or "").strip()]
        if not paragraphs:
            paragraphs = [str(record.get("intro_line") or ""), *story_lines[:2], *context_lines[:1]]
        paragraphs = [item for item in paragraphs if item.strip()]
        body_font = self._font(24)
        body_width = width - padding * 2
        # Highlighted names are measured character by character when drawn,
        # so use the same wrapping model before fixing the canvas height.
        body_height = sum(
            self._fate_text_height(item, body_font, body_width)
            for item in paragraphs
        ) + 10 * max(0, len(paragraphs) - 1)
        hero_top = 128
        hero_height = 188
        story_start = hero_top + hero_height + 28
        story_draw_height = body_height + 10 * len(paragraphs)
        relation_y = story_start + story_draw_height + 4
        relation_height = self._relation_note_height(relation, width - padding * 2)
        toolbar_y = relation_y + relation_height + 18
        # The height follows the final toolbar position, not a separate
        # approximation, so dynamic narratives cannot crop the controls.
        toolbar_actions = available_actions
        if toolbar_actions is None:
            for source in (reveal, relation, record):
                if "available_actions" in source:
                    toolbar_actions = source.get("available_actions")
                    break
        height = toolbar_y + self._game_toolbar_height(5, toolbar_actions) + 32
        image, draw = self._new_fate_canvas(width, height)
        self._draw_fate_masthead(image, draw, "今日", "缘分档案", seal="DRAW", left=padding)
        self._draw_game_act(draw, padding, 93, day_state, width - padding * 2)
        self._paste_fate_panel_gradient(
            image,
            (padding, hero_top, width - padding, hero_top + hero_height),
            tone="#e8ddf7",
            radius=22,
        )
        actor_x = width - padding - avatar_size * 2 + 18
        target_x = width - padding - avatar_size
        avatar_y = hero_top + (hero_height - avatar_size) // 2
        connection_y = avatar_y + avatar_size // 2
        draw.line(
            (actor_x + avatar_size - 8, connection_y, target_x + 8, connection_y),
            fill=self.FATE_ACCENT,
            width=5,
        )
        self._draw_fate_avatar_at(image, draw, actor_x, avatar_y, avatar_size, avatar_paths.get(int(record.get("actor_id") or 0)), actor_name)
        self._draw_fate_avatar_at(image, draw, target_x, avatar_y, avatar_size, avatar_paths.get(int(record.get("target_id") or 0)), target_name)
        copy_x = padding + 24
        copy_width = actor_x - copy_x - 26
        draw.text((copy_x, hero_top + 24), "今日缘分", font=self._font(17, True), fill=self.FATE_MUTED)
        pair_text = self._ellipsize(f"{actor_name} → {target_name}", self._font(27, True), copy_width)
        draw.text((copy_x, hero_top + 53), pair_text, font=self._font(27, True), fill=self.FATE_ACCENT)
        relationship_label = str(reveal.get("relationship_label") or record.get("relationship_key") or "今日同行者")
        if str(record.get("draw_source") or "random") == "directed":
            relationship_label = f"{relationship_label} · 指定缘分"
        draw.text((copy_x, hero_top + 96), self._ellipsize(relationship_label, self._font(20, True), copy_width), font=self._font(20, True), fill=self.FATE_CORAL)
        affection = int(relation.get("affection") or 0)
        draw.text((copy_x, hero_top + 133), f"今日好感 {affection} · 从这一刻开始", font=self._font(18), fill=self.FATE_MUTED)
        y = story_start
        for paragraph in paragraphs:
            y = self._draw_fate_wrapped(draw, padding, y, paragraph, body_font, body_width, (target_name,), text_color=self.FATE_TEXT)
            y += 10
        y += 4
        relation_height = self._draw_relation_note(draw, padding, y, width - padding, relation, "关系状态")
        y += relation_height + 18
        self._draw_game_toolbar(
            draw,
            padding,
            y,
            width - padding,
            interaction_remaining=5,
            available_actions=toolbar_actions,
        )
        return self._save(image, "today_wife_draw")

    def render_today_wife_interaction(
        self,
        event: Mapping[str, Any],
        avatar_paths: Mapping[int, Path],
        *,
        available_actions: Any | None = None,
    ) -> Path:
        width, padding = 1020, 56
        narrative = str(event.get("narrative") or "")
        body_font = self._font(24)
        effects = tuple(effect for effect in event.get("effects", ()) if isinstance(effect, Mapping))
        event_names = self._event_names(effects, str(event.get("actor_nickname") or ""))
        plan = event.get("narrative_plan") if isinstance(event.get("narrative_plan"), Mapping) else {}
        blocks = tuple(item for item in plan.get("blocks", ()) if isinstance(item, Mapping))
        story_items = tuple(
            (str(item.get("label") or "").strip(), str(item.get("text") or "").strip())
            for item in blocks
            if str(item.get("text") or "").strip()
        )
        if not story_items:
            story_items = tuple(("", paragraph) for paragraph in self._fate_paragraphs(narrative))
        body_width = width - padding * 2 - 44
        label_font = self._font(17, True)
        story_content_height = 55
        for index, (label, paragraph) in enumerate(story_items):
            if label:
                story_content_height += self._text_block_height(label, label_font, body_width) + 6
            story_content_height += self._fate_text_height(paragraph, body_font, body_width)
            if index < len(story_items) - 1:
                story_content_height += 18
        story_height = max(154, story_content_height + 24)
        columns = 2 if len(effects) > 1 else 1
        effect_outer_inset = 16
        effect_gap = 24
        effect_width = (width - padding * 2 - effect_outer_inset * 2 - effect_gap * (columns - 1)) // columns
        effect_heights = [self._interaction_effect_height(effect, effect_width) for effect in effects] or [90]
        effect_height = sum(max(effect_heights[index:index + columns]) for index in range(0, len(effect_heights), columns)) + 16 * max(0, (len(effect_heights) - 1) // columns) + 32
        toolbar_actions = available_actions if available_actions is not None else event.get("available_actions")
        toolbar_height = self._game_toolbar_height(event.get("actor_remaining"), toolbar_actions)
        height = 144 + story_height + 24 + effect_height + 30 + toolbar_height + 34
        image, draw = self._new_fate_canvas(width, height)
        self._draw_fate_masthead(
            image,
            draw,
            "今日",
            str(event.get("title") or "互动"),
            seal="SCENE",
            left=padding,
        )
        state = event.get("day_state") if isinstance(event.get("day_state"), Mapping) else {}
        self._draw_game_act(draw, padding, 98, state, width - padding * 2)
        y = 144
        self._paste_fate_panel_gradient(
            image,
            (padding, y, width - padding, y + story_height),
            tone="#eee7f8",
            radius=22,
        )
        draw.text((padding + 22, y + 18), "本次剧情", font=self._font(18, True), fill=self.FATE_ACCENT)
        text_y = y + 55
        for index, (label, paragraph) in enumerate(story_items):
            if label:
                text_y = self._draw_wrapped(draw, padding + 22, text_y, label, label_font, body_width, self.FATE_ACCENT)
                text_y += 6
            text_y = self._draw_fate_wrapped(draw, padding + 22, text_y, paragraph, body_font, body_width, event_names, text_color=self.FATE_TEXT)
            if index < len(story_items) - 1:
                text_y += 18
        y += story_height + 24
        draw.text((padding, y), "关系影响", font=self._font(20, True), fill=self.FATE_ACCENT)
        y += 34
        self._paste_fate_panel_gradient(
            image,
            (padding, y, width - padding, y + effect_height),
            tone="#e5f6f2",
            radius=22,
        )
        if not effects:
            draw.text((padding + 22, y + 32), "这次相遇留下了同场印记，暂未改变任何主缘分。", font=self._font(19), fill=self.FATE_MUTED)
        for index, effect in enumerate(effects):
            row = index // columns
            column = index % columns
            row_top = y + 16 + sum(max(effect_heights[offset:offset + columns]) + 16 for offset in range(0, row * columns, columns))
            left = padding + effect_outer_inset + column * (effect_width + effect_gap)
            self._draw_interaction_effect(draw, left, row_top, effect_width, effect_heights[index], effect)
        self._draw_game_toolbar(
            draw,
            padding,
            height - toolbar_height - 30,
            width - padding,
            interaction_remaining=event.get("actor_remaining"),
            available_actions=toolbar_actions,
        )
        return self._save(image, "today_wife_interaction")

    def render_today_wife_action_prompt(self, prompt: Mapping[str, Any]) -> Path:
        """Render a no-side-effect action chooser returned by the game service."""

        width, padding = 840, 52
        body_width = width - padding * 2 - 44
        state = prompt.get("day_state") if isinstance(prompt.get("day_state"), Mapping) else {}
        hook = prompt.get("current_hook")
        if isinstance(hook, Mapping):
            hook_text = str(hook.get("summary") or hook.get("question") or hook.get("text") or "").strip()
        else:
            hook_text = str(hook or "").strip()
        message = str(prompt.get("message") or "选择一个动作，让这一幕继续往前。 ").strip()
        story_items = [("当前线索", hook_text)] if hook_text else []
        story_items.append(("下一步", message))
        action_options = tuple(
            item for item in prompt.get("action_options", ()) if isinstance(item, Mapping)
        )
        for option in action_options:
            label = str(option.get("label") or option.get("intent") or "可选行动").strip()
            command = str(option.get("display_command") or option.get("command") or "").strip()
            risk_hint = str(option.get("risk_hint") or "").strip()
            target_hint = (
                "请 @ 对应群友。"
                if bool(option.get("requires_mention")) and "+ @" not in command
                else ""
            )
            detail = " ".join(item for item in (command, risk_hint, target_hint) if item)
            if detail:
                story_items.append((f"选择｜{label}", detail))
        label_font = self._font(17, True)
        body_font = self._font(23)
        content_height = 55
        for index, (label, paragraph) in enumerate(story_items):
            content_height += self._text_block_height(label, label_font, body_width) + 6
            content_height += self._fate_text_height(paragraph, body_font, body_width)
            if index < len(story_items) - 1:
                content_height += 18
        card_height = max(154, content_height + 24)
        toolbar_actions = prompt.get("action_options") or prompt.get("available_actions")
        toolbar_height = self._game_toolbar_height(prompt.get("actor_remaining"), toolbar_actions)
        height = 144 + card_height + 30 + toolbar_height + 34
        image, draw = self._new_fate_canvas(width, height)
        self._draw_fate_masthead(image, draw, "今日", "互动选择", seal="CHOICE", left=padding)
        self._draw_game_act(draw, padding, 96, state, width - padding * 2)
        y = 144
        self._paste_fate_panel_gradient(
            image,
            (padding, y, width - padding, y + card_height),
            tone="#e8ddf7",
            radius=22,
        )
        draw.text((padding + 22, y + 18), "此刻可以做什么", font=self._font(18, True), fill=self.FATE_ACCENT)
        text_y = y + 55
        for index, (label, paragraph) in enumerate(story_items):
            text_y = self._draw_wrapped(draw, padding + 22, text_y, label, label_font, body_width, self.FATE_ACCENT)
            text_y += 6
            text_y = self._draw_wrapped(draw, padding + 22, text_y, paragraph, body_font, body_width, self.FATE_TEXT)
            if index < len(story_items) - 1:
                text_y += 18
        self._draw_game_toolbar(
            draw,
            padding,
            height - toolbar_height - 20,
            width - padding,
            interaction_remaining=prompt.get("actor_remaining"),
            available_actions=toolbar_actions,
        )
        return self._save(image, "today_wife_action_prompt")

    def render_today_wife_archive(
        self,
        archive: Mapping[str, Any],
        avatar_paths: Mapping[int, Path],
        *,
        available_actions: Any | None = None,
    ) -> Path:
        width, padding = 880, 52
        own = tuple(item for item in archive.get("own", ()) if isinstance(item, Mapping))
        incoming = tuple(item for item in archive.get("incoming", ()) if isinstance(item, Mapping))
        events = tuple(item for item in archive.get("events", ()) if isinstance(item, Mapping))
        own_height = 82 if not own else len(own) * 122
        incoming_height = 0 if not incoming else 34 + min(3, len(incoming)) * 94
        events_height = 0 if not events else 34 + min(4, len(events)) * 24
        toolbar_actions = available_actions if available_actions is not None else archive.get("available_actions")
        toolbar_height = self._game_toolbar_height(archive.get("remaining"), toolbar_actions)
        height = 166 + own_height + incoming_height + events_height + toolbar_height + 44
        image, draw = self._new_fate_canvas(width, height)
        self._draw_fate_masthead(image, draw, "我的", "缘分", seal="ARCHIVE", left=padding)
        state = archive.get("day_state") if isinstance(archive.get("day_state"), Mapping) else {}
        self._draw_game_act(draw, padding, 94, state, width - padding * 2)
        y = 132
        draw.text((padding, y), "今日主线", font=self._font(21, True), fill=self.FATE_ACCENT)
        y += 34
        if not own:
            draw.text((padding, y + 18), "今天还没有属于你的缘分档案。", font=self._font(22), fill=self.FATE_MUTED)
            y += 82
        for row in own:
            relation = row.get("relation") if isinstance(row.get("relation"), Mapping) else {}
            self._draw_archive_relation(draw, image, padding, y, width - padding, row, relation, avatar_paths)
            y += 122
        if incoming:
            draw.text((padding, y + 4), f"有人把你写进故事 · {len(incoming)} 段", font=self._font(21, True), fill=self.FATE_ACCENT)
            y += 34
            for row in incoming[:3]:
                relation = row.get("relation") if isinstance(row.get("relation"), Mapping) else {}
                self._draw_incoming_relation(draw, padding, y, width - padding, row, relation)
                y += 94
        if events:
            draw.text((padding, y + 4), "今天留下的镜头", font=self._font(21, True), fill=self.FATE_ACCENT)
            y += 34
            for event in events[:4]:
                draw.text((padding + 8, y), self._ellipsize(str(event.get("title") or "今日互动"), self._font(18, True), width - padding * 2 - 16), font=self._font(18, True), fill=self.FATE_TEXT)
                y += 24
        self._draw_game_toolbar(
            draw,
            padding,
            height - toolbar_height - 20,
            width - padding,
            interaction_remaining=archive.get("remaining"),
            available_actions=toolbar_actions,
        )
        return self._save(image, "today_wife_archive")

    def render_today_wife_history_archive(
        self, history: Mapping[str, Any], avatar_paths: Mapping[int, Path]
    ) -> Path:
        rows = tuple(item for item in history.get("rows", ()) if isinstance(item, Mapping))
        width, padding, row_height = 880, 52, 114
        height = 162 + max(1, len(rows)) * (row_height + 12) + 46
        image, draw = self._new_fate_canvas(width, height)
        self._draw_fate_masthead(image, draw, "个人", "永久留档", seal="MEMORY", left=padding)
        page_text = f"第 {int(history.get('page') or 1)}/{int(history.get('pages') or 1)} 页 · 共 {int(history.get('total') or 0)} 段"
        draw.text((padding, 94), page_text, font=self._font(18), fill=self.FATE_MUTED)
        y = 132
        if not rows:
            draw.text((padding, y + 24), "还没有可以留档的缘分。", font=self._font(22), fill=self.FATE_MUTED)
        for row in rows:
            relation = row.get("relation") if isinstance(row.get("relation"), Mapping) else {}
            target = str(row.get("target_nickname") or "这位群友")
            self._paste_fate_panel_gradient(
                image,
                (padding, y, width - padding, y + row_height),
                tone="#eef9f7" if relation.get("frozen_affection") is None else "#f3f0f7",
            )
            self._draw_fate_avatar_at(image, draw, padding + 16, y + 16, 76, avatar_paths.get(int(row.get("target_id") or 0)), target)
            draw.text((padding + 110, y + 16), f"{str(row.get('day') or '')} · {target}", font=self._font(21, True), fill=self.FATE_TEXT)
            state = "已离婚 · 曾经好感" if relation.get("frozen_affection") is not None else "结缘时好感"
            score = int(relation.get("frozen_affection") if relation.get("frozen_affection") is not None else relation.get("affection") or 0)
            draw.text((padding + 110, y + 48), f"{state} {score}", font=self._font(17, True), fill=self.FATE_ACCENT)
            note = " · ".join(str(mark) for mark in relation.get("marks", ())[:3]) or str(row.get("relationship_key") or "今日同行者")
            draw.text((padding + 110, y + 76), self._ellipsize(note, self._font(16), width - padding * 2 - 132), font=self._font(16), fill=self.FATE_MUTED)
            y += row_height + 12
        return self._save(image, "today_wife_history_archive")

    def render_today_wife_group_archive(self, archive: Mapping[str, Any]) -> Path:
        summaries = tuple(item for item in archive.get("summaries", ()) if isinstance(item, Mapping))
        width, padding, row_height = 880, 52, 82
        height = 164 + max(1, len(summaries)) * (row_height + 10) + 40
        image, draw = self._new_fate_canvas(width, height)
        self._draw_fate_masthead(image, draw, "群缘分", "往日摘要", seal="REPLAY", left=padding)
        retention = int(archive.get("detail_retention_days") or 7)
        draw.text((padding, 94), f"最近 {retention} 天可查看完整群像；更早日期保留公开摘要。", font=self._font(18), fill=self.FATE_MUTED)
        y = 132
        if not summaries:
            draw.text((padding, y + 22), "这个群还没有落幕的缘分篇章。", font=self._font(22), fill=self.FATE_MUTED)
        for item in summaries:
            self._paste_fate_panel_gradient(
                image,
                (padding, y, width - padding, y + row_height),
                tone="#eee7f8" if item.get("full_detail") else "#edf8f6",
            )
            draw.text((padding + 18, y + 14), f"{str(item.get('day') or '')} · 《{str(item.get('title') or '留档摘要')}》", font=self._font(19, True), fill=self.FATE_TEXT)
            detail = "完整群像可查看" if item.get("full_detail") else "公开摘要"
            detail_color = self.FATE_ACCENT if item.get("full_detail") else self.FATE_MUTED
            draw.text((width - padding - self._text_width(detail, self._font(16, True)) - 18, y + 18), detail, font=self._font(16, True), fill=detail_color)
            stats = f"{int(item.get('relations') or 0)} 段缘分 · {int(item.get('interactions') or 0)} 次互动 · {int(item.get('divorces') or 0)} 次离婚"
            draw.text((padding + 18, y + 47), stats, font=self._font(16), fill=self.FATE_MUTED)
            y += row_height + 10
        return self._save(image, "today_wife_group_archive")

    def render_today_wife_conclusion(self, conclusion: Mapping[str, Any]) -> Path:
        width, padding = 1180, 58
        sections = tuple(item for item in conclusion.get("sections", ()) if isinstance(item, Mapping))
        column_gap = 24
        card_width = (width - padding * 2 - column_gap) // 2
        card_heights = [self._conclusion_section_height(item, card_width) for item in sections]
        row_heights = [max(card_heights[index:index + 2]) for index in range(0, len(card_heights), 2)]
        ending_font = self._font(24)
        ending_height = self._text_block_height(str(conclusion.get("ending") or "今天的故事在这里合上。"), ending_font, width - padding * 2 - 44)
        stats = conclusion.get("stats") if isinstance(conclusion.get("stats"), Mapping) else {}
        stat_text = f"{int(stats.get('participants') or 0)} 人入场 · {int(stats.get('interactions') or 0)} 次互动 · {int(stats.get('assists') or 0)} 次助攻 · {int(stats.get('responses') or 0)} 次回应 · {int(stats.get('misunderstandings') or 0)} 次误会"
        stats_height = self._text_block_height(stat_text, self._font(17), width - padding * 2 - 32)
        height = 178 + max(76, ending_height + 38) + sum(row_heights) + 18 * max(0, len(row_heights) - 1) + stats_height + 104
        image, draw = self._new_fate_canvas(width, height)
        self._draw_fate_masthead(image, draw, "今日", "终章", seal="FINALE", left=padding)
        draw.text((padding, 97), f"《{str(conclusion.get('title') or '今日篇章')}》", font=self._font(25, True), fill=self.FATE_ACCENT)
        y = 140
        intro_height = max(76, ending_height + 38)
        self._paste_fate_panel_gradient(
            image,
            (padding, y, width - padding, y + intro_height),
            tone="#eee7f8",
            radius=22,
        )
        self._draw_wrapped(draw, padding + 22, y + 18, str(conclusion.get("ending") or "今天的故事在这里合上。"), ending_font, width - padding * 2 - 44, self.FATE_TEXT)
        y += intro_height + 22
        for row, row_height in enumerate(row_heights):
            for column in range(2):
                index = row * 2 + column
                if index >= len(sections):
                    break
                left = padding + column * (card_width + column_gap)
                self._draw_conclusion_section(draw, left, y, card_width, row_height, sections[index])
            y += row_height + 18
        self._paste_fate_panel_gradient(
            image,
            (padding, y, width - padding, y + stats_height + 34),
            tone="#e5f6f2",
        )
        self._draw_wrapped(draw, padding + 16, y + 15, stat_text, self._font(17), width - padding * 2 - 32, self.FATE_MUTED)
        return self._save(image, "today_wife_conclusion")

    def render_today_wife_collective_round(self, payload: Mapping[str, Any]) -> Path:
        """Render one long editorial recap with representative events first."""

        width, padding = 1180, 58
        events = tuple(item for item in payload.get("events", ()) if isinstance(item, Mapping))
        representative_count = min(3, len(events))
        card_width = width - padding * 2
        event_heights = [self._collective_event_height(event, card_width) for event in events]
        conclusion = payload.get("conclusion") if isinstance(payload.get("conclusion"), Mapping) else {}
        ending = str(conclusion.get("ending") or "").strip()
        ending_height = (
            max(112, 64 + self._text_block_height(ending, self._font(22), card_width - 44))
            if ending
            else 0
        )
        section_headers = 42 + (42 if len(events) > representative_count else 0)
        toolbar_height = self._game_toolbar_height(None)
        height = (
            164
            + section_headers
            + sum(event_heights)
            + 16 * max(0, len(events) - 1)
            + (ending_height + 28 if ending else 0)
            + toolbar_height
            + 58
        )
        image, draw = self._new_fate_canvas(width, height)
        round_no = int(payload.get("round_no") or 1)
        self._draw_fate_masthead(
            image,
            draw,
            "今日",
            str(payload.get("title") or "集体互动"),
            seal=f"ROUND {round_no}",
            left=padding,
        )
        state = payload.get("day_state") if isinstance(payload.get("day_state"), Mapping) else {}
        self._draw_game_act(draw, padding, 98, state, card_width)
        draw.text(
            (padding, 126),
            f"第 {round_no} 轮 · {int(payload.get('participant_count') or 0)} 位参与者完成自动演算",
            font=self._font(18, True),
            fill=self.FATE_MUTED,
        )
        y = 164
        draw.text((padding, y), "代表镜头", font=self._font(22, True), fill=self.FATE_ACCENT)
        y += 42
        core_event_id = int(payload.get("core_event_id") or 0)
        for index, (event, event_height) in enumerate(zip(events, event_heights, strict=True)):
            if index == representative_count and len(events) > representative_count:
                y += 2
                draw.text((padding, y), "其余互动", font=self._font(22, True), fill=self.FATE_ACCENT)
                y += 42
            self._draw_collective_event(
                draw,
                padding,
                y,
                card_width,
                event_height,
                event,
                core=int(event.get("event_id") or 0) == core_event_id,
            )
            y += event_height + 16
        if ending:
            y += 6
            self._paste_fate_panel_gradient(
                image,
                (padding, y, width - padding, y + ending_height),
                tone="#eee7f8",
                radius=22,
            )
            draw.text((padding + 22, y + 16), "收官", font=self._font(19, True), fill=self.FATE_ACCENT)
            self._draw_wrapped(
                draw,
                padding + 22,
                y + 44,
                ending,
                self._font(22),
                card_width - 44,
                self.FATE_TEXT,
            )
            y += ending_height + 22
        self._draw_game_toolbar(draw, padding, height - toolbar_height - 24, width - padding, None)
        return self._save(image, "today_wife_collective_round")

    def _collective_event_height(self, event: Mapping[str, Any], width: int) -> int:
        narrative = str(event.get("narrative") or "今天留下了一段新的互动。")
        narrative_height = self._text_block_height(narrative, self._font(21), width - 44)
        effects = tuple(item for item in event.get("effects", ()) if isinstance(item, Mapping))
        effect_height = sum(
            self._text_block_height(
                f"{effect.get('left')} → {effect.get('right')}  {int(effect.get('delta') or 0):+d} · {effect.get('mark') or '关系发生变化'}",
                self._font(17, True),
                width - 44,
            ) + 7
            for effect in effects
        )
        return max(154, 78 + narrative_height + 16 + effect_height + 20)

    def _draw_collective_event(
        self,
        draw: ImageDraw.ImageDraw,
        left: int,
        top: int,
        width: int,
        height: int,
        event: Mapping[str, Any],
        *,
        core: bool,
    ) -> None:
        draw.rounded_rectangle(
            (left, top, left + width, top + height),
            radius=20,
            fill=self.FATE_PANEL if core else self.FATE_PANEL_ALT,
        )
        draw.rounded_rectangle(
            (left, top + 18, left + 7, top + height - 18),
            radius=3,
            fill=self.FATE_PINK if core else self.FATE_MINT,
        )
        label = "核心事件" if core else str(event.get("role") or "互动事件")
        draw.text((left + 22, top + 16), label, font=self._font(17, True), fill=self.FATE_ACCENT)
        actor = str(event.get("actor_nickname") or "一位群友")
        title = str(event.get("title") or "关系发生了新的变化")
        y = self._draw_wrapped(draw, left + 22, top + 45, f"{actor} · {title}", self._font(23, True), width - 44, self.FATE_TEXT)
        y = self._draw_wrapped(draw, left + 22, y + 10, str(event.get("narrative") or ""), self._font(21), width - 44, self.FATE_MUTED)
        for effect in (item for item in event.get("effects", ()) if isinstance(item, Mapping)):
            delta = int(effect.get("delta") or 0)
            color = self.FATE_ACCENT if delta >= 0 else "#b45f70"
            value = f"{effect.get('left')} → {effect.get('right')}  {delta:+d} · {effect.get('mark') or '关系发生变化'}"
            y = self._draw_wrapped(draw, left + 22, y + 7, value, self._font(17, True), width - 44, color)

    def _draw_game_act(self, draw: ImageDraw.ImageDraw, x: int, y: int, state: Mapping[str, Any], width: int) -> None:
        title = str(state.get("theme_title") or "今日篇章")
        act = str(state.get("act_title") or "第一幕｜故事刚刚开始")
        value = self._ellipsize(f"《{title}》 · {act}", self._font(18, True), width)
        draw.text((x, y), value, font=self._font(18, True), fill=self.FATE_ACCENT)

    def _relation_note_height(self, relation: Mapping[str, Any], width: int) -> int:
        arc = relation.get("narrative") if isinstance(relation.get("narrative"), Mapping) else {}
        hook = arc.get("hook") if isinstance(arc.get("hook"), Mapping) else {}
        hook_text = str(hook.get("summary") or "").strip()
        if not hook_text:
            return 76
        return max(102, 50 + self._text_block_height(hook_text, self._font(17), width - 36))

    def _draw_relation_note(self, draw: ImageDraw.ImageDraw, left: int, y: int, right: int, relation: Mapping[str, Any], label: str) -> int:
        height = self._relation_note_height(relation, right - left)
        draw.rounded_rectangle((left, y, right, y + height), radius=18, fill=self.FATE_PANEL)
        draw.rounded_rectangle((left, y + 14, left + 6, y + height - 14), radius=3, fill=self.FATE_MINT)
        draw.text((left + 18, y + 13), label, font=self._font(17, True), fill=self.FATE_MUTED)
        marks = " · ".join(str(item) for item in relation.get("marks", ())[:3]) or "尚未留下共同印记"
        draw.text((left + 18, y + 40), self._ellipsize(marks, self._font(19), right - left - 36), font=self._font(19), fill=self.FATE_TEXT)
        arc = relation.get("narrative") if isinstance(relation.get("narrative"), Mapping) else {}
        hook = arc.get("hook") if isinstance(arc.get("hook"), Mapping) else {}
        hook_text = str(hook.get("summary") or "").strip()
        if hook_text:
            self._draw_wrapped(draw, left + 18, y + 68, f"当前线索：{hook_text}", self._font(17), right - left - 36, self.FATE_MUTED)
        return height

    @staticmethod
    def _fate_paragraphs(value: str) -> tuple[str, ...]:
        """Split composed narration into readable beats, preserving author breaks."""
        paragraphs: list[str] = []
        for source in (item.strip() for item in value.splitlines() if item.strip()):
            sentence = ""
            for character in source:
                sentence += character
                if character in "。！？!?" and len(sentence) >= 18:
                    paragraphs.append(sentence)
                    sentence = ""
            if sentence:
                paragraphs.append(sentence)
        return tuple(paragraphs) or ("今天的故事在这里留下了一点新的变化。",)

    def _interaction_effect_height(self, effect: Mapping[str, Any], width: int) -> int:
        relation = f"{str(effect.get('left') or '这位群友')} → {str(effect.get('right') or '这位群友')}"
        # Reserve the score column while measuring the relation line.  The
        # previous wider measurement could undercount wraps for long names.
        name_height = self._text_block_height(relation, self._font(21, True), width - 128)
        mark_height = self._text_block_height(str(effect.get("mark") or "留下印记"), self._font(17), width - 48)
        return max(104, 32 + name_height + 8 + mark_height + 22)

    def _draw_interaction_effect(
        self,
        draw: ImageDraw.ImageDraw,
        left: int,
        top: int,
        width: int,
        height: int,
        effect: Mapping[str, Any],
    ) -> None:
        delta = int(effect.get("delta") or 0)
        score_color = self.FATE_ACCENT if delta > 0 else "#8593a3" if delta == 0 else "#bd6d7a"
        draw.rounded_rectangle((left, top, left + width, top + height), radius=16, fill=self.FATE_PANEL_ALT)
        relation = f"{str(effect.get('left') or '这位群友')} → {str(effect.get('right') or '这位群友')}"
        relation_width = width - 128
        relation_bottom = self._draw_wrapped(draw, left + 16, top + 16, relation, self._font(21, True), relation_width, self.FATE_TEXT)
        self._draw_wrapped(draw, left + 16, relation_bottom + 6, str(effect.get("mark") or "留下印记"), self._font(17), width - 32, self.FATE_MUTED)
        score = f"{delta:+d}" if delta else "+0"
        self._draw_centered(draw, left + width - 48, top + height // 2, score, self._font(27, True), score_color)

    def _conclusion_section_height(self, section: Mapping[str, Any], width: int) -> int:
        body_width = width - 40
        if section.get("left"):
            relation = f"{section.get('left')} → {section.get('right')}"
            relation_height = self._text_block_height(relation, self._font(23, True), body_width)
            story_height = self._text_block_height(str(section.get("story") or ""), self._font(18), body_width)
            marks = " · ".join(str(item) for item in section.get("marks", ())[:3])
            marks_height = self._text_block_height(marks, self._font(16), body_width) if marks else 0
            return max(202, 30 + relation_height + 12 + self._line_height(self._font(18)) + 12 + story_height + 10 + marks_height + 24)
        actor_height = self._text_block_height(str(section.get("actor") or "一位群友"), self._font(22, True), body_width)
        story_height = self._text_block_height(str(section.get("story") or ""), self._font(18), body_width)
        effects = tuple(item for item in section.get("effects", ()) if isinstance(item, Mapping))
        effect_height = sum(self._text_block_height(f"{item.get('left')} → {item.get('right')}  {int(item.get('delta') or 0):+d}", self._font(16, True), body_width) + 6 for item in effects)
        return max(194, 32 + actor_height + 12 + story_height + 14 + effect_height + 24)

    def _draw_conclusion_section(
        self,
        draw: ImageDraw.ImageDraw,
        left: int,
        top: int,
        width: int,
        height: int,
        section: Mapping[str, Any],
    ) -> None:
        body_width = width - 40
        draw.rounded_rectangle((left, top, left + width, top + height), radius=20, fill=self.FATE_PANEL)
        draw.rounded_rectangle((left, top + 16, left + 6, top + height - 16), radius=3, fill=self.FATE_PURPLE)
        draw.text((left + 20, top + 16), str(section.get("kind") or "今日镜头"), font=self._font(19, True), fill=self.FATE_ACCENT)
        y = top + 49
        if section.get("left"):
            relation = f"{section.get('left')} → {section.get('right')}"
            y = self._draw_wrapped(draw, left + 20, y, relation, self._font(23, True), body_width, self.FATE_TEXT)
            score = f"好感 {int(section.get('minimum') or 0):+d} → {int(section.get('affection') or 0):+d}"
            y = self._draw_wrapped(draw, left + 20, y + 8, score, self._font(18), body_width, self.FATE_MUTED)
            story = str(section.get("story") or "")
            if story:
                y = self._draw_wrapped(draw, left + 20, y + 10, story, self._font(18), body_width, self.FATE_MUTED)
            marks = " · ".join(str(item) for item in section.get("marks", ())[:3])
            if marks:
                self._draw_wrapped(draw, left + 20, y + 8, marks, self._font(16), body_width, self.FATE_CORAL)
            return
        y = self._draw_wrapped(draw, left + 20, y, str(section.get("actor") or "一位群友"), self._font(22, True), body_width, self.FATE_TEXT)
        y = self._draw_wrapped(draw, left + 20, y + 10, str(section.get("story") or ""), self._font(18), body_width, self.FATE_MUTED)
        for effect in (item for item in section.get("effects", ()) if isinstance(item, Mapping)):
            delta = int(effect.get("delta") or 0)
            color = self.FATE_ACCENT if delta >= 0 else "#bd6d7a"
            effect_text = f"{effect.get('left')} → {effect.get('right')}  {delta:+d}"
            y = self._draw_wrapped(draw, left + 20, y + 7, effect_text, self._font(16, True), body_width, color)

    @classmethod
    def _game_toolbar_actions(cls, available_actions: Any | None) -> tuple[str, ...]:
        return ("#我的缘分", "#群缘分", "#离婚")

    @classmethod
    def _game_toolbar_columns(cls, available_actions: Any | None) -> int:
        return 3

    @classmethod
    def _game_toolbar_height(cls, interaction_remaining: Any, available_actions: Any | None = None) -> int:
        labels = cls._game_toolbar_actions(available_actions)
        columns = cls._game_toolbar_columns(labels)
        return 64

    def _draw_game_toolbar(
        self,
        draw: ImageDraw.ImageDraw,
        left: int,
        y: int,
        right: int,
        interaction_remaining: Any,
        available_actions: Any | None = None,
    ) -> None:
        labels = self._game_toolbar_actions(available_actions)
        columns = self._game_toolbar_columns(labels)
        height = self._game_toolbar_height(interaction_remaining, labels)
        draw.rounded_rectangle((left, y, right, y + height), radius=20, fill="#ffffff")
        chip_top = y + 13
        chip_width = (right - left - 36 - 8 * (columns - 1)) // columns
        for index, label in enumerate(labels):
            row, column = divmod(index, columns)
            chip_left = left + 18 + column * (chip_width + 8)
            chip_y = chip_top + row * 44
            fills = ("#ffe1ea", "#eee7f8", "#def5f1")
            text_colors = (self.FATE_CORAL, self.FATE_PURPLE, "#308c80")
            draw.rounded_rectangle(
                (chip_left, chip_y, chip_left + chip_width, chip_y + 38),
                radius=19,
                fill=fills[index % len(fills)],
            )
            font = self._font(16, True)
            display_label = self._ellipsize(label, font, chip_width - 18)
            self._draw_centered(
                draw,
                chip_left + chip_width // 2,
                chip_y + 19,
                display_label,
                font,
                text_colors[index % len(text_colors)],
            )

    def _draw_archive_relation(self, draw: ImageDraw.ImageDraw, image: Image.Image, left: int, y: int, right: int, row: Mapping[str, Any], relation: Mapping[str, Any], avatar_paths: Mapping[int, Path]) -> None:
        self._paste_fate_panel_gradient(
            image,
            (left, y, right, y + 108),
            tone="#eef9f7" if relation.get("frozen_affection") is None else "#f3f0f7",
        )
        target_id = int(row.get("target_id") or 0)
        target = str(row.get("target_nickname") or "这位群友")
        self._draw_fate_avatar_at(image, draw, left + 16, y + 16, 72, avatar_paths.get(target_id), target)
        draw.text((left + 104, y + 16), f"今日老婆：{target}", font=self._font(22, True), fill=self.FATE_TEXT)
        status = "已离婚 · 曾经好感" if relation.get("frozen_affection") is not None else "今日好感"
        score = int(relation.get("frozen_affection") if relation.get("frozen_affection") is not None else relation.get("affection") or 0)
        draw.text((left + 104, y + 49), f"{status} {score}", font=self._font(18, True), fill=self.FATE_ACCENT)
        marks = " · ".join(str(item) for item in relation.get("marks", ())[:3]) or "尚未留下共同印记"
        draw.text((left + 104, y + 77), self._ellipsize(marks, self._font(16), right - left - 122), font=self._font(16), fill=self.FATE_MUTED)

    def _draw_incoming_relation(self, draw: ImageDraw.ImageDraw, left: int, y: int, right: int, row: Mapping[str, Any], relation: Mapping[str, Any]) -> None:
        draw.rounded_rectangle((left, y, right, y + 78), radius=18, fill=self.FATE_PANEL_ALT)
        name = str(row.get("actor_nickname") or "这位群友")
        state = "你已回应" if int(relation.get("response_count") or 0) else "等待回应"
        draw.text((left + 18, y + 14), f"{name} → 你", font=self._font(20, True), fill=self.FATE_TEXT)
        draw.text((left + 18, y + 43), f"{state} · 当前好感 {int(relation.get('affection') or 0)}", font=self._font(17), fill=self.FATE_ACCENT)

    @staticmethod
    def _event_names(effects: tuple[Mapping[str, Any], ...], actor: str) -> tuple[str, ...]:
        names = {actor}
        for effect in effects:
            names.update((str(effect.get("left") or ""), str(effect.get("right") or "")))
        return tuple(name for name in names if name)

    def render_group_today_wife(
        self,
        rows: list[Mapping[str, Any]],
        avatar_paths: Mapping[int, Path],
        day: str,
        episode: Mapping[str, str],
        spotlight: str,
    ) -> Path:
        if not str(episode.get("title") or "").strip():
            raise ValueError("group today-wife cards require a daily episode")
        if not spotlight.strip():
            raise ValueError("group today-wife cards require a randomized spotlight")
        nodes: dict[int, str] = {}
        edges: list[tuple[int, int]] = []
        for row in rows:
            actor_id, target_id = int(row.get("actor_id") or 0), int(row.get("target_id") or 0)
            if actor_id <= 0 or target_id <= 0:
                continue
            nodes.setdefault(actor_id, str(row.get("actor_nickname") or "这位群友"))
            nodes.setdefault(target_id, str(row.get("target_nickname") or "这位群友"))
            edges.append((actor_id, target_id))
        if not nodes:
            image, draw = self._new_fate_canvas(960, 540)
            self._draw_fate_masthead(image, draw, "今日", "群缘分", seal="GROUP")
            draw.text((52, 96), f"{day} · 今日篇章《{episode['title']}》", font=self._font(20), fill=self.FATE_ACCENT)
            self._draw_wrapped(draw, 52, 148, spotlight, self._font(23), 856, self.FATE_MUTED, max_lines=4)
            return self._save(image, "today_wife_group")

        node_ids = tuple(sorted(nodes))
        avatar_size = 96 if len(node_ids) <= 18 else 76 if len(node_ids) <= 35 else 62 if len(node_ids) <= 48 else 68
        edge_types = self._fate_edge_types(edges)
        visible_edge_types = tuple(key for key in self.FATE_EDGE_STYLES if key in set(edge_types.values()))
        has_divorced_edge = any(str(row.get("status") or "active") == "divorced" for row in rows)
        positions, width, graph_height, component_boxes = self._fate_group_layout(node_ids, edges, avatar_size, nodes)
        spotlight_font = self._font(19)
        spotlight_lines = len(self._wrap_text(spotlight, spotlight_font, max(300, width - 104)))
        legend_y = 120 + spotlight_lines * self._line_height(spotlight_font) + 10
        top = max(228, legend_y + 52)
        positions, component_boxes, width, height = self._fit_fate_aspect(positions, component_boxes, width, top + graph_height + 64)
        image, draw = self._new_fate_canvas(width, height)
        self._draw_fate_masthead(image, draw, "今日", "群缘分", seal="GROUP")
        draw.text((52, 92), f"{day} · 今日篇章《{episode['title']}》", font=self._font(19, True), fill=self.FATE_ACCENT)
        self._draw_fate_wrapped(draw, 52, 120, spotlight, spotlight_font, max(300, width - 104), tuple(nodes.values()), text_color=self.FATE_MUTED)
        self._draw_fate_legend(draw, 52, legend_y, visible_edge_types + (("divorced",) if has_divorced_edge else ()))
        edge_set = set(edges)
        mutual_nodes = {node for left, right in edge_set if (right, left) in edge_set for node in (left, right)}
        incoming: dict[int, int] = {}
        for _actor_id, target_id in edges:
            incoming[target_id] = incoming.get(target_id, 0) + 1
        details: list[dict[str, Any]] = []
        for actor_id, target_id in edges:
            edge_rows = [row for row in rows if int(row.get("actor_id") or 0) == actor_id and int(row.get("target_id") or 0) == target_id]
            divorced = bool(edge_rows) and all(str(row.get("status") or "active") == "divorced" for row in edge_rows)
            relation = edge_rows[0].get("relation") if edge_rows and isinstance(edge_rows[0].get("relation"), Mapping) else {}
            affection = int(relation.get("frozen_affection") if divorced and relation.get("frozen_affection") is not None else relation.get("affection") or 0)
            details.append({"actor_id": actor_id, "target_id": target_id, "curved": (target_id, actor_id) in edge_set, "divorced": divorced, "color": "#aeb5be" if divorced else self.FATE_EDGE_STYLES[edge_types[(actor_id, target_id)]][1], "affection": affection})

        node_boxes = self._fate_node_boxes(positions, nodes, avatar_size, top)
        for detail in details:
            actor_id, target_id = int(detail["actor_id"]), int(detail["target_id"])
            detail["start"] = (positions[actor_id][0], positions[actor_id][1] + top)
            detail["end"] = (positions[target_id][0], positions[target_id][1] + top)
        occupied_labels: list[tuple[int, int, int, int]] = []
        label_order = sorted(
            details,
            key=lambda detail: -math.hypot(
                detail["end"][0] - detail["start"][0], detail["end"][1] - detail["start"][1]
            ),
        )
        for detail in label_order:
            score = ("\u5df2\u79bb\u5a5a " if detail["divorced"] else "\u597d\u611f ") + str(detail["affection"])
            font = self._font(13, True)
            other_paths = [
                (other["start"], other["end"])
                for other in details
                if other is not detail
            ]
            label_box = self._fate_label_position(
                detail["start"],
                detail["end"],
                self._text_width(score, font) + 58,
                28,
                list(node_boxes.values()),
                occupied_labels,
                canvas_width=width,
                canvas_height=height,
                other_paths=other_paths,
            )
            occupied_labels.append(label_box)
            detail["label_box"] = label_box
            detail["label_text"] = score

        # The first pass avoids direct links. Curves can bulge into a nearby
        # label later, so refine against the actual final Bezier geometry.
        for _ in range(3):
            changed = False
            for detail in label_order:
                other_paths = [
                    segment
                    for other in details
                    if other is not detail
                    for segment in zip(
                        self._fate_curve_through_label_points(
                            other["start"], other["end"], other["label_box"]
                        ),
                        self._fate_curve_through_label_points(
                            other["start"], other["end"], other["label_box"]
                        )[1:],
                    )
                ]
                occupied = [other["label_box"] for other in details if other is not detail]
                label_box = self._fate_label_position(
                    detail["start"],
                    detail["end"],
                    detail["label_box"][2] - detail["label_box"][0],
                    detail["label_box"][3] - detail["label_box"][1],
                    list(node_boxes.values()),
                    occupied,
                    canvas_width=width,
                    canvas_height=height,
                    other_paths=other_paths,
                )
                if label_box != detail["label_box"]:
                    detail["label_box"] = label_box
                    changed = True
            if not changed:
                break

        # A label is an ownership anchor, not an annotation floating above a
        # shared tangle of lines. Each edge is one continuous curve through its
        # own anchor. Avatar avoidance is deliberately not part of this route:
        # forcing it created angular, harder-to-read polylines in dense groups.
        all_label_boxes = [detail["label_box"] for detail in details]
        for detail in details:
            detail["path"] = self._fate_path_through_label(
                detail["start"],
                detail["end"],
                detail["label_box"],
                [box for box in all_label_boxes if box != detail["label_box"]]
            )

        # Layer 1: arrows. Each path travels under its affection label.
        for detail in details:
            actor_id, target_id = int(detail["actor_id"]), int(detail["target_id"])
            start, end = detail["start"], detail["end"]
            path = detail["path"]
            self._draw_fate_arrow(
                draw, start, end, avatar_size / 2 + 7,
                curved=True,
                color=str(detail["color"]),
                dashed=bool(detail["divorced"]),
                curve_offset=34,
                waypoints=tuple(path[1:-1]),
            )

        # Layer 2: avatars and names.
        for user_id in node_ids:
            center_x, center_y = positions[user_id]
            left, avatar_top, name = int(center_x - avatar_size / 2), int(center_y + top - avatar_size / 2), nodes[user_id]
            if user_id in mutual_nodes:
                draw.ellipse((left - 5, avatar_top - 5, left + avatar_size + 5, avatar_top + avatar_size + 5), outline="#e85c91", width=4)
            self._draw_fate_avatar_at(image, draw, left, avatar_top, avatar_size, avatar_paths.get(user_id), name)
            if incoming.get(user_id, 0) >= 2:
                badge = f"×{incoming[user_id]}"
                draw.rounded_rectangle((left + avatar_size - 27, avatar_top - 8, left + avatar_size + 20, avatar_top + 24), radius=15, fill="#e85c91")
                self._draw_centered(draw, left + avatar_size - 4, avatar_top + 8, badge, self._font(15, True), "#ffffff")
            label = self._ellipsize(name, self._font(18, True), avatar_size + 40)
            self._draw_centered(draw, int(center_x), avatar_top + avatar_size + 20, label, self._font(18, True), self.FATE_TEXT)

        # Layer 3: affection labels, positioned after all node bounds are known.
        node_boxes = self._fate_node_boxes(positions, nodes, avatar_size, top)
        occupied_labels: list[tuple[int, int, int, int]] = []
        for detail in details:
            actor_id, target_id = int(detail["actor_id"]), int(detail["target_id"])
            text = f"已离婚 · 曾经 {detail['affection']}" if detail["divorced"] else f"好感 {detail['affection']}"
            text = str(detail["label_text"])
            font = self._font(13, True)
            label_box = detail["label_box"]
            left, label_top, right, bottom = label_box
            draw.rounded_rectangle(
                label_box,
                radius=12,
                fill="#f3f1f6" if detail["divorced"] else "#ffffff",
                outline="#c4c9cf" if detail["divorced"] else str(detail["color"]),
                width=1,
            )
            self._draw_centered(draw, (left + right) // 2, (label_top + bottom) // 2, text, font, "#7f8993" if detail["divorced"] else self.FATE_ACCENT)
        return self._save(image, "today_wife_group")

    @staticmethod
    def _fate_gradient_image(
        width: int,
        height: int,
        stops: tuple[tuple[float, str], ...],
        *,
        vertical: bool = False,
    ) -> Image.Image:
        length = max(2, height if vertical else width)
        strip_size = (1, length) if vertical else (length, 1)
        strip = Image.new("RGB", strip_size)
        pixels = strip.load()
        ordered = tuple(sorted(stops, key=lambda item: item[0]))
        segment = 0
        for index in range(length):
            position = index / (length - 1)
            while segment + 1 < len(ordered) - 1 and position > ordered[segment + 1][0]:
                segment += 1
            start_at, start_color = ordered[segment]
            end_at, end_color = ordered[min(segment + 1, len(ordered) - 1)]
            span = max(0.0001, end_at - start_at)
            ratio = min(1.0, max(0.0, (position - start_at) / span))
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

    def _new_fate_canvas(self, width: int, height: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
        image = self._fate_gradient_image(
            width,
            height,
            (
                (0.0, "#fff8fb"),
                (0.38, "#ffffff"),
                (0.68, "#f0fbf9"),
                (1.0, "#f7f3ff"),
            ),
        )
        vertical_tint = self._fate_gradient_image(
            width,
            height,
            ((0.0, "#ffffff"), (1.0, "#f7f5fc")),
            vertical=True,
        )
        image = Image.blend(image, vertical_tint, 0.22)

        glow_scale = 0.25
        glow_width = max(1, round(width * glow_scale))
        glow_height = max(1, round(height * glow_scale))
        glow = Image.new("RGBA", (glow_width, glow_height), (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow)

        def ellipse(box: tuple[float, float, float, float], color: str, alpha: int) -> None:
            scaled = tuple(round(value * glow_scale) for value in box)
            glow_draw.ellipse(scaled, fill=(*ImageColor.getrgb(color), alpha))

        ellipse((width * 0.42, 24, width * 1.08, min(height * 0.34, 360)), self.FATE_PINK, 72)
        ellipse((-width * 0.15, height * 0.35, width * 0.55, height * 0.66), self.FATE_MINT, 55)
        ellipse((width * 0.52, height * 0.64, width * 1.16, height * 1.05), self.FATE_PURPLE, 50)
        ellipse((width * 0.12, height * 0.72, width * 0.48, height * 1.02), self.FATE_YELLOW, 34)
        glow = glow.filter(ImageFilter.GaussianBlur(max(18, round(min(glow_width, glow_height) * 0.12))))
        glow = glow.resize((width, height), Image.Resampling.BICUBIC)
        image = Image.alpha_composite(image.convert("RGBA"), glow).convert("RGB")

        rail_width = max(18, min(26, width // 44))
        rail = self._fate_gradient_image(
            rail_width,
            height,
            (
                (0.0, self.FATE_PURPLE),
                (0.34, self.FATE_PINK),
                (0.67, self.FATE_YELLOW),
                (1.0, self.FATE_MINT),
            ),
            vertical=True,
        )
        image.paste(rail, (0, 0))
        draw = ImageDraw.Draw(image)
        self._draw_fate_rail_label(image, height, rail_width)
        draw = ImageDraw.Draw(image)
        draw.ellipse((width - 72, 20, width - 60, 32), fill=self.FATE_PINK)
        draw.ellipse((width - 52, 20, width - 40, 32), fill=self.FATE_YELLOW)
        draw.ellipse((width - 32, 20, width - 20, 32), fill=self.FATE_MINT)
        return image, draw

    def _draw_fate_rail_label(self, image: Image.Image, height: int, rail_width: int) -> None:
        label_width = max(1, height - 72)
        label = Image.new("RGBA", (label_width, rail_width), (0, 0, 0, 0))
        label_draw = ImageDraw.Draw(label)
        font = self._fate_latin_font(max(8, min(11, rail_width - 10)), bold=True)
        text = "AK-BOT FUNCTION"
        text_width = self._text_width(text, font)
        label_draw.text(
            (max(0, (label_width - text_width) // 2), max(0, (rail_width - self._line_height(font)) // 2 - 1)),
            text,
            font=font,
            fill=(255, 255, 255, 226),
        )
        rotated = label.rotate(90, expand=True, resample=Image.Resampling.BICUBIC)
        image.paste(rotated, (0, 36), rotated)

    def _fate_latin_font(self, size: int, *, bold: bool = False, italic: bool = False) -> ImageFont.ImageFont:
        candidates: list[Path] = []
        if bold and italic:
            candidates.append(Path(r"C:\Windows\Fonts\segoeuiz.ttf"))
        elif italic:
            candidates.append(Path(r"C:\Windows\Fonts\segoeuii.ttf"))
        elif bold:
            candidates.append(Path(r"C:\Windows\Fonts\segoeuib.ttf"))
        candidates.append(Path(r"C:\Windows\Fonts\segoeui.ttf"))
        for candidate in candidates:
            if not candidate.is_file():
                continue
            try:
                return ImageFont.truetype(str(candidate), size=size)
            except OSError:
                continue
        return self._font(size, bold)

    def _draw_fate_masthead(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        primary: str,
        secondary: str,
        *,
        seal: str,
        left: int = 52,
        right: int = 52,
    ) -> None:
        draw.text(
            (left, 24),
            "AK-BOT FUNCTION",
            font=self._fate_latin_font(11, bold=True),
            fill=self.FATE_MUTED,
        )
        primary_font = self._font(39, True)
        primary_box = primary_font.getbbox(primary or "缘")
        primary_width = primary_box[2] - primary_box[0]
        primary_height = primary_box[3] - primary_box[1]
        primary_mask = Image.new("L", (primary_width + 4, primary_height + 4), 0)
        ImageDraw.Draw(primary_mask).text(
            (2 - primary_box[0], 2 - primary_box[1]),
            primary,
            font=primary_font,
            fill=255,
        )
        primary_gradient = self._fate_gradient_image(
            primary_mask.width,
            primary_mask.height,
            ((0.0, self.FATE_PINK), (0.62, self.FATE_PURPLE), (1.0, self.FATE_MINT)),
        )
        image.paste(primary_gradient, (left, 48), primary_mask)

        divider_x = left + primary_width + 18
        draw.rounded_rectangle((divider_x, 56, divider_x + 4, 88), radius=2, fill=self.FATE_MINT)
        secondary_font = self._font(25, True)
        seal_font = self._fate_latin_font(42, bold=True, italic=True)
        seal_text = str(seal or "FATE").upper()
        seal_width = self._text_width(seal_text, seal_font)
        seal_x = image.width - right - seal_width
        available = max(80, seal_x - divider_x - 18)
        display_secondary = self._ellipsize(secondary, secondary_font, available)
        draw.text((divider_x + 15, 58), display_secondary, font=secondary_font, fill=self.FATE_TEXT)
        draw.text((seal_x, 37), seal_text, font=seal_font, fill="#ded8eb")

    def _paste_fate_panel_gradient(
        self,
        image: Image.Image,
        box: tuple[int, int, int, int],
        *,
        tone: str | None = None,
        radius: int = 18,
    ) -> None:
        paste_horizontal_gradient(
            image,
            box,
            "#ffffff",
            tone or "#def4ef",
            radius=radius,
        )

    def _draw_fate_avatar_at(
        self,
        image: Image.Image,
        draw: ImageDraw.ImageDraw,
        left: int,
        top: int,
        size: int,
        avatar_path: Path | None,
        label: str,
    ) -> None:
        ring_box = (left - 4, top - 4, left + size + 3, top + size + 3)
        for start, end, color in (
            (-90, 0, self.FATE_YELLOW),
            (0, 90, self.FATE_PINK),
            (90, 180, self.FATE_PURPLE),
            (180, 270, self.FATE_MINT),
        ):
            draw.arc(ring_box, start=start, end=end, fill=color, width=max(3, size // 32))
        if avatar_path is not None and avatar_path.is_file():
            self._draw_avatar_at(image, draw, left, top, size, avatar_path, label)
            draw.ellipse(
                (left, top, left + size - 1, top + size - 1),
                outline="#ffffff",
                width=max(2, size // 42),
            )
            return
        draw.ellipse(
            (left, top, left + size - 1, top + size - 1),
            fill=self.FATE_SOFT,
            outline=self.FATE_ACCENT,
            width=2,
        )
        self._draw_centered(
            draw,
            left + size // 2,
            top + size // 2,
            label[-2:] or "?",
            self._font(max(14, size // 3), True),
            self.FATE_ACCENT,
        )

    def _draw_fate_wrapped(
        self,
        draw: ImageDraw.ImageDraw,
        x: int,
        y: int,
        value: str,
        font: Any,
        width: int,
        highlight_names: tuple[str, ...],
        text_color: str | None = None,
    ) -> int:
        """Draw one narrative paragraph while giving names a visible accent."""
        names = tuple(sorted({name.strip() for name in highlight_names if name.strip()}, key=len, reverse=True))
        tokens: list[tuple[str, str]] = []
        cursor = 0
        while cursor < len(value):
            name = next((candidate for candidate in names if value.startswith(candidate, cursor)), None)
            if name is not None:
                tokens.extend((character, self.FATE_ACCENT) for character in name)
                cursor += len(name)
            else:
                tokens.append((value[cursor], text_color or self.FATE_TEXT))
                cursor += 1
        line: list[tuple[str, str]] = []
        line_width = 0
        line_height = self._line_height(font)

        def flush() -> None:
            nonlocal y, line, line_width
            cursor_x = x
            for text, fill in line:
                draw.text((cursor_x, y), text, font=font, fill=fill)
                cursor_x += self._text_width(text, font)
            y += line_height
            line = []
            line_width = 0

        for text, fill in tokens:
            if text == "\n":
                flush()
                continue
            text_width = self._text_width(text, font)
            if line and line_width + text_width > width:
                flush()
            line.append((text, fill))
            line_width += text_width
        if line or not tokens:
            flush()
        return y

    def _fate_text_height(self, value: str, font: Any, width: int) -> int:
        if not value:
            return self._line_height(font)
        lines = 1
        line_width = 0
        for character in value:
            if character == "\n":
                lines += 1
                line_width = 0
                continue
            character_width = self._text_width(character, font)
            if line_width and line_width + character_width > width:
                lines += 1
                line_width = 0
            line_width += character_width
        return lines * self._line_height(font)

    def _fate_group_layout(
        self,
        node_ids: tuple[int, ...],
        edges: list[tuple[int, int]],
        avatar_size: int,
        node_names: Mapping[int, str] | None = None,
    ) -> tuple[dict[int, tuple[float, float]], int, int, list[tuple[int, int, int, int]]]:
        components = self._fate_components(node_ids, edges)
        # Today's graph is normally a collection of small, unrelated relation
        # islands. Lay those islands out independently so a two-person thread
        # is never inserted into another cluster and forced into a huge arc.
        if len(node_ids) < 12 or len(components) == 1:
            positions, width, height = self._fate_graph_layout(node_ids, edges, avatar_size, node_names=node_names)
            return positions, width, height, []

        gap = 96
        prepared: list[tuple[dict[int, tuple[float, float]], int, int]] = []
        for component in components:
            component_set = set(component)
            component_edges = [edge for edge in edges if edge[0] in component_set and edge[1] in component_set]
            component_size = len(component)
            block_width = max(250, 150 + component_size * 62 + len(component_edges) * 18)
            block_height = max(190, 116 + component_size * 52 + len(component_edges) * 14)
            local, block_width, block_height = self._fate_graph_layout(
                tuple(component), component_edges, avatar_size,
                minimum_width=block_width, minimum_height=block_height,
                node_names=node_names, component_layout=True,
            )
            prepared.append((local, block_width, block_height))

        total_area = sum((block_width + gap) * (block_height + gap) for _, block_width, block_height in prepared)
        ideal_width = math.sqrt(total_area * self.FATE_ASPECT_RATIO)
        candidate_widths = {
            max(1200, int(ideal_width * (0.70 + step * 0.05))) for step in range(25)
        }

        def pack(target_width: int) -> tuple[list[tuple[int, int]], int, int]:
            """Pack whole relation islands into balanced, justified rows.

            A purely greedy line break lets a short final row leave a large
            lower-right void. Keep the deterministic component ordering, but
            choose row breaks by their unused width, then distribute each row
            across the same usable span. Components remain isolated and retain
            the same local layout; only the empty space between islands moves.
            """
            outer_margin = 52
            available_width = max(1, target_width - outer_margin * 2)
            item_count = len(prepared)
            row_widths = [[0] * (item_count + 1) for _ in range(item_count)]
            for start in range(item_count):
                width = 0
                for end in range(start, item_count):
                    if end > start:
                        width += gap
                    width += prepared[end][1]
                    row_widths[start][end + 1] = width

            # Dynamic programming chooses contiguous rows so the component
            # order remains stable. Squared slack strongly prefers rows of a
            # similar visual width without splitting a component.
            costs = [math.inf] * (item_count + 1)
            breaks = [-1] * (item_count + 1)
            costs[0] = 0.0
            for end in range(1, item_count + 1):
                for start in range(end - 1, -1, -1):
                    width = row_widths[start][end]
                    if width > available_width and start != end - 1:
                        break
                    if math.isinf(costs[start]):
                        continue
                    slack = max(0, available_width - width)
                    cost = costs[start] + slack * slack
                    if cost < costs[end]:
                        costs[end] = cost
                        breaks[end] = start

            row_ranges: list[tuple[int, int]] = []
            cursor = item_count
            while cursor > 0:
                start = breaks[cursor]
                if start < 0:
                    # A defensive fallback for a component wider than every
                    # candidate width. It mirrors the former greedy behavior.
                    start = cursor - 1
                row_ranges.append((start, cursor))
                cursor = start
            row_ranges.reverse()

            used_inner_width = max(row_widths[start][end] for start, end in row_ranges)
            canvas_width = max(1200, used_inner_width + outer_margin * 2)
            usable_width = canvas_width - outer_margin * 2
            placements: list[tuple[int, int]] = []
            cursor_y = 42
            for start, end in row_ranges:
                natural_width = row_widths[start][end]
                row_height = max(block_height for _local, _block_width, block_height in prepared[start:end])
                columns = end - start
                if columns == 1:
                    cursor_x = outer_margin + (usable_width - natural_width) / 2
                    step_gap = 0.0
                else:
                    cursor_x = float(outer_margin)
                    step_gap = gap + (usable_width - natural_width) / (columns - 1)
                for index in range(start, end):
                    _local, block_width, _block_height = prepared[index]
                    placements.append((round(cursor_x), cursor_y))
                    cursor_x += block_width + step_gap
                cursor_y += row_height + gap
            return placements, canvas_width, cursor_y - gap + 48

        packed_options = []
        for candidate_width in sorted(candidate_widths):
            placements, packed_width, packed_height = pack(candidate_width)
            # The eventual card reserves the title, spotlight and legend above
            # the graph, plus its lower breathing room.
            ratio = packed_width / max(1, packed_height + 292)
            score = abs(math.log(ratio / self.FATE_ASPECT_RATIO))
            if ratio > self.FATE_ASPECT_RATIO:
                score += 0.18
            packed_options.append((score, placements, packed_width, packed_height))
        _score, placements, canvas_width, canvas_height = min(packed_options, key=lambda item: item[0])

        positions: dict[int, tuple[float, float]] = {}
        boxes: list[tuple[int, int, int, int]] = []
        for (local, block_width, block_height), (left, box_top) in zip(prepared, placements):
            for user_id, position in local.items():
                positions[user_id] = (position[0] + left, position[1] + box_top)
            boxes.append((left, box_top, block_width, block_height))
        return positions, canvas_width, canvas_height, boxes

    @staticmethod
    def _fate_components(node_ids: tuple[int, ...], edges: list[tuple[int, int]]) -> list[list[int]]:
        adjacent = {user_id: set() for user_id in node_ids}
        for left, right in edges:
            if left in adjacent and right in adjacent:
                adjacent[left].add(right)
                adjacent[right].add(left)
        components: list[list[int]] = []
        remaining = set(node_ids)
        while remaining:
            seed = min(remaining)
            pending = [seed]
            component: list[int] = []
            remaining.remove(seed)
            while pending:
                current = pending.pop()
                component.append(current)
                for neighbor in sorted(adjacent[current]):
                    if neighbor in remaining:
                        remaining.remove(neighbor)
                        pending.append(neighbor)
            components.append(sorted(component))
        return sorted(components, key=lambda item: (-len(item), item))

    def _fate_graph_layout(
        self,
        node_ids: tuple[int, ...],
        edges: list[tuple[int, int]],
        avatar_size: int,
        minimum_width: int = 900,
        minimum_height: int = 650,
        node_names: Mapping[int, str] | None = None,
        component_layout: bool = False,
    ) -> tuple[dict[int, tuple[float, float]], int, int]:
        count = len(node_ids)
        degrees = {user_id: 0 for user_id in node_ids}
        for left, right in edges:
            if left in degrees:
                degrees[left] += 1
            if right in degrees:
                degrees[right] += 1
        relation_count = max(1, len(edges))
        compact_group = 20 <= count <= 48 and not component_layout
        dense_layout = compact_group or component_layout
        if component_layout:
            width, height = minimum_width, minimum_height
        elif compact_group:
            # Screenshot-scale groups read better as a broad field. The extra
            # horizontal room gives unrelated links different corridors instead
            # of concentrating every crossing in the middle.
            width = max(minimum_width, 760 + count * 30 + relation_count * 10)
            height = max(minimum_height, 500 + count * 18 + relation_count * 7)
        else:
            width = max(minimum_width, 960 + count * 120 + relation_count * 18)
            height = max(minimum_height, 560 + count * 76 + relation_count * 10)
        center_x, center_y = width / 2, height / 2
        ordered_nodes = sorted(
            node_ids,
            key=lambda user_id: (-degrees[user_id], hashlib.blake2s(str(user_id).encode("ascii"), digest_size=2).digest()),
        )
        positions: dict[int, list[float]] = {}
        golden_angle = math.pi * (3 - math.sqrt(5))
        for rank, user_id in enumerate(ordered_nodes):
            digest = hashlib.blake2s(str(user_id).encode("ascii"), digest_size=4).digest()
            jitter_x = (int.from_bytes(digest[:2], "big") % 71) - 35
            jitter_y = (int.from_bytes(digest[2:], "big") % 71) - 35
            radius = 52 + math.sqrt(rank) * (142 if count <= 14 else 118)
            angle = rank * golden_angle + (int.from_bytes(digest[2:], "big") / 65535 - 0.5) * 0.48
            positions[user_id] = [
                center_x + math.cos(angle) * radius + jitter_x,
                center_y + math.sin(angle) * radius * 0.82 + jitter_y,
            ]
        adjacent = {(min(left, right), max(left, right)) for left, right in edges if left != right}
        min_distance = avatar_size + (72 if dense_layout else 118 if count > 48 else 104)
        for _ in range(220):
            forces = {user_id: [0.0, 0.0] for user_id in node_ids}
            for index, left in enumerate(node_ids):
                for right in node_ids[index + 1 :]:
                    dx = positions[right][0] - positions[left][0]
                    dy = positions[right][1] - positions[left][1]
                    distance = max(1.0, math.hypot(dx, dy))
                    force = 10400.0 / (distance * distance)
                    if distance < min_distance:
                        force += (min_distance - distance) * 0.62
                    fx, fy = force * dx / distance, force * dy / distance
                    forces[left][0] -= fx
                    forces[left][1] -= fy
                    forces[right][0] += fx
                    forces[right][1] += fy
            for left, right in adjacent:
                dx = positions[right][0] - positions[left][0]
                dy = positions[right][1] - positions[left][1]
                distance = max(1.0, math.hypot(dx, dy))
                digest = hashlib.blake2s(f"{left}:{right}".encode("ascii"), digest_size=2).digest()
                variation = int.from_bytes(digest, "big") % 96
                # Direct relationships define local neighborhoods. Keep their
                # endpoints visibly nearer than unrelated members, while the
                # clearance force below still protects avatar and name bounds.
                desired = max(
                    min_distance * 1.28,
                    (132 if dense_layout else 250 if count > 48 else 290) + variation,
                )
                force = (distance - desired) * (0.10 if dense_layout else 0.035)
                fx, fy = force * dx / distance, force * dy / distance
                forces[left][0] += fx
                forces[left][1] += fy
                forces[right][0] -= fx
                forces[right][1] -= fy
            for user_id in node_ids:
                x, y = positions[user_id]
                forces[user_id][0] += (center_x - x) * (0.0011 if dense_layout else 0.0022)
                forces[user_id][1] += (center_y - y) * (0.0034 if dense_layout else 0.0022)
                margin_x = avatar_size / 2 + 110
                margin_y = avatar_size / 2 + 104
                positions[user_id][0] = min(width - margin_x, max(margin_x, x + max(-10, min(10, forces[user_id][0]))))
                positions[user_id][1] = min(height - margin_y, max(margin_y, y + max(-10, min(10, forces[user_id][1]))))

        clearance = avatar_size + (68 if dense_layout else 96 if count > 48 else 84)
        margin_x = avatar_size / 2 + 48
        margin_y = avatar_size / 2 + 54
        for _ in range(80):
            adjusted = False
            for index, left in enumerate(node_ids):
                for right in node_ids[index + 1 :]:
                    dx = positions[right][0] - positions[left][0]
                    dy = positions[right][1] - positions[left][1]
                    distance = math.hypot(dx, dy)
                    if distance >= clearance:
                        continue
                    if distance < 1.0:
                        digest = hashlib.blake2s(f"{left}:{right}".encode("ascii"), digest_size=2).digest()
                        angle = int.from_bytes(digest, "big") / 65535 * math.tau
                        unit_x, unit_y = math.cos(angle), math.sin(angle)
                    else:
                        unit_x, unit_y = dx / distance, dy / distance
                    shift = (clearance - distance) / 2 + 0.5
                    positions[left][0] -= unit_x * shift
                    positions[left][1] -= unit_y * shift
                    positions[right][0] += unit_x * shift
                    positions[right][1] += unit_y * shift
                    for user_id in (left, right):
                        positions[user_id][0] = min(width - margin_x, max(margin_x, positions[user_id][0]))
                        positions[user_id][1] = min(height - margin_y, max(margin_y, positions[user_id][1]))
                    adjusted = True
            if not adjusted:
                break
        # Protect the visible avatar-plus-name footprint, not just its center.
        name_map = node_names or {}
        for _ in range(160):
            adjusted = False
            visible_boxes = self._fate_node_boxes(
                {user_id: (position[0], position[1]) for user_id, position in positions.items()},
                name_map,
                avatar_size,
            )
            for index, left in enumerate(node_ids):
                for right in node_ids[index + 1 :]:
                    if not self._fate_rects_overlap(visible_boxes[left], visible_boxes[right], padding=8):
                        continue
                    dx, dy = positions[right][0] - positions[left][0], positions[right][1] - positions[left][1]
                    if abs(dx) >= abs(dy):
                        direction = 1 if dx >= 0 else -1
                        positions[left][0] -= direction * 5
                        positions[right][0] += direction * 5
                    else:
                        direction = 1 if dy >= 0 else -1
                        positions[left][1] -= direction * 5
                        positions[right][1] += direction * 5
                    for user_id in (left, right):
                        positions[user_id][0] = min(width - margin_x, max(margin_x, positions[user_id][0]))
                        positions[user_id][1] = min(height - margin_y, max(margin_y, positions[user_id][1]))
                    adjusted = True
            if not adjusted:
                break
        # Fill the usable canvas after the simulation settles. This preserves
        # the irregular network shape while preventing a tiny central cluster
        # from floating in a large card made for labels and long connections.
        lower_x, upper_x = min(point[0] for point in positions.values()), max(point[0] for point in positions.values())
        lower_y, upper_y = min(point[1] for point in positions.values()), max(point[1] for point in positions.values())
        usable_left, usable_right = 120.0, width - 120.0
        usable_top, usable_bottom = 112.0, height - 132.0
        if component_layout:
            usable_left, usable_right = 64.0, width - 64.0
            usable_top, usable_bottom = 56.0, height - 72.0
        if compact_group:
            # Do not scale a naturally wide layout into a vertical tangle.
            # The graph uses most of the card width and a controlled middle
            # band, leaving outer lanes available for labels and detours.
            usable_top, usable_bottom = 86.0, height - 104.0
        span_x, span_y = max(1.0, upper_x - lower_x), max(1.0, upper_y - lower_y)
        scale = min((usable_right - usable_left) / span_x, (usable_bottom - usable_top) / span_y, 2.25 if compact_group else 1.85)
        if scale > 1.05:
            source_center_x, source_center_y = (lower_x + upper_x) / 2, (lower_y + upper_y) / 2
            target_center_x, target_center_y = (usable_left + usable_right) / 2, (usable_top + usable_bottom) / 2
            for point in positions.values():
                point[0] = target_center_x + (point[0] - source_center_x) * scale
                point[1] = target_center_y + (point[1] - source_center_y) * scale
        if compact_group:
            # The force solver is intentionally isotropic so it can preserve
            # real graph neighborhoods. The card is not: it is widescreen.
            # Stretch the settled result horizontally to turn empty card space
            # into separate edge corridors without forcing nodes into a grid.
            lower_x, upper_x = min(point[0] for point in positions.values()), max(point[0] for point in positions.values())
            horizontal_scale = min(2.25, (width - 240.0) / max(1.0, upper_x - lower_x))
            if horizontal_scale > 1.05:
                source_center_x = (lower_x + upper_x) / 2
                for point in positions.values():
                    point[0] = center_x + (point[0] - source_center_x) * horizontal_scale
        return {user_id: (round(value[0] / 4) * 4, round(value[1] / 4) * 4) for user_id, value in positions.items()}, width, height

    def _fate_node_boxes(
        self,
        positions: Mapping[int, tuple[float, float] | list[float]],
        names: Mapping[int, str],
        avatar_size: int,
        top: int = 0,
    ) -> dict[int, tuple[int, int, int, int]]:
        font = self._font(18, True)
        result: dict[int, tuple[int, int, int, int]] = {}
        for user_id, position in positions.items():
            center_x, center_y = float(position[0]), float(position[1]) + top
            label = self._ellipsize(str(names.get(user_id) or "这位群友"), font, avatar_size + 40)
            label_width = self._text_width(label, font)
            result[int(user_id)] = (
                int(center_x - max(avatar_size / 2, label_width / 2) - 6),
                int(center_y - avatar_size / 2 - 6),
                int(center_x + max(avatar_size / 2, label_width / 2) + 6),
                int(center_y + avatar_size / 2 + self._line_height(font) + 16),
            )
        return result

    @staticmethod
    def _fate_avatar_boxes(
        positions: Mapping[int, tuple[float, float] | list[float]],
        avatar_size: int,
        top: int = 0,
    ) -> dict[int, tuple[int, int, int, int]]:
        radius = avatar_size / 2 + 8
        return {
            int(user_id): (
                round(float(position[0]) - radius),
                round(float(position[1]) + top - radius),
                round(float(position[0]) + radius),
                round(float(position[1]) + top + radius),
            )
            for user_id, position in positions.items()
        }

    @staticmethod
    def _fate_rects_overlap(
        left: tuple[int, int, int, int],
        right: tuple[int, int, int, int],
        padding: int = 0,
    ) -> bool:
        return not (
            left[2] + padding < right[0]
            or right[2] + padding < left[0]
            or left[3] + padding < right[1]
            or right[3] + padding < left[1]
        )

    def _fate_label_position(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        width: int,
        height: int,
        node_boxes: list[tuple[int, int, int, int]],
        occupied_labels: list[tuple[int, int, int, int]],
        canvas_width: int | None = None,
        canvas_height: int | None = None,
        other_paths: list[tuple[tuple[float, float], tuple[float, float]]] | None = None,
    ) -> tuple[int, int, int, int]:
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = max(1.0, math.hypot(dx, dy))
        normal_x, normal_y = -dy / length, dx / length
        midpoint_x, midpoint_y = (start[0] + end[0]) / 2, (start[1] + end[1]) / 2
        unit_x, unit_y = dx / length, dy / length
        candidates = []
        max_offset = min(116.0, max(52.0, length * 0.20))
        for offset in (0, 28, -28, 56, -56, 84, -84, max_offset, -max_offset):
            for progress in (0.50, 0.42, 0.58, 0.34, 0.66, 0.24, 0.76, 0.16, 0.84):
                center_x = start[0] + dx * progress + normal_x * offset
                center_y = start[1] + dy * progress + normal_y * offset
                candidates.append((center_x, center_y))
        for center_x, center_y in candidates:
            box = (
                round(center_x - width / 2), round(center_y - height / 2),
                round(center_x + width / 2), round(center_y + height / 2),
            )
            if canvas_width is not None and canvas_height is not None and (
                box[0] < 32 or box[1] < 32 or box[2] > canvas_width - 32 or box[3] > canvas_height - 32
            ):
                continue
            crosses_other_path = any(
                self._fate_segment_intersects_rect(path_start, path_end, box, padding=10)
                for path_start, path_end in other_paths or ()
            )
            if not crosses_other_path and not any(
                self._fate_rects_overlap(box, other, padding=8) for other in node_boxes + occupied_labels
            ):
                return box
        # Never send a curve on a tour of the canvas to rescue one label.
        # Pick the least-conflicting lane near its own relationship instead.
        def collision_score(point: tuple[float, float]) -> tuple[int, float]:
            box = (
                round(point[0] - width / 2), round(point[1] - height / 2),
                round(point[0] + width / 2), round(point[1] + height / 2),
            )
            conflicts = sum(
                self._fate_rects_overlap(box, other, padding=8)
                for other in node_boxes + occupied_labels
            )
            conflicts += sum(
                self._fate_segment_intersects_rect(path_start, path_end, box, padding=10)
                for path_start, path_end in other_paths or ()
            )
            return conflicts, math.hypot(point[0] - midpoint_x, point[1] - midpoint_y)

        center_x, center_y = min(candidates, key=collision_score)
        return (
            round(center_x - width / 2), round(center_y - height / 2),
            round(center_x + width / 2), round(center_y + height / 2),
        )

    @staticmethod
    def _fate_segment_intersects_rect(
        start: tuple[float, float],
        end: tuple[float, float],
        box: tuple[int, int, int, int],
        padding: int = 0,
    ) -> bool:
        left, top, right, bottom = (
            box[0] - padding,
            box[1] - padding,
            box[2] + padding,
            box[3] + padding,
        )
        dx, dy = end[0] - start[0], end[1] - start[1]
        samples = max(4, int(math.hypot(dx, dy) / 12))
        return any(
            left <= start[0] + dx * index / samples <= right
            and top <= start[1] + dy * index / samples <= bottom
            for index in range(samples + 1)
        )

    def _fate_path_through_label(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
        label_box: tuple[int, int, int, int],
        protected_labels: list[tuple[int, int, int, int]],
    ) -> tuple[tuple[float, float], ...]:
        center = ((label_box[0] + label_box[2]) / 2, (label_box[1] + label_box[3]) / 2)
        return (start, center, end)

    @classmethod
    def _fate_curve_through_label_points(
        cls,
        start: tuple[float, float],
        end: tuple[float, float],
        label_box: tuple[int, int, int, int],
    ) -> tuple[tuple[float, float], ...]:
        anchor = ((label_box[0] + label_box[2]) / 2, (label_box[1] + label_box[3]) / 2)
        control = (
            anchor[0] * 2 - (start[0] + end[0]) / 2,
            anchor[1] * 2 - (start[1] + end[1]) / 2,
        )
        return cls._fate_bezier_points(start, control, end)

    @staticmethod
    def _fate_arrow_crosses_avatar(
        start: tuple[float, float],
        end: tuple[float, float],
        positions: Mapping[int, tuple[float, float]],
        excluded: set[int],
        radius: float,
        top: int,
    ) -> bool:
        dx, dy = end[0] - start[0], end[1] - start[1]
        length_squared = dx * dx + dy * dy
        if length_squared <= 0:
            return False
        for user_id, (center_x, center_y) in positions.items():
            if user_id in excluded:
                continue
            point_x, point_y = center_x, center_y + top
            ratio = max(0.0, min(1.0, ((point_x - start[0]) * dx + (point_y - start[1]) * dy) / length_squared))
            nearest_x, nearest_y = start[0] + ratio * dx, start[1] + ratio * dy
            if math.hypot(point_x - nearest_x, point_y - nearest_y) < radius:
                return True
        return False

    @classmethod
    def _fate_edge_types(cls, edges: list[tuple[int, int]]) -> dict[tuple[int, int], str]:
        edge_set = set(edges)
        incoming: dict[int, int] = {}
        outgoing: set[int] = set()
        for actor_id, target_id in edges:
            incoming[target_id] = incoming.get(target_id, 0) + 1
            outgoing.add(actor_id)
        result: dict[tuple[int, int], str] = {}
        for edge in edges:
            actor_id, target_id = edge
            if (target_id, actor_id) in edge_set:
                edge_type = "mutual"
            elif incoming.get(target_id, 0) >= 2:
                edge_type = "contested"
            elif cls._fate_has_directed_path(edge_set - {edge}, target_id, actor_id):
                edge_type = "cycle"
            elif incoming.get(actor_id, 0) > 0 or target_id in outgoing:
                edge_type = "relay"
            else:
                edge_type = "ordinary"
            result[edge] = edge_type
        return result

    @staticmethod
    def _fate_has_directed_path(edges: set[tuple[int, int]], start: int, end: int) -> bool:
        adjacent: dict[int, set[int]] = {}
        for actor_id, target_id in edges:
            adjacent.setdefault(actor_id, set()).add(target_id)
        pending = [start]
        seen = {start}
        while pending:
            current = pending.pop()
            if current == end:
                return True
            for neighbor in adjacent.get(current, ()):
                if neighbor not in seen:
                    seen.add(neighbor)
                    pending.append(neighbor)
        return False

    @classmethod
    def _fit_fate_aspect(
        cls,
        positions: dict[int, tuple[float, float]],
        boxes: list[tuple[int, int, int, int]],
        width: int,
        height: int,
    ) -> tuple[dict[int, tuple[float, float]], list[tuple[int, int, int, int]], int, int]:
        target_width = math.ceil(height * cls.FATE_ASPECT_RATIO / 8) * 8
        if target_width > width:
            margin = 48.0
            scale_x = (target_width - margin * 2) / max(1.0, width - margin * 2)
            positions = {
                user_id: (margin + (x - margin) * scale_x, y)
                for user_id, (x, y) in positions.items()
            }
            boxes = [
                (
                    round(margin + (left - margin) * scale_x),
                    top,
                    round(box_width * scale_x),
                    box_height,
                )
                for left, top, box_width, box_height in boxes
            ]
            width = target_width
        else:
            height = math.ceil(width / cls.FATE_ASPECT_RATIO / 8) * 8
        return positions, boxes, width, height

    def _draw_fate_legend(
        self,
        draw: ImageDraw.ImageDraw,
        x: int,
        y: int,
        edge_types: tuple[str, ...],
    ) -> None:
        font = self._font(16, True)
        cursor_x = x
        for edge_type in edge_types:
            label, color = (
                self.FATE_DIVORCED_EDGE_STYLE
                if edge_type == "divorced"
                else self.FATE_EDGE_STYLES[edge_type]
            )
            if edge_type == "divorced":
                for offset in range(0, 28, 10):
                    draw.line((cursor_x + offset, y + 10, cursor_x + offset + 6, y + 10), fill=color, width=4)
            else:
                draw.line((cursor_x, y + 10, cursor_x + 28, y + 10), fill=color, width=4)
                draw.polygon(
                    [(cursor_x + 32, y + 10), (cursor_x + 24, y + 5), (cursor_x + 24, y + 15)],
                    fill=color,
                )
            draw.text((cursor_x + 40, y), label, font=font, fill=self.FATE_MUTED)
            cursor_x += 40 + self._text_width(label, font) + 28

    @staticmethod
    def _draw_fate_arrow(
        draw: ImageDraw.ImageDraw,
        start: tuple[float, float],
        end: tuple[float, float],
        node_radius: float,
        curved: bool,
        color: str,
        dashed: bool = False,
        curve_offset: float = 34,
        waypoint: tuple[float, float] | None = None,
        waypoints: tuple[tuple[float, float], ...] | None = None,
    ) -> None:
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = max(1.0, math.hypot(dx, dy))
        unit_x, unit_y = dx / length, dy / length
        begin = (start[0] + unit_x * node_radius, start[1] + unit_y * node_radius)
        finish = (end[0] - unit_x * (node_radius + 10), end[1] - unit_y * (node_radius + 10))
        if waypoints:
            if len(waypoints) == 1:
                anchor = waypoints[0]
                # A quadratic Bezier reaches (begin + 2 * control + finish) / 4
                # at its midpoint. Recompute after clipping to avatar edges so
                # the drawn curve, not merely its source geometry, owns label.
                control = (
                    anchor[0] * 2 - (begin[0] + finish[0]) / 2,
                    anchor[1] * 2 - (begin[1] + finish[1]) / 2,
                )
                points = MiniGameReportRenderer._fate_bezier_points(begin, control, finish)
                MiniGameReportRenderer._draw_fate_path(draw, points, color, dashed)
                tangent = (finish[0] - control[0], finish[1] - control[1])
            else:
                points = (begin,) + waypoints + (finish,)
                MiniGameReportRenderer._draw_fate_path(draw, points, color, dashed)
                tangent = (finish[0] - points[-2][0], finish[1] - points[-2][1])
        elif waypoint is not None:
            middle = waypoint
            MiniGameReportRenderer._draw_fate_path(draw, (begin, middle, finish), color, dashed)
            tangent = (finish[0] - middle[0], finish[1] - middle[1])
        elif curved:
            normal_x, normal_y = -unit_y, unit_x
            control = (
                (begin[0] + finish[0]) / 2 + normal_x * curve_offset,
                (begin[1] + finish[1]) / 2 + normal_y * curve_offset,
            )
            points = [
                (
                    (1 - t) ** 2 * begin[0] + 2 * (1 - t) * t * control[0] + t**2 * finish[0],
                    (1 - t) ** 2 * begin[1] + 2 * (1 - t) * t * control[1] + t**2 * finish[1],
                )
                for t in (index / 16 for index in range(17))
            ]
            MiniGameReportRenderer._draw_fate_path(draw, points, color, dashed)
            tangent = (finish[0] - control[0], finish[1] - control[1])
        else:
            MiniGameReportRenderer._draw_fate_path(draw, (begin, finish), color, dashed)
            tangent = (finish[0] - begin[0], finish[1] - begin[1])
        tangent_length = max(1.0, math.hypot(*tangent))
        tx, ty = tangent[0] / tangent_length, tangent[1] / tangent_length
        nx, ny = -ty, tx
        tip = (finish[0] + tx * 9, finish[1] + ty * 9)
        draw.polygon(
            [tip, (finish[0] - tx * 8 + nx * 7, finish[1] - ty * 8 + ny * 7), (finish[0] - tx * 8 - nx * 7, finish[1] - ty * 8 - ny * 7)],
            fill=color,
        )

    @staticmethod
    def _fate_bezier_points(
        start: tuple[float, float],
        control: tuple[float, float],
        end: tuple[float, float],
        steps: int = 28,
    ) -> tuple[tuple[float, float], ...]:
        return tuple(
            (
                (1 - t) ** 2 * start[0] + 2 * (1 - t) * t * control[0] + t**2 * end[0],
                (1 - t) ** 2 * start[1] + 2 * (1 - t) * t * control[1] + t**2 * end[1],
            )
            for t in (index / steps for index in range(steps + 1))
        )

    @staticmethod
    def _draw_fate_path(
        draw: ImageDraw.ImageDraw,
        points: tuple[tuple[float, float], ...] | list[tuple[float, float]],
        color: str,
        dashed: bool,
    ) -> None:
        if not dashed:
            draw.line(points, fill=color, width=4, joint="curve")
            return
        for index in range(len(points) - 1):
            if index % 2 == 0:
                draw.line((points[index], points[index + 1]), fill=color, width=4)

    def render_menu(self) -> Path:
        cards = (
            (
                "俄罗斯转盘",
                "#装填  ->  #开枪",
                "六格一发。随机：同生共死、反弹、弹仓偏移、意外失误、手指抽筋",
                "#fff1f6",
            ),
            (
                "定时炸弹",
                "#装弹  ->  #丢给 @成员",
                "120 秒接盘。另有成语接龙 DLC，玩法与专属事件请见本合并消息第六张图。",
                "#fff8fb",
            ),
            (
                "幸运骰局",
                "#骰子",
                "1-120 不重复。现有 16 种随机事件，完整效果请查看本合并消息的骰局详情页。",
                "#fff3f8",
            ),
            (
                "猜数字",
                "#猜数  ->  #猜 123",
                "0-999 猜中即胜。随机：发散思维、温差提示、数字回声、数位透视",
                "#fff7fb",
            ),
        )
        card_height = 188
        card_gap = 16
        body_height = len(cards) * card_height + (len(cards) - 1) * card_gap + 18
        image, draw, y = self._new_report("糖糖 · 小游戏", "统一使用 # 指令；成语接龙见炸弹详情页", body_height, "榜单：#转盘榜 / #炸弹榜 / #骰子榜 / #猜数榜")
        for title, command, note, fill in cards:
            draw.rounded_rectangle(
                (self.LEFT, y, self.WIDTH - self.RIGHT, y + card_height),
                radius=18,
                fill=fill,
                outline=self.TABLE_LINE,
                width=1,
            )
            draw.text((self.LEFT + 24, y + 20), title, font=self._font(25, True), fill=self.TEXT)
            self._draw_chip(draw, self.LEFT + 24, y + 62, command, self.PANEL, self.ACCENT)
            self._draw_wrapped(draw, self.LEFT + 24, y + 108, note, self._font(18), self._content_width - 48, self.MUTED, max_lines=2)
            y += card_height + card_gap
        return self._save(image, "mini_game_menu")

    def _menu_note_font(self, draw: ImageDraw.ImageDraw, note: str):
        max_width = self.WIDTH - self.LEFT - self.RIGHT - 56
        for size in range(19, 14, -1):
            font = self._font(size)
            left, _top, right, _bottom = draw.textbbox((0, 0), note, font=font)
            if right - left <= max_width:
                return font
        return self._font(15)

    def render_game_details(self) -> list[Path]:
        return [
            self.render_game_detail(game_type)
            for game_type in ("roulette", "bomb", "dice", "guess", "bomb_idiom")
        ]

    def render_game_detail(self, game_type: str) -> Path:
        detail = GAME_DETAIL_PAGES[game_type]
        events = tuple(detail["events"])
        event_height = 108
        event_gap = 10
        rule = str(detail["rule"])
        ranking = str(detail["ranking"])
        instruction_width = self.WIDTH - self.RIGHT - self.LEFT - 48
        rule_font = self._font(19)
        ranking_font = self._font(18)
        rule_height = self._text_block_height(rule, rule_font, instruction_width)
        ranking_y_offset = 57 + rule_height + 8
        ranking_height = self._text_block_height(ranking, ranking_font, instruction_width)
        instruction_height = ranking_y_offset + ranking_height + 18
        body_height = (
            instruction_height
            + 24
            + 40
            + len(events) * event_height
            + max(0, len(events) - 1) * event_gap
            + 18
        )
        image, draw, y = self._new_report(
            f"糖糖 · {detail['title']}",
            "玩法说明与随机事件效果",
            body_height,
            "成语接龙按详情格式；触发式事件会在下一次有效互动时揭示。",
        )
        draw.rounded_rectangle(
            (self.LEFT, y, self.WIDTH - self.RIGHT, y + instruction_height),
            radius=10,
            fill=str(detail["fill"]),
            outline=self.TABLE_LINE,
            width=1,
        )
        draw.text((self.LEFT + 24, y + 16), "玩法", font=self._font(23, True), fill=self.ACCENT)
        self._draw_chip(draw, self.LEFT + 112, y + 13, str(detail["command"]), self.PANEL, self.ACCENT)
        self._draw_wrapped(
            draw,
            self.LEFT + 24,
            y + 57,
            rule,
            rule_font,
            instruction_width,
            self.TEXT,
        )
        self._draw_wrapped(
            draw,
            self.LEFT + 24,
            y + ranking_y_offset,
            ranking,
            ranking_font,
            instruction_width,
            self.MUTED,
        )
        y += instruction_height + 24
        draw.text((self.LEFT, y), "随机事件", font=self._font(24, True), fill=self.ACCENT)
        y += 40
        for index, (name, effect) in enumerate(events):
            fill = self.PANEL if index % 2 == 0 else self.ROW_ALT
            draw.rounded_rectangle(
                (self.LEFT, y, self.WIDTH - self.RIGHT, y + event_height),
                radius=8,
                fill=fill,
                outline=self.TABLE_LINE,
                width=1,
            )
            label = "玩法规则" if name == "接龙规则" else "随机事件"
            draw.text((self.LEFT + 22, y + 14), f"{label} · {name}", font=self._font(21, True), fill=self.TEXT)
            self._draw_wrapped(
                draw,
                self.LEFT + 22,
                y + 47,
                effect,
                self._font(18),
                self.WIDTH - self.RIGHT - self.LEFT - 44,
                self.MUTED,
                max_lines=2,
            )
            y += event_height + event_gap
        return self._save(image, f"mini_game_{game_type}_detail")

    def render_ranking(
        self,
        payload: Mapping[str, Any],
        avatar_paths: Mapping[int, Path],
        group_avatar_paths: Mapping[int, Path],
    ) -> Path:
        sections = list(payload.get("sections", ()))
        is_global = bool(payload.get("global"))
        show_group_details = is_global and bool(payload.get("show_group_details"))
        group_labels = self._group_codes(payload.get("groups", ())) if show_group_details else {}
        body_height = 18
        for section in sections:
            rows = list(section.get("rows", ()))
            body_height += 50 + max(1, len(rows)) * 150 + max(0, len(rows) - 1) * 10
        if show_group_details and group_labels:
            body_height += 42 + len(group_labels) * 68
        image, draw, y = self._new_report(
            str(payload.get("title") or "小游戏榜单"),
            "仅按主数据排序，参局次数仅作注释",
            body_height,
            "总榜群标记仅为本图编号，不包含群号或 QQ 号。"
            if show_group_details
            else "发送 #游戏列表 查看小游戏。",
        )
        for section_index, section in enumerate(sections):
            rows = list(section.get("rows", ()))
            title = str(section.get("title") or "榜单")
            value_label = str(section.get("value_label") or "次数")
            draw.rounded_rectangle(
                (self.LEFT, y + 4, self.LEFT + 6, y + 34),
                radius=3,
                fill=AURORA_SIGNAL_COLORS[section_index % len(AURORA_SIGNAL_COLORS)],
            )
            draw.text((self.LEFT + 18, y), title, font=self._font(25, True), fill=self.TEXT)
            y += 42
            if not rows:
                self._draw_empty_card(draw, y, "暂无战绩")
                y += 106
                continue
            for index, row in enumerate(rows):
                self._draw_score_row(
                    image,
                    draw,
                    y,
                    row,
                    value_label,
                    avatar_paths,
                    group_labels,
                    index,
                )
                y += 160
        if show_group_details and group_labels:
            y += 4
            draw.text((self.LEFT, y), "涉及群聊", font=self._font(22, True), fill=self.MUTED)
            y += 34
            for group_id, code, name in group_labels.values():
                paste_multistop_gradient(
                    image,
                    (self.LEFT, y, self.WIDTH - self.RIGHT, y + 58),
                    ((0.0, "#ffffff"), (1.0, "#eef9f7")),
                    radius=8,
                    outline=self.TABLE_LINE,
                )
                self._draw_avatar_at(
                    image,
                    draw,
                    self.LEFT + 12,
                    y + 7,
                    44,
                    group_avatar_paths.get(group_id),
                    code,
                )
                draw.text((self.LEFT + 70, y + 16), code, font=self._font(19, True), fill=self.ACCENT)
                draw.text(
                    (self.LEFT + 128, y + 15),
                    self._ellipsize(name, self._font(20), self.WIDTH - self.LEFT - self.RIGHT - 150),
                    font=self._font(20),
                    fill=self.TEXT,
                )
                y += 68
        return self._save(image, "mini_game_ranking")

    @staticmethod
    def _group_codes(groups: object) -> dict[int, tuple[int, str, str]]:
        values = list(groups) if isinstance(groups, (list, tuple)) else []
        return {
            int(item["group_id"]): (int(item["group_id"]), f"G{index}", str(item.get("group_name") or "未命名群"))
            for index, item in enumerate(values, 1)
        }

    def _draw_score_row(
        self,
        image: Any,
        draw: ImageDraw.ImageDraw,
        y: int,
        row: Mapping[str, Any],
        value_label: str,
        avatar_paths: Mapping[int, Path],
        group_labels: Mapping[int, tuple[int, str, str]],
        index: int,
    ) -> None:
        row_tones = ("#fff2f5", "#f3effb", "#edf9f7", "#fff8dd")
        paste_multistop_gradient(
            image,
            (self.LEFT, y, self.WIDTH - self.RIGHT, y + 150),
            ((0.0, "#ffffff"), (1.0, row_tones[index % len(row_tones)])),
            radius=12,
            outline=self.TABLE_LINE,
        )
        draw.rounded_rectangle(
            (self.LEFT, y + 20, self.LEFT + 6, y + 130),
            radius=3,
            fill=AURORA_SIGNAL_COLORS[index % len(AURORA_SIGNAL_COLORS)],
        )
        rank = int(row.get("rank", index + 1))
        self._draw_rank_badge(draw, self.LEFT + 18, y + 23, rank)
        user_id = int(row.get("user_id", 0))
        self._draw_avatar_at(image, draw, self.LEFT + 76, y + 14, 62, avatar_paths.get(user_id), str(rank))
        nickname = self._ellipsize(str(row.get("nickname") or "这位群友"), self._font(22, True), 400)
        draw.text((self.LEFT + 156, y + 20), nickname, font=self._font(22, True), fill=self.TEXT)
        draw.text(
            (self.LEFT + 156, y + 55),
            f"参局 {int(row.get('games', 0))} 次",
            font=self._font(19),
            fill=self.MUTED,
        )
        value = f"{value_label} {int(row.get('value', 0))} 次"
        chip_fill = row_tones[index % len(row_tones)]
        chip_text = AURORA_SIGNAL_COLORS[index % len(AURORA_SIGNAL_COLORS)]
        self._draw_chip(draw, self.LEFT + 156, y + 88, value, chip_fill, chip_text)
        codes = [group_labels[group_id][1] for group_id in row.get("group_ids", ()) if group_id in group_labels]
        if codes:
            draw.text(
                (self.LEFT + 340, y + 58),
                "群 " + "、".join(codes),
                font=self._font(16),
                fill=self.MUTED,
            )

    def _draw_rank_badge(self, draw: ImageDraw.ImageDraw, x: int, y: int, rank: int) -> None:
        colors = (AURORA_SIGNAL_COLORS[3], AURORA_SIGNAL_COLORS[1], AURORA_SIGNAL_COLORS[2])
        fill = colors[rank - 1] if 1 <= rank <= 3 else "#f2eff7"
        text = self.TEXT if rank == 1 else "#ffffff" if rank <= 3 else self.MUTED
        draw.ellipse((x, y, x + 40, y + 40), fill=fill, outline="#ffffff", width=2)
        self._draw_centered(draw, x + 20, y + 20, str(rank), self._font(18, True), text)
