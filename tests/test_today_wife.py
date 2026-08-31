from __future__ import annotations

import ast
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw
import pytest

from bot.db import Database
from bot.services.mini_game_reports import MiniGameReportRenderer
from bot.services.today_wife import (
    ALREADY_DIVORCED_MESSAGES,
    DIVORCE_LINES,
    EMPTY_SPOTLIGHT_LINES,
    EPISODES,
    GROUP_SPOTLIGHT_LINES,
    NO_CANDIDATE_MESSAGES,
    NO_DRAW_MESSAGES,
    PLAYER_MESSAGE_POOLS,
    RELATIONSHIP_TITLES,
    RICH_CONTESTED_CONTEXTS,
    RICH_CYCLE_CONTEXTS,
    RICH_ECHO_CONTEXTS,
    RICH_MUTUAL_CONTEXTS,
    RICH_POPULAR_CONTEXTS,
    RICH_REDRAW_CONTEXTS,
    RICH_REUNION_CONTEXTS,
    RICH_TAKEN_CONTEXTS,
    RESULT_INTROS,
    STORY_CLOSINGS,
    STORY_MIDDLES,
    STORY_OPENINGS,
    STATE_MESSAGE_POOLS,
    TodayWifeService,
)
from bot.services.today_wife_story import StoryDirector


NOW = datetime.fromisoformat("2026-08-11T12:00:00+08:00")
GROUP_ID = 1001
BOT_ID = 2120682836
ACTOR_ID = 595861835


def make_service(tmp_path):
    database = Database(tmp_path / "bot.db")
    database.configure_groups((GROUP_ID,))
    database.set_group_info(GROUP_ID, "缘分测试群")
    return database, TodayWifeService(database)


def test_first_draw_result_message_mentions_the_selected_member() -> None:
    from bot.services.today_wife_result import first_draw_result_message

    message = first_draw_result_message(42, 600, Path("draw.png"))

    assert [segment.type for segment in message] == ["reply", "image", "text", "at", "text"]
    assert message[0].data["id"] == "42"
    assert message[3].data["qq"] == "600"
    assert message.extract_plain_text() == "\n今天的缘分悄悄落在了  身上。"


