from __future__ import annotations

import asyncio

from PIL import Image

from bot.services.community_web import (
    CommunityWebRenderer,
    help_payload,
    page_html,
    public_web_url,
    ranking_payload,
)


GROUP_IDS = (910000101, 910000102, 910000103, 910000104, 910000105)
GROUP_LABELS = dict(zip(GROUP_IDS, ("修会", "剧团", "莫塔里", "翡萨烈", "墓岛"), strict=True))
GROUP_OPTIONS = [{"key": "domain", "label": "A海岸"}] + [
    {"key": str(group_id), "label": GROUP_LABELS[group_id]}
    for group_id in GROUP_IDS
]


class _Stats:
    def ranking_rows(self, scope: str, group_id: int | None):
        assert scope == "week"
        assert group_id == GROUP_IDS[2]
        return [
            {"rank": 1, "nickname": "测试成员", "message_count": 18, "group_name": "莫塔里"},
            {"rank": 2, "nickname": "另一位成员", "message_count": 8, "group_name": "莫塔里"},
        ]

    def group_totals(self, scope: str):
        return []

    def recent_group_daily_totals(self, group_id: int):
        return [
            {"day": f"2026-08-{day:02d}", "message_count": day - 20}
            for day in range(24, 31)
        ]


def test_community_short_links_are_bare_and_approved():
    assert public_web_url("ranking") is None
    assert public_web_url("help") == "short.example.invalid/h"


def test_ranking_payload_keeps_the_requested_scope_and_fixed_group_order():
    payload = ranking_payload(
        _Stats(),
        "week",
        str(GROUP_IDS[2]),
        selected_group_id=GROUP_IDS[2],
        group_label_override="莫塔里",
        group_labels=GROUP_LABELS,
        group_options=GROUP_OPTIONS,
    )

    assert payload["title"] == "莫塔里本周发言榜"
    assert payload["group_label"] == "莫塔里"
    assert payload["scope_title"] == "本周发言榜"
    assert payload["header_kicker"] == "AK-BOT FUNCTION"
    assert payload["displayed_count"] == 2
    assert [item["label"] for item in payload["group_options"]] == [
        "A海岸", "修会", "剧团", "莫塔里", "翡萨烈", "墓岛"
    ]
    assert payload["message_total"] == 26
    assert payload["rows"][0]["nickname"] == "测试成员"
    assert payload["chart"]["kind"] == "daily"
    assert payload["chart"]["title"] == "莫塔里近 7 日发言趋势"
    assert payload["chart"]["x_axis_label"] == "日期"
    assert payload["chart"]["y_axis_label"] == "发言数（条）"
    assert len(payload["chart"]["rows"]) == 7


