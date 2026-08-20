"""Render a fixed, screenshot-scale review case for the group fate graph."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from bot.services.mini_game_reports import MiniGameReportRenderer


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "data" / "reports" / "today_wife_cases"
OUTPUT_PATH = OUTPUT_DIR / "15_dense_group_fate_review.png"

# This mirrors the difficult shape seen in the production screenshot: around
# forty people, a few popular targets, reciprocal pairs, cycles, long links,
# and enough labels to reveal routing regressions.
NAMES = (
    "浩燃德", "奶块bot", "本群唯一指定吉祥物", "Fleet snowflake", "飞行纸蜻蜓",
    "松江小百合", "南岛雾梦", "贝拉娜气青拉", "干早爱音", "阿好的收到",
    "小王", "上善若水", "狼人", "夜盐酸", "暮雨千灯", "林间白鹿",
    "星野栞", "澄海的龙卷风", "写信人", "Destination", "knight",
    "人家才不是梦女", "拉菲今天没睡醒", "一卡要卡", "森町铃兰",
    "柚子汽水", "冬日回声", "春日部防卫队", "雾岛听风", "岚山微光",
    "秋水长天", "藤原千花", "猫与月亮", "夏目友人帐", "银河漫游者",
    "竹取物语", "十七岁的雨季", "晚安电台", "琥珀色海岸", "风吹麦浪",
)

EDGES = (
    (0, 7), (1, 13), (2, 7), (3, 18), (4, 9), (5, 22), (6, 7),
    (7, 8), (8, 6), (9, 16), (10, 14), (11, 20), (12, 10), (13, 16),
    (14, 23), (15, 5), (16, 28), (17, 16), (18, 19), (19, 18), (20, 29),
    (21, 16), (22, 5), (23, 31), (24, 7), (25, 33), (26, 30), (27, 10),
    (28, 35), (29, 34), (30, 2), (31, 37), (32, 24), (33, 32), (34, 39),
    (35, 36), (36, 35), (37, 7), (38, 20), (39, 25),
)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    avatar_path = OUTPUT_DIR / "dense_review_avatar.png"
    Image.new("RGB", (300, 300), "#f3a2bd").save(avatar_path)
    rows = [
        {
            "actor_id": 1000 + actor,
            "actor_nickname": NAMES[actor],
            "target_id": 1000 + target,
            "target_nickname": NAMES[target],
            "relation": {"affection": ((index * 11) % 47) - 16},
        }
        for index, (actor, target) in enumerate(EDGES)
    ]
    avatars = {1000 + index: avatar_path for index in range(len(NAMES))}
    renderer = MiniGameReportRenderer(OUTPUT_DIR, retention_hours=720)
    rendered = renderer.render_group_today_wife(
        rows,
        avatars,
        "2026-08-13",
        {"title": "特别篇", "key": "dense-review"},
        "四十位群友的关系同时显影，重点检查每条好感是否能一眼找到归属。",
    )
    rendered.replace(OUTPUT_PATH)
    print(OUTPUT_PATH.relative_to(ROOT))


if __name__ == "__main__":
    main()