def test_legacy_daily_record_table_migrates_to_first_draw(tmp_path):
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE today_wife_records (
            group_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            actor_id INTEGER NOT NULL,
            actor_nickname TEXT NOT NULL DEFAULT '',
            target_id INTEGER NOT NULL,
            target_nickname TEXT NOT NULL DEFAULT '',
            relationship_key TEXT NOT NULL DEFAULT '',
            story_id TEXT NOT NULL DEFAULT '',
            branch TEXT NOT NULL DEFAULT 'ordinary',
            context_nickname TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            drawn_at TEXT NOT NULL,
            divorced_at TEXT,
            PRIMARY KEY (group_id, day, actor_id)
        );
        INSERT INTO today_wife_records
            (group_id,day,actor_id,actor_nickname,target_id,target_nickname,drawn_at)
        VALUES (1001,'2026-08-11',500,'小夏',600,'小秋','2026-08-11T12:00:00+08:00');
        """
    )
    connection.close()

    database = Database(path)
    with database.connect() as connection:
        row = connection.execute(
            "SELECT actor_id,target_id,draw_index,draw_source FROM today_wife_records"
        ).fetchone()

    assert tuple(row) == (500, 600, 1, "random")


def members():
    return [
        {"user_id": ACTOR_ID, "nickname": "测试用户"},
        {"user_id": BOT_ID, "nickname": "机器人账号"},
        {"user_id": 201, "nickname": "小青"},
        {"user_id": 202, "nickname": "小白"},
        {"user_id": 301, "nickname": "小红"},
        {"user_id": 302, "nickname": "小蓝"},
        {"user_id": 303, "nickname": "小绿"},
        {"user_id": 400, "nickname": "小明"},
        {"user_id": 500, "nickname": "小夏"},
        {"user_id": 600, "nickname": "小秋"},
    ]


def draw(service: TodayWifeService, actor_id: int, actor_name: str, target_id: int):
    return service.draw(GROUP_ID, actor_id, actor_name, members(), NOW, selected_target_id=target_id)


def test_draw_is_daily_and_can_target_the_bot_account(tmp_path):
    _database, service = make_service(tmp_path)

    first = draw(service, ACTOR_ID, "测试用户", BOT_ID)
    repeated = draw(service, ACTOR_ID, "测试用户", 201)

    assert first.kind == "drawn"
    assert first.record is not None
    assert first.record["target_id"] == BOT_ID
    assert first.record["target_nickname"] == "机器人账号"
    assert first.record["branch"] == "ordinary"
    assert repeated.kind == "existing"
    assert repeated.record is not None
    assert repeated.record["target_id"] == BOT_ID
    assert "糖糖" not in "".join(service.story_lines(first.record))


def test_force_draw_targets_the_requested_current_member(tmp_path):
    _database, service = make_service(tmp_path)

    outcome = service.force_draw(
        GROUP_ID,
        ACTOR_ID,
        "测试用户",
        members(),
        NOW,
        target_id=202,
    )

    assert outcome.kind == "drawn"
    assert outcome.record is not None
    assert outcome.record["target_id"] == 202
    assert service.history(GROUP_ID, ACTOR_ID)[0]["target_id"] == 202


def test_force_draw_persists_its_directed_source_and_initial_arc(tmp_path):
    from bot.services.today_wife_game import TodayWifeGameService

    database, service = make_service(tmp_path)
    outcome = service.force_draw(
        GROUP_ID,
        ACTOR_ID,
        "测试用户",
        members(),
        NOW,
        target_id=202,
    )

    assert outcome.kind == "drawn" and outcome.record is not None
    assert outcome.record["draw_source"] == "directed"
    assert "指定缘分" in outcome.record["story_tags"]
    relation = TodayWifeGameService(database).personal_archive(GROUP_ID, ACTOR_ID, NOW)["own"][0]["relation"]
    assert relation["narrative"]["relationship_origin"] == "directed"


def test_force_draw_fails_for_an_active_relationship_without_mutating_it(tmp_path):
    _database, service = make_service(tmp_path)
    first = service.force_draw(
        GROUP_ID,
        500,
        "小夏",
        members(),
        NOW,
        target_id=600,
    )
    assert first.kind == "drawn"
    before = service.history(GROUP_ID, 500)

    repeated = service.force_draw(
        GROUP_ID,
        500,
        "小夏",
        members(),
        NOW,
        target_id=201,
    )

    assert repeated.kind == "force_existing"
    assert repeated.record is not None
    assert repeated.record["target_id"] == 600
    assert service.history(GROUP_ID, 500) == before


@pytest.mark.parametrize("target_id", [None, "not-a-qq", ACTOR_ID, 999999])
def test_force_draw_rejects_invalid_targets_without_writing_a_record(tmp_path, target_id):
    _database, service = make_service(tmp_path)

    outcome = service.force_draw(
        GROUP_ID,
        ACTOR_ID,
        "测试用户",
        members(),
        NOW,
        target_id=target_id,
    )

    assert outcome.kind == "force_invalid_target"
    assert outcome.record is None
    assert service.history(GROUP_ID, ACTOR_ID) == []


def test_force_draw_preserves_the_single_post_divorce_redraw_rule(tmp_path):
    _database, service = make_service(tmp_path)
    first = service.force_draw(
        GROUP_ID,
        500,
        "小夏",
        members(),
        NOW,
        target_id=600,
    )
    assert first.kind == "drawn"
    assert service.divorce(GROUP_ID, 500, NOW).kind == "divorced"

    repeated_old_target = service.force_draw(
        GROUP_ID,
        500,
        "小夏",
        members(),
        NOW,
        target_id=600,
    )
    redraw = service.force_draw(
        GROUP_ID,
        500,
        "小夏",
        members(),
        NOW,
        target_id=201,
    )
    assert service.divorce(GROUP_ID, 500, NOW).kind == "divorced"
    exhausted = service.force_draw(
        GROUP_ID,
        500,
        "小夏",
        members(),
        NOW,
        target_id=202,
    )

    assert repeated_old_target.kind == "force_invalid_target"
    assert redraw.kind == "drawn"
    assert redraw.record is not None and redraw.record["draw_index"] == 2
    assert exhausted.kind == "existing_divorced"
    assert exhausted.record is not None and exhausted.record["draw_index"] == 2


def test_mutual_and_competing_branches_are_saved_for_the_later_drawer(tmp_path):
    _database, service = make_service(tmp_path)

    draw(service, 201, "小青", 202)
    mutual = draw(service, 202, "小白", 201)
    draw(service, 301, "小红", 400)
    contested = draw(service, 302, "小蓝", 400)
    popular = draw(service, 303, "小绿", 400)

    assert mutual.record is not None and mutual.record["branch"] == "mutual"
    assert "双向奔赴" in "".join(service.context_lines(mutual.record))
    assert contested.record is not None
    assert contested.record["branch"] == "contested"
    assert contested.record["context_nickname"] == "小红"
    assert "小红" in "".join(service.context_lines(contested.record))
    assert popular.record is not None and popular.record["branch"] == "popular"
    assert service.context_lines(popular.record)


def test_divorce_allows_one_redraw_and_preserves_both_history_entries(tmp_path):
    _database, service = make_service(tmp_path)

    draw(service, 500, "小夏", 600)
    divorce = service.divorce(GROUP_ID, 500, NOW)

    assert divorce.kind == "divorced"
    reopened = service.draw(GROUP_ID, 500, "小夏", members(), NOW, selected_target_id=201)
    assert reopened.kind == "drawn"
    assert reopened.record is not None and reopened.record["draw_index"] == 2
    history = service.history(GROUP_ID, 500)
    assert [(row["target_id"], row["status"]) for row in history] == [(201, "active"), (600, "divorced")]
    assert [row["target_id"] for row in service.group_records(GROUP_ID, NOW)] == [201]
    assert service.divorce(GROUP_ID, 500, NOW).kind == "divorced"
    exhausted = service.draw(GROUP_ID, 500, "小夏", members(), NOW, selected_target_id=202)
    assert exhausted.kind == "existing_divorced"
    assert exhausted.record is not None and exhausted.record["draw_index"] == 2


def test_history_group_graph_and_story_cards_render_with_dynamic_canvas(tmp_path):
    _database, service = make_service(tmp_path)
    ordinary = draw(service, ACTOR_ID, "测试用户", BOT_ID)
    draw(service, 201, "小青", 202)
    mutual = draw(service, 202, "小白", 201)
    draw(service, 301, "小红", 400)
    contested = draw(service, 302, "小蓝", 400)
    draw(service, 303, "小绿", 400)
    draw(service, 500, "小夏", 600)
    divorce = service.divorce(GROUP_ID, 500, NOW)

    avatar_path = tmp_path / "avatar.png"
    Image.new("RGB", (300, 300), "#f3a2bd").save(avatar_path)
    avatars = {int(member["user_id"]): avatar_path for member in members()}
    renderer = MiniGameReportRenderer(tmp_path / "reports")
    assert ordinary.record is not None and mutual.record is not None and contested.record is not None
    assert divorce.record is not None
    paths = [
        renderer.render_today_wife(ordinary.record, service.story_lines(ordinary.record), service.context_lines(ordinary.record), avatars),
        renderer.render_today_wife(mutual.record, service.story_lines(mutual.record), service.context_lines(mutual.record), avatars),
        renderer.render_today_wife(contested.record, service.story_lines(contested.record), service.context_lines(contested.record), avatars),
        renderer.render_divorce(divorce.record, service.divorce_lines(divorce.record), avatars),
        renderer.render_today_wife_history(
            service.history(GROUP_ID, 500),
            avatars,
            service.state_message("empty_history", GROUP_ID, 500, NOW),
        ),
        renderer.render_group_today_wife(
            service.group_records(GROUP_ID, NOW),
            avatars,
            "2026-08-11",
            service.episode(GROUP_ID, NOW.date()),
            service.group_spotlight(
                service.group_records(GROUP_ID, NOW), GROUP_ID, NOW.date()
            ),
        ),
    ]
    for path in paths:
        assert path.exists()
        with Image.open(path) as image:
            assert image.width >= 768
            assert image.height >= 260
            assert image.getbbox() is not None
    with Image.open(paths[0]) as image:
        # The dynamically selected name is rendered with the report accent,
        # rather than disappearing into the surrounding narrative text.
        assert any(pixel[:3] == (243, 93, 145) for pixel in image.get_flattened_data())
    for path in paths[:3]:
        with Image.open(path) as image:
            bottom_safe_area = image.crop((30, image.height - 52, image.width - 30, image.height - 22))
            assert not any(max(pixel[:3]) < 130 for pixel in bottom_safe_area.get_flattened_data())


def test_clear_group_records_removes_only_the_selected_group(tmp_path):
    database, service = make_service(tmp_path)
    database.configure_groups((GROUP_ID, 1002))
    draw(service, ACTOR_ID, "测试用户", BOT_ID)
    service.draw(1002, 201, "小青", members(), NOW, selected_target_id=202)
    service.record_activity("wife-activity-1", GROUP_ID, 201, NOW)
    service.record_activity("wife-activity-2", GROUP_ID, 201, NOW)
    service.record_activity("wife-activity-other-group", 1002, 201, NOW)

    assert service.clear_group_records(GROUP_ID) == 1
    assert service.history(GROUP_ID, ACTOR_ID) == []
    assert database.today_wife_activity_counts(GROUP_ID, NOW.date()) == {201: 2}
    assert database.today_wife_activity_counts(1002, NOW.date()) == {201: 1}
    assert len(service.group_records(1002, NOW)) == 1


def test_candidate_weights_favor_active_members_without_excluding_quiet_ones(tmp_path):
    _database, service = make_service(tmp_path)
    for index in range(12):
        service.record_activity(f"activity-{index}", GROUP_ID, 202, NOW)
    service.record_activity("activity-quiet", GROUP_ID, 201, NOW)

    weights = service.candidate_weights(GROUP_ID, ACTOR_ID, members(), NOW)

    assert weights[202] > weights[201] > 0


def test_candidate_weights_strongly_favor_same_day_reciprocal_draws(tmp_path):
    _database, service = make_service(tmp_path)
    draw(service, 202, "小白", ACTOR_ID)

    weights = service.candidate_weights(GROUP_ID, ACTOR_ID, members(), NOW)

    assert weights[202] / sum(weights.values()) == pytest.approx(0.45)


def test_candidate_weights_make_active_fresh_reciprocal_draws_high_probability(tmp_path):
    _database, service = make_service(tmp_path)
    draw(service, 202, "小白", ACTOR_ID)
    for index in range(303):
        service.record_activity(f"mutual-activity-{index}", GROUP_ID, 202, NOW)

    weights = service.candidate_weights(GROUP_ID, ACTOR_ID, members(), NOW)

    assert weights[202] / sum(weights.values()) == pytest.approx(0.70, abs=0.001)


def test_being_drawn_does_not_reduce_a_quiet_members_base_probability(tmp_path):
    _database, service = make_service(tmp_path)
    for actor_id, actor_name in ((201, "小青"), (301, "小红"), (302, "小蓝"), (303, "小绿")):
        draw(service, actor_id, actor_name, 202)

    weights = service.candidate_weights(GROUP_ID, 500, members(), NOW)

    assert weights[202] == weights[201]


def test_being_drawn_only_cools_the_water_chat_bonus(tmp_path):
    _database, service = make_service(tmp_path)
    for index in range(100):
        service.record_activity(f"popular-activity-{index}", GROUP_ID, 202, NOW)
        service.record_activity(f"control-activity-{index}", GROUP_ID, 201, NOW)

    before = service.candidate_weights(GROUP_ID, 500, members(), NOW)
    draw(service, 301, "小红", 202)
    after = service.candidate_weights(GROUP_ID, 500, members(), NOW)

    assert before[202] == before[201]
    assert 1.20 <= after[202] < after[201]


def test_first_three_messages_do_not_create_a_water_chat_bonus(tmp_path):
    _database, service = make_service(tmp_path)
    for index in range(3):
        service.record_activity(f"small-activity-{index}", GROUP_ID, 202, NOW)

    weights = service.candidate_weights(GROUP_ID, 500, members(), NOW)

    assert weights[202] == weights[201]


def test_candidate_weights_favor_recently_unseen_pairings(tmp_path):
    _database, service = make_service(tmp_path)
    service.draw(
        GROUP_ID,
        500,
        "小夏",
        members(),
        NOW - timedelta(days=1),
        selected_target_id=201,
    )

    weights = service.candidate_weights(GROUP_ID, 500, members(), NOW)

    assert weights[202] == weights[201] * 1.20


def test_draw_only_considers_members_who_spoke_in_the_last_three_days(tmp_path):
    _database, service = make_service(tmp_path)

    assert service.draw(GROUP_ID, ACTOR_ID, "测试用户", members(), NOW).kind == "no_candidates"
    service.record_activity("recently-active", GROUP_ID, 201, NOW - timedelta(days=2))

    outcome = service.draw(GROUP_ID, ACTOR_ID, "测试用户", members(), NOW)

    assert outcome.kind == "drawn"
    assert outcome.record is not None and outcome.record["target_id"] == 201


def test_large_group_graph_uses_a_dynamic_partitioned_wide_canvas(tmp_path):
    _database, service = make_service(tmp_path)
    renderer = MiniGameReportRenderer(tmp_path / "reports")
    rows = [
        {
            "actor_id": 10_000 + index * 2,
            "actor_nickname": f"成员{index * 2 + 1}",
            "target_id": 10_001 + index * 2,
            "target_nickname": f"成员{index * 2 + 2}",
        }
        for index in range(50)
    ]

    path = renderer.render_group_today_wife(
        rows,
        {},
        "2026-08-11",
        service.episode(GROUP_ID, NOW.date()),
        service.group_spotlight(rows, GROUP_ID, NOW.date()),
    )

    with Image.open(path) as image:
        assert image.width > renderer.WIDTH
        assert image.width > image.height
        assert abs(image.width / image.height - renderer.FATE_ASPECT_RATIO) < 0.01
        assert image.getbbox() is not None

    node_ids = tuple(
        user_id
        for index in range(50)
        for user_id in (10_000 + index * 2, 10_001 + index * 2)
    )
    positions, _width, _height, _boxes = renderer._fate_group_layout(node_ids, [
        (int(row["actor_id"]), int(row["target_id"])) for row in rows
    ], 68)
    minimum_distance = min(
        ((positions[left][0] - positions[right][0]) ** 2 + (positions[left][1] - positions[right][1]) ** 2) ** 0.5
        for index, left in enumerate(node_ids)
        for right in node_ids[index + 1 :]
    )
    assert minimum_distance >= 150


def test_group_graph_uses_distinct_colors_for_relationship_types(tmp_path):
    _database, service = make_service(tmp_path)
    renderer = MiniGameReportRenderer(tmp_path / "reports")
    edges = [
        (1, 2), (2, 1),
        (3, 4), (5, 4),
        (6, 7), (7, 8), (8, 6),
        (9, 10), (10, 11),
        (12, 13),
    ]
    rows = [
        {
            "actor_id": actor_id,
            "actor_nickname": f"成员{actor_id}",
            "target_id": target_id,
            "target_nickname": f"成员{target_id}",
        }
        for actor_id, target_id in edges
    ]

    edge_types = renderer._fate_edge_types(edges)

    assert set(edge_types.values()) == set(renderer.FATE_EDGE_STYLES)
    assert edge_types[(1, 2)] == "mutual"
    assert edge_types[(3, 4)] == "contested"
    assert edge_types[(6, 7)] == "cycle"
    assert edge_types[(9, 10)] == "relay"
    assert edge_types[(12, 13)] == "ordinary"

    path = renderer.render_group_today_wife(
        rows,
        {},
        "2026-08-11",
        service.episode(GROUP_ID, NOW.date()),
        service.group_spotlight(rows, GROUP_ID, NOW.date()),
    )

    with Image.open(path) as image:
        colors = image.convert("RGB").getcolors(maxcolors=image.width * image.height)
        assert colors is not None
        color_counts = {color: count for count, color in colors}
        for _label, color in renderer.FATE_EDGE_STYLES.values():
            assert color_counts.get(ImageColor.getrgb(color), 0) >= 100


def test_group_graph_keeps_avatar_names_and_affection_labels_separate(tmp_path):
    renderer = MiniGameReportRenderer(tmp_path / "reports")
    names = {
        1: "名字很长很长的小明",
        2: "名字很长很长的小夏",
        3: "名字很长很长的小白",
        4: "名字很长很长的小雨",
        5: "名字很长很长的小北",
        6: "名字很长很长的小东",
    }
    edges = [(1, 2), (3, 4), (5, 6), (1, 4), (3, 6)]
    positions, _width, _height, _boxes = renderer._fate_group_layout(
        tuple(names), edges, 96, names
    )
    node_boxes = list(renderer._fate_node_boxes(positions, names, 96).values())
    for index, box in enumerate(node_boxes):
        assert not any(renderer._fate_rects_overlap(box, other, padding=8) for other in node_boxes[index + 1 :])

    label_boxes = []
    for actor_id, target_id in edges:
        label = renderer._fate_label_position(
            positions[actor_id], positions[target_id], 88, 28, node_boxes, label_boxes
        )
        assert not any(renderer._fate_rects_overlap(label, node_box, padding=8) for node_box in node_boxes)
        assert not any(renderer._fate_rects_overlap(label, other, padding=8) for other in label_boxes)
        label_boxes.append(label)


def test_group_graph_keeps_one_connected_affection_anchor_per_edge(tmp_path):
    renderer = MiniGameReportRenderer(tmp_path / "reports")
    names = {index: f"成员{index}" for index in range(1, 9)}
    edges = [(1, 2), (3, 2), (4, 5), (6, 7), (8, 2)]
    positions, width, height, _boxes = renderer._fate_group_layout(tuple(names), edges, 96, names)
    node_boxes = list(renderer._fate_node_boxes(positions, names, 96).values())
    label_boxes = []
    for actor_id, target_id in edges:
        label = renderer._fate_label_position(
            positions[actor_id], positions[target_id], 92, 28, node_boxes, label_boxes
        )
        label_boxes.append(label)
        center_x, center_y = (label[0] + label[2]) / 2, (label[1] + label[3]) / 2
        assert 0 < center_x < width
        assert 0 < center_y < height
        distance = ((positions[target_id][0] - positions[actor_id][0]) ** 2 + (positions[target_id][1] - positions[actor_id][1]) ** 2) ** 0.5
        assert ((center_x - positions[actor_id][0]) ** 2 + (center_y - positions[actor_id][1]) ** 2) ** 0.5 < distance * 1.5


def test_group_graph_affection_label_rejects_other_relationship_paths(tmp_path):
    renderer = MiniGameReportRenderer(tmp_path / "reports")
    start, end = (180.0, 180.0), (620.0, 460.0)
    crossing_paths = [((180.0, 460.0), (620.0, 180.0))]

    label = renderer._fate_label_position(
        start,
        end,
        92,
        28,
        [],
        [],
        canvas_width=800,
        canvas_height=640,
        other_paths=crossing_paths,
    )

    assert not renderer._fate_segment_intersects_rect(*crossing_paths[0], label, padding=10)
    assert renderer._fate_segment_intersects_rect(start, end, label, padding=120)


def test_screenshot_scale_group_graph_keeps_every_affection_anchor_unambiguous(tmp_path):
    renderer = MiniGameReportRenderer(tmp_path / "reports")
    node_ids = tuple(range(1, 41))
    names = {user_id: f"群友{user_id}的很长昵称" for user_id in node_ids}
    edges = [
        (1, 8), (2, 14), (3, 8), (4, 19), (5, 10), (6, 23), (7, 8),
        (8, 9), (9, 7), (10, 17), (11, 15), (12, 11), (13, 10), (14, 17),
        (15, 24), (16, 6), (17, 29), (18, 17), (19, 20), (20, 19), (21, 30),
        (22, 17), (23, 6), (24, 32), (25, 8), (26, 34), (27, 31), (28, 11),
        (29, 36), (30, 35), (31, 3), (32, 38), (33, 25), (34, 33), (35, 40),
        (36, 37), (37, 36), (38, 8), (39, 21), (40, 26),
    ]
    positions, width, graph_height, _boxes = renderer._fate_group_layout(node_ids, edges, 62, names)
    top = 228
    positions, _boxes, width, height = renderer._fit_fate_aspect(positions, [], width, top + graph_height + 64)
    starts = {edge: (positions[edge[0]][0], positions[edge[0]][1] + top) for edge in edges}
    ends = {edge: (positions[edge[1]][0], positions[edge[1]][1] + top) for edge in edges}
    node_boxes = list(renderer._fate_node_boxes(positions, names, 62, top).values())
    labels = {}
    occupied = []
    for edge in sorted(edges, key=lambda item: -((ends[item][0] - starts[item][0]) ** 2 + (ends[item][1] - starts[item][1]) ** 2)):
        label = renderer._fate_label_position(
            starts[edge], ends[edge], 88, 28, node_boxes, occupied,
            canvas_width=width, canvas_height=height,
            other_paths=[(starts[other], ends[other]) for other in edges if other != edge],
        )
        labels[edge] = label
        occupied.append(label)

    for edge, label in labels.items():
        assert not any(renderer._fate_rects_overlap(label, other, padding=8) for other_edge, other in labels.items() if other_edge != edge)
        assert not any(renderer._fate_rects_overlap(label, node_box, padding=8) for node_box in node_boxes)


def test_group_graph_routes_affection_with_one_continuous_curve(tmp_path):
    renderer = MiniGameReportRenderer(tmp_path / "reports")
    start, end = (120.0, 260.0), (620.0, 340.0)
    label = (330, 190, 430, 218)

    path = renderer._fate_path_through_label(start, end, label, [])

    assert len(path) == 3
    assert path[1] == ((label[0] + label[2]) / 2, (label[1] + label[3]) / 2)
    control = (
        path[1][0] * 2 - (start[0] + end[0]) / 2,
        path[1][1] * 2 - (start[1] + end[1]) / 2,
    )
    curve = renderer._fate_bezier_points(start, control, end)
    assert curve[len(curve) // 2] == path[1]


def test_screenshot_scale_layout_keeps_connected_members_closer_than_unrelated_members(tmp_path):
    renderer = MiniGameReportRenderer(tmp_path / "reports")
    node_ids = tuple(range(1, 25))
    edges = [
        (1, 2), (2, 3), (3, 4), (4, 5), (5, 6),
        (7, 8), (8, 9), (9, 10), (10, 11),
        (12, 13), (13, 14), (14, 15),
        (16, 17), (18, 19), (20, 21),
    ]

    positions, _width, _height, _boxes = renderer._fate_group_layout(node_ids, edges, 62)
    adjacent = {tuple(sorted(edge)) for edge in edges}
    edge_distances = [
        ((positions[left][0] - positions[right][0]) ** 2 + (positions[left][1] - positions[right][1]) ** 2) ** 0.5
        for left, right in adjacent
    ]
    unrelated_distances = [
        ((positions[left][0] - positions[right][0]) ** 2 + (positions[left][1] - positions[right][1]) ** 2) ** 0.5
        for index, left in enumerate(node_ids)
        for right in node_ids[index + 1 :]
        if (left, right) not in adjacent
    ]

    assert sum(edge_distances) / len(edge_distances) < sum(unrelated_distances) / len(unrelated_distances) * 0.78


def test_disconnected_relationship_islands_are_not_interleaved(tmp_path):
    renderer = MiniGameReportRenderer(tmp_path / "reports")
    node_ids = tuple(range(1, 17))
    edges = [(1, 2), (2, 3), (4, 5), (6, 7), (8, 9), (10, 11), (12, 13), (14, 15)]

    positions, _width, _height, boxes = renderer._fate_group_layout(node_ids, edges, 68)
    components = renderer._fate_components(node_ids, edges)

    assert len(boxes) == len(components)
    for component, (left, top, width, height) in zip(components, boxes):
        for user_id in component:
            x, y = positions[user_id]
            assert left <= x <= left + width
            assert top <= y <= top + height
    for index, left_box in enumerate(boxes):
        assert not any(
            renderer._fate_rects_overlap(
                (left_box[0], left_box[1], left_box[0] + left_box[2], left_box[1] + left_box[3]),
                (right_box[0], right_box[1], right_box[0] + right_box[2], right_box[1] + right_box[3]),
                padding=24,
            )
            for right_box in boxes[index + 1 :]
        )


def test_large_connected_group_graph_keeps_avatar_clearance(tmp_path):
    renderer = MiniGameReportRenderer(tmp_path / "reports")
    node_ids = tuple(range(1, 101))
    edges = [(user_id, user_id + 1) for user_id in range(1, 100)] + [(100, 1)]

    positions, width, graph_height, _boxes = renderer._fate_group_layout(node_ids, edges, 68)
    minimum_distance = min(
        ((positions[left][0] - positions[right][0]) ** 2 + (positions[left][1] - positions[right][1]) ** 2) ** 0.5
        for index, left in enumerate(node_ids)
        for right in node_ids[index + 1 :]
    )
    _positions, _boxes, fitted_width, fitted_height = renderer._fit_fate_aspect(
        positions, [], width, graph_height + 250
    )

    assert minimum_distance >= 160
    assert fitted_width > fitted_height
    assert abs(fitted_width / fitted_height - renderer.FATE_ASPECT_RATIO) < 0.01


def test_empty_group_graph_uses_widescreen_canvas(tmp_path):
    _database, service = make_service(tmp_path)
    renderer = MiniGameReportRenderer(tmp_path / "reports")

    path = renderer.render_group_today_wife(
        [],
        {},
        "2026-08-11",
        service.episode(GROUP_ID, NOW.date()),
        service.group_spotlight([], GROUP_ID, NOW.date()),
    )

    with Image.open(path) as image:
        assert image.size == (960, 540)


def test_today_wife_intro_card_is_widescreen_and_keeps_relation_colors(tmp_path):
    renderer = MiniGameReportRenderer(tmp_path / "reports")

    path = renderer.render_today_wife_intro_card()

    with Image.open(path) as image:
        assert image.size == (960, 540)
        colors = image.convert("RGB").getcolors(maxcolors=image.width * image.height)
        assert colors is not None
        color_counts = {color: count for count, color in colors}
        for edge_type in ("mutual", "contested"):
            color = ImageColor.getrgb(renderer.FATE_EDGE_STYLES[edge_type][1])
            assert color_counts.get(color, 0) >= 100


def test_same_group_and_day_share_one_episode_but_keep_individual_scenes(tmp_path):
    _database, service = make_service(tmp_path)
    first = draw(service, 201, "小青", 202)
    second = draw(service, 301, "小红", 302)

    assert first.record is not None and second.record is not None
    assert first.record["episode_key"] == second.record["episode_key"]
    context = StoryDirector.context_for_story_id(str(first.record["story_id"]))
    assert context is not None
    assert first.record["episode_title"] == context["title"]
    assert first.record["episode_prop"] == context["prop"]
    assert context["opening"] in service.story_lines(first.record)
    assert service.story_lines(first.record) != service.story_lines(second.record)


def test_unified_story_ids_do_not_fall_back_to_a_legacy_episode(tmp_path):
    _database, service = make_service(tmp_path)
    outcome = draw(service, ACTOR_ID, "测试用户", BOT_ID)

    assert outcome.record is not None
    record = outcome.record
    context = StoryDirector.context_for_story_id(str(record["story_id"]))
    assert context is not None

    lines = service.story_lines(record)

    assert lines[0] == context["opening"]
    assert record["episode_key"] == context["scene_id"]
    assert record["episode_title"] == context["title"]


def test_interaction_card_reserves_height_for_structured_story_labels(tmp_path):
    renderer = MiniGameReportRenderer(tmp_path / "reports")
    label = "承接上一幕的关键线索" * 24
    paragraph = "小明和小夏把这一幕继续写下去。" * 30
    event = {
        "title": "今日互动",
        "actor_nickname": "小明",
        "actor_remaining": 5,
        "effects": (),
        "narrative_plan": {
            "blocks": ({"label": label, "text": paragraph},),
        },
    }

    path = renderer.render_today_wife_interaction(event, {})

    width, padding = 1020, 56
    body_width = width - padding * 2 - 44
    expected_story_height = max(
        154,
        55
        + renderer._text_block_height(label, renderer._font(17, True), body_width)
        + 6
        + renderer._fate_text_height(paragraph, renderer._font(24), body_width)
        + 24,
    )
    expected_height = 144 + expected_story_height + 24 + 122 + 30 + renderer._game_toolbar_height(5) + 34
    with Image.open(path) as image:
        assert image.height == expected_height


def test_game_toolbar_stays_fixed_when_legacy_actions_are_present(tmp_path):
    renderer = MiniGameReportRenderer(tmp_path / "reports")
    event = {
        "title": "今日互动",
        "actor_nickname": "小明",
        "actor_remaining": 5,
        "effects": (),
        "narrative": "这一幕还没有结束。",
    }

    default_path = renderer.render_today_wife_interaction(event, {})
    expanded_path = renderer.render_today_wife_interaction(
        {**event, "available_actions": ("靠近", "倾听", "回应", "修复", "助攻")},
        {},
    )

    assert renderer._game_toolbar_actions(("靠近", "倾听", "回应", "修复", "助攻")) == (
        "#我的缘分",
        "#群缘分",
        "#离婚",
    )
    assert renderer._game_toolbar_height(5) == 64
    assert renderer._game_toolbar_height(5, ("靠近", "倾听", "回应", "修复", "助攻")) == 64
    with Image.open(default_path) as default_image, Image.open(expanded_path) as expanded_image:
        assert expanded_image.height == default_image.height
        assert expanded_image.getbbox() is not None


def test_action_prompt_card_renders_current_hook_and_available_actions(tmp_path):
    renderer = MiniGameReportRenderer(tmp_path / "reports")
    path = renderer.render_today_wife_action_prompt(
        {
            "actor_remaining": 4,
            "current_hook": {"summary": "那句没有说完的话还在等回应"},
            "message": "请选择一次不会消耗次数的下一步。",
            "available_actions": ("靠近", "倾听", "回应", "修复", "助攻"),
        }
    )

    with Image.open(path) as image:
        assert image.width == 840
        assert image.height > 300


def test_legacy_action_prompt_cannot_put_target_bound_commands_in_the_footer(tmp_path):
    renderer = MiniGameReportRenderer(tmp_path / "reports")
    options = (
        {
            "choice_id": "assist_opening",
            "label": "替人递回线索",
            "command": "#互动 助攻",
            "display_command": "#互动 助攻 + @小夏",
            "intent": "助攻",
            "risk_hint": "只能创造机会，结局仍由当事人决定",
            "requires_mention": True,
        },
    )

    path = renderer.render_today_wife_action_prompt(
        {
            "actor_remaining": 4,
            "current_hook": {"summary": "有人在等一位群友的回应"},
            "message": "请选择下一步。",
            "action_options": options,
        }
    )

    assert renderer._game_toolbar_actions(options) == ("#我的缘分", "#群缘分", "#离婚")
    with Image.open(path) as image:
        assert image.width == 840
        assert image.height > 360
        assert image.getbbox() is not None


def test_unknown_legacy_story_ids_use_the_new_daily_episode_generator(tmp_path):
    _database, service = make_service(tmp_path)
    record = {
        "group_id": GROUP_ID,
        "day": NOW.date().isoformat(),
        "actor_id": 201,
        "target_id": 202,
        "target_nickname": "小白",
        "draw_index": 1,
        "story_id": "便利店",
    }

    lines = service.story_lines(record)

    assert len(lines) == 3
    assert lines != (
        "凌晨一点的便利店只剩关东煮还冒着热气。",
        "你推门时，对方正好拿起最后一杯热可可。",
        "于是你们决定平分这一晚的好运。",
    )


def test_taken_member_can_start_a_new_relationship_chain(tmp_path):
    _database, service = make_service(tmp_path)
    draw(service, 201, "小青", 202)
    outcome = draw(service, 202, "小白", 301)

    assert outcome.record is not None
    assert "taken" in outcome.record["story_flags"]
    assert outcome.record["taken_by_nickname"] == "小青"
    assert service.context_lines(outcome.record)
    assert "自有想法" in outcome.record["story_tags"]


def test_three_person_cycle_becomes_a_group_story_highlight(tmp_path):
    _database, service = make_service(tmp_path)
    draw(service, 201, "小青", 202)
    draw(service, 202, "小白", 301)
    outcome = draw(service, 301, "小红", 201)

    assert outcome.record is not None
    assert "cycle" in outcome.record["story_flags"]
    assert "闭环" in outcome.record["story_tags"]
    assert "关系闭环" in service.group_spotlight(
        service.group_records(GROUP_ID, NOW), GROUP_ID, NOW.date()
    )


def test_narrative_renderer_highlights_every_dynamic_name_and_wraps_long_names(tmp_path):
    renderer = MiniGameReportRenderer(tmp_path / "reports")
    image = Image.new("RGB", (420, 180), "#fffafd")
    draw_context = ImageDraw.Draw(image)
    font = renderer._font(27)
    long_name = "很长很长很长很长很长很长的群友名字"

    end_y = renderer._draw_fate_wrapped(
        draw_context,
        20,
        20,
        f"你当前已经是{long_name}的老婆，但你有自己的想法。",
        font,
        220,
        (long_name,),
    )

    assert end_y > 20 + renderer._line_height(font)
    assert any(pixel[:3] == (243, 93, 145) for pixel in image.get_flattened_data())


def test_empty_history_and_group_cards_require_randomized_narration(tmp_path):
    _database, service = make_service(tmp_path)
    renderer = MiniGameReportRenderer(tmp_path / "reports")
    empty_message = service.state_message("empty_history", GROUP_ID, ACTOR_ID, NOW)
    history_path = renderer.render_today_wife_history([], {}, empty_message)
    group_path = renderer.render_group_today_wife(
        [],
        {},
        NOW.date().isoformat(),
        service.episode(GROUP_ID, NOW.date()),
        service.group_spotlight([], GROUP_ID, NOW.date()),
    )

    assert history_path.exists() and group_path.exists()
    with pytest.raises(ValueError):
        renderer.render_today_wife_history([], {}, "")
    with pytest.raises(ValueError):
        renderer.render_group_today_wife(
            [], {}, NOW.date().isoformat(), service.episode(GROUP_ID, NOW.date()), ""
        )


def test_history_echo_reunion_and_redraw_are_persisted_story_chapters(tmp_path):
    _database, service = make_service(tmp_path)
    yesterday = NOW - timedelta(days=1)
    old = service.draw(GROUP_ID, 500, "小夏", members(), yesterday, selected_target_id=600)
    assert old.record is not None

    echo = draw(service, 500, "小夏", 600)
    assert echo.record is not None and "echo" in echo.record["story_flags"]
    assert "前缘回声" in echo.record["story_tags"]
    assert len(service.context_lines(echo.record)) == 2

    service.divorce(GROUP_ID, 500, NOW)
    redraw = draw(service, 500, "小夏", 201)
    assert redraw.record is not None and "redraw" in redraw.record["story_flags"]
    assert "新篇章" in redraw.record["story_tags"]
    assert len(service.context_lines(redraw.record)) == 2

    tomorrow = NOW + timedelta(days=1)
    reunion = service.draw(GROUP_ID, 500, "小夏", members(), tomorrow, selected_target_id=600)
    assert reunion.record is not None and "reunion" in reunion.record["story_flags"]
    assert "旧缘重逢" in reunion.record["story_tags"]
    assert len(service.context_lines(reunion.record)) == 2


def test_story_and_context_pools_have_enough_everyday_variety(tmp_path):
    _database, service = make_service(tmp_path)
    draw(service, 301, "小红", 400)
    outcome = draw(service, 302, "小蓝", 400)
    assert outcome.record is not None
    assert outcome.record["branch"] == "contested"
    assert service.context_lines(outcome.record) == service.context_lines(outcome.record)
    exact_thirty_pools = (
        RICH_MUTUAL_CONTEXTS,
        RICH_CONTESTED_CONTEXTS,
        RICH_POPULAR_CONTEXTS,
        DIVORCE_LINES,
        NO_DRAW_MESSAGES,
        NO_CANDIDATE_MESSAGES,
        ALREADY_DIVORCED_MESSAGES,
    )
    for pool in exact_thirty_pools:
        assert len(pool) == 30
        assert len(set(pool)) == 30

    assert len(EPISODES) >= 10
    assert set(PLAYER_MESSAGE_POOLS) >= {
        "member_list_unavailable",
        "empty_history",
        "locked",
        "clear_forbidden",
        "clear_warning",
        "clear_cancel_missing",
        "clear_cancelled",
        "clear_confirm_missing",
        "clear_success",
    }
    for name, pool in PLAYER_MESSAGE_POOLS.items():
        assert len(pool) >= 10
        assert len(set(pool)) == len(pool), name
        assert all(value for value in pool), name
        assert all("糖糖" not in str(value) for value in pool), name
        reachable = {service._variant(pool, f"pool-audit:{name}:{index}") for index in range(512)}
        assert len(reachable) >= 10, name

    reachable_episodes = {
        service.episode(GROUP_ID + offset, NOW.date())["key"] for offset in range(512)
    }
    assert len(reachable_episodes) >= 10
    reachable_empty_spotlights = {
        service.group_spotlight([], GROUP_ID + offset, NOW.date()) for offset in range(512)
    }
    assert len(reachable_empty_spotlights) >= 10

    assert set(STATE_MESSAGE_POOLS) == {
        "not_found",
        "no_candidates",
        "already_divorced",
        "member_list_unavailable",
        "empty_history",
        "locked",
        "clear_forbidden",
        "clear_warning",
        "clear_cancel_missing",
        "clear_cancelled",
        "clear_confirm_missing",
        "clear_success",
    }

    divorce = service.divorce(GROUP_ID, 302, NOW)
    assert divorce.record is not None
    assert service.divorce_lines(divorce.record) == service.divorce_lines(divorce.record)
    assert service.state_message("not_found", GROUP_ID, 999, NOW) == service.state_message(
        "not_found", GROUP_ID, 999, NOW
    )
    assert "7" in service.state_message("clear_success", GROUP_ID, 999, NOW, deleted=7)


def test_today_wife_plugin_has_no_inline_fixed_player_response() -> None:
    plugin_path = Path(__file__).parents[1] / "bot" / "plugins" / "today_wife.py"
    tree = ast.parse(plugin_path.read_text(encoding="utf-8"))
    fixed_responses: list[str] = []
    used_state_kinds: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr == "finish" and node.args:
            if isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                fixed_responses.append(node.args[0].value)
        if isinstance(node.func, ast.Attribute) and node.func.attr == "state_message" and node.args:
            if isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                used_state_kinds.add(node.args[0].value)

    assert fixed_responses == []
    assert used_state_kinds <= set(STATE_MESSAGE_POOLS)
    assert "locked" in used_state_kinds