def test_a_coast_payload_preserves_member_avatars_and_five_group_chart(tmp_path):
    avatar = tmp_path / "member.png"
    group_avatar = tmp_path / "group.png"
    Image.new("RGB", (120, 120), "#d76b91").save(avatar)
    Image.new("RGB", (120, 120), "#7c596a").save(group_avatar)
    group_totals = [
        {"group_id": group_id, "group_name": f"A海岸{index}群", "message_count": index * 10}
        for index, group_id in enumerate(GROUP_IDS, start=1)
    ]

    payload = ranking_payload(
        _Stats(),
        "week",
        "domain",
        rows=[
            {
                "rank": rank,
                "user_id": 42,
                "nickname": "测试成员" if rank == 1 else f"很长昵称也不能挤掉头像和发言数 {rank}",
                "message_count": 20 - rank,
                "group_labels": f"A海岸{rank}群",
            }
            for rank in range(1, 7)
        ],
        avatar_paths={42: avatar},
        group_totals=group_totals,
        group_avatar_paths={group_id: group_avatar for group_id in GROUP_IDS},
        group_label_override="A海岸",
        group_labels=GROUP_LABELS,
        group_options=GROUP_OPTIONS,
    )

    assert payload["rows"][0]["avatar"].startswith("data:image/webp;base64,")
    assert payload["rows"][0]["group_name"] == "A海岸1群"
    assert payload["chart"]["title"] == "A海岸群组发言对比"
    assert payload["chart"]["x_axis_label"] == "群组"
    assert payload["chart"]["y_axis_label"] == "发言数（条）"
    assert [row["label"] for row in payload["chart"]["rows"]] == [
        "修会", "剧团", "莫塔里", "翡萨烈", "墓岛"
    ]
    assert len(payload["chart"]["rows"]) == 5
    assert all(row["avatar"].startswith("data:image/webp;base64,") for row in payload["chart"]["rows"])
    capture = page_html("ranking", payload, capture=True)
    assert 'class="avatar"' in capture
    assert "A海岸群组发言对比" in capture
    assert "AK-BOT FUNCTION" in capture
    assert "AK bot" in capture
    assert 'class="chart-line"' in capture

    renderer = CommunityWebRenderer(tmp_path)

    async def render_and_close():
        try:
            path = await renderer.render_ranking(payload)
            point_x = await renderer._page.locator(".chart-point").evaluate_all(
                "nodes => nodes.map(node => node.getAttribute('cx'))"
            )
            label_x = await renderer._page.locator(".chart-x-label").evaluate_all(
                "nodes => nodes.map(node => node.getAttribute('x'))"
            )
            avatar_x = await renderer._page.locator(".chart-avatar").evaluate_all(
                "nodes => nodes.map(node => node.dataset.centerX)"
            )
            return path, point_x, label_x, avatar_x
        finally:
            await renderer.close()

    path, point_x, label_x, avatar_x = asyncio.run(render_and_close())
    assert point_x == label_x == avatar_x
    with Image.open(path) as image:
        assert image.width == 1080
        assert image.height > 1_100


def test_help_payload_uses_the_shared_public_command_catalog():
    payload = help_payload()

    assert payload["mode"] == "help"
    assert payload["categories"][0]["items"][0]["title"] == "在线帮助"
    assert any(item["title"] == "发言统计" for item in payload["categories"])
    assert any(item["title"] == "NTE 查询与排行" for item in payload["categories"])
    assert any(item["title"] == "本群设置" for item in payload["categories"])
    assert all(
        item["title"] != "个人发言统计"
        for category in payload["categories"]
        for item in category["items"]
    )
    commands = "\n".join(
        item["command"]
        for category in payload["categories"]
        for item in category["items"]
    )
    assert "#nte薄荷排行" in commands
    assert "#群设置" in commands
    assert "#机器人状态" not in commands
    assert "#系统设置" not in commands
    assert "#公告面板" not in commands
    assert "#管理员帮助" not in commands
    assert "#查重" not in commands


def test_help_payload_preserves_every_source_item_once_and_groups_local_games():
    payload = help_payload()
    source_items = [
        (category["title"], item["title"], item["command"], item["description"])
        for category in payload["categories"]
        for item in category["items"]
    ]
    grouped_items = [
        (section["title"], item["title"], item["command"], item["description"])
        for group in payload["groups"]
        for section in group["sections"]
        for item in section["items"]
    ]

    assert grouped_items == source_items
    games = next(group for group in payload["groups"] if group["key"] == "games")
    assert [section["title"] for section in games["sections"]] == ["小游戏", "小游戏榜单"]
    assert games["item_count"] == 8
    assert [group["key"] for group in payload["groups"]] == [
        "start", "chat", "nte", "wuwa", "live", "fate", "games", "stats", "group-admin"
    ]
    nte = next(group for group in payload["groups"] if group["key"] == "nte")
    group_admin = next(group for group in payload["groups"] if group["key"] == "group-admin")
    assert nte["item_count"] == 4
    assert [section["title"] for section in group_admin["sections"]] == ["本群设置", "群内自动功能"]
    assert group_admin["item_count"] == 6


def test_help_payload_indexes_only_confirmed_public_destinations():
    payload = help_payload()
    groups = {group["key"]: group for group in payload["groups"]}

    assert [action["target"] for action in groups["live"]["quick_actions"]] == [
        "https://bot.example.invalid/live/?view=today",
        "https://bot.example.invalid/live/?view=tomorrow",
        "https://bot.example.invalid/live/?view=week",
    ]
    assert groups["stats"]["quick_actions"] == []


def test_help_page_exposes_accessible_tabs_copy_feedback_and_mobile_layout():
    source = page_html("help")

    assert 'class="skip-link"' in source
    assert "setAttribute('role', 'tablist')" in source
    assert "setAttribute('role', 'tab')" in source
    assert "ArrowRight" in source and "ArrowLeft" in source
    assert 'data-copy-command=' in source
    assert "button.textContent = '已复制'" in source
    assert 'class="help-toast"' in source
    assert "flex-wrap:nowrap" in source
    assert "min-height:46px" in source
    assert "--help-violet:#8d67ce" in source
    assert "群友用法，" in source
    assert "本群设置只影响当前群" in source
    assert "--help-yellow" not in source
    assert "box-shadow:4px 4px 0 var(--help-ink)" not in source
    assert "background-size:32px 32px" not in source


def test_ranking_renderer_outputs_png_without_interactive_controls(tmp_path):
    payload = ranking_payload(
        _Stats(),
        "week",
        str(GROUP_IDS[2]),
        selected_group_id=GROUP_IDS[2],
        group_label_override="莫塔里",
        group_labels=GROUP_LABELS,
        group_options=GROUP_OPTIONS,
    )
    renderer = CommunityWebRenderer(tmp_path)

    async def render_and_close():
        try:
            path = await renderer.render_ranking(payload)
            point_x = await renderer._page.locator(".chart-point").evaluate_all(
                "nodes => nodes.map(node => node.getAttribute('cx'))"
            )
            label_x = await renderer._page.locator(".chart-x-label").evaluate_all(
                "nodes => nodes.map(node => node.getAttribute('x'))"
            )
            point_centers = await renderer._page.locator(".chart-point").evaluate_all(
                "nodes => nodes.map(node => { const point = node.ownerSVGElement.createSVGPoint(); "
                "point.x = Number(node.getAttribute('cx')); "
                "return point.matrixTransform(node.getScreenCTM()).x; })"
            )
            label_centers = await renderer._page.locator(".chart-x-label").evaluate_all(
                "nodes => nodes.map(node => { const point = node.ownerSVGElement.createSVGPoint(); "
                "point.x = Number(node.getAttribute('x')); "
                "return point.matrixTransform(node.getScreenCTM()).x; })"
            )
            return path, point_x, label_x, point_centers, label_centers
        finally:
            await renderer.close()

    path, point_x, label_x, point_centers, label_centers = asyncio.run(render_and_close())

    assert path.is_file() and path.stat().st_size > 10_000
    assert len(point_x) == len(label_x) == 7
    assert point_x == label_x
    assert all(
        abs(point_center - label_center) < 0.1
        for point_center, label_center in zip(point_centers, label_centers, strict=True)
    )
    capture = page_html("ranking", payload, capture=True)
    assert '<body class="capture-mode"' in capture
    assert ".capture-mode .interactive-bar{display:none!important}" in capture
    assert 'class="side-rail"' in capture
    assert 'class="date-seal"' in capture
    assert 'class="title-primary"' in capture
    assert 'class="title-secondary"' in capture
    assert ".ranking-row.row-first" in capture
    assert ".ranking-row.row-top" in capture
    assert ".ranking-row.row-main" in capture
    assert "日期" in capture
    assert "发言数（条）" in capture
    with Image.open(path) as image:
        assert image.width == 1080
        assert image.height > 500
        assert image.getbbox() is not None
