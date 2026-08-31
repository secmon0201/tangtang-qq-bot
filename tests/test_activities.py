import asyncio
import sqlite3
from datetime import date
from types import SimpleNamespace

from PIL import Image, ImageColor

from bot.config import settings
from bot.config import managed_group_order
from bot.db import Database
from bot.services.activities import (
    ACTIVITY_LOTTERY,
    ActivityService,
    parse_group_ids,
    parse_create_payload,
    parse_activity_id,
    parse_update_payload,
)
import bot.services.activities as activities_module
import bot.services.roles as roles_module
from bot.services.reports import ReportRenderer


def make_db(tmp_path):
    db = Database(tmp_path / "bot.db")
    db.configure_groups((1001, 1002, 1003))
    return db


def test_activity_ids_start_at_500_and_do_not_rewind_existing_ids(tmp_path):
    db = make_db(tmp_path)
    first = db.create_activity(
        "编号测试",
        "",
        "announcement",
        "2026-07-20T12:00:00+00:00",
        "2026-07-20T14:00:00+00:00",
        99,
        1001,
        (1001,),
        (),
        "创建 {activity_id}",
    )
    assert first == 500

    Database(tmp_path / "bot.db")
    second = db.create_activity(
        "连续编号",
        "",
        "announcement",
        "2026-07-20T12:00:00+00:00",
        "2026-07-20T14:00:00+00:00",
        99,
        1001,
        (1001,),
        (),
        "创建 {activity_id}",
    )
    assert second == 501


def test_activity_payload_requires_fixed_times_and_lottery_prizes():
    payload = parse_create_payload(
        "周末抽奖 | 2026-07-20 20:00 | 2026-07-20 22:00 | 1001,1002 | 抽奖 | 测试活动 | 一等奖=1;二等奖=2"
    )
    assert payload["activity_type"] == ACTIVITY_LOTTERY
    assert payload["prizes"] == (("一等奖", 1), ("二等奖", 2))


def test_activity_payload_supports_public_or_masked_participation():
    public = parse_create_payload(
        "公开活动 | 2026-07-20 20:00 | 2026-07-20 22:00 | 1001,1002 | 通报 | 测试 | 无 | 公开"
    )
    masked = parse_create_payload(
        "默认脱敏 | 2026-07-20 20:00 | 2026-07-20 22:00 | 1001,1002 | 通报 | 测试"
    )
    assert public["visibility"] == "public"
    assert masked["visibility"] == "masked"


def test_activity_payload_supports_readable_labeled_lines():
    payload = parse_create_payload(
        """活动名：这是一个测试活动
开始时间：2026-7-26 18:30
结束时间：2026-7-26 20:30
类型：抽奖
说明：欢迎参加
奖项：一等奖=1；二等奖=2
参与群：1001,1002
隐私：公开"""
    )
    assert payload["title"] == "这是一个测试活动"
    assert payload["activity_type"] == ACTIVITY_LOTTERY
    assert payload["groups_raw"] == "1001,1002"
    assert payload["visibility"] == "public"
    assert payload["prizes"] == (("一等奖", 1), ("二等奖", 2))


def test_activity_parse_group_ids_default_uses_managed_order(monkeypatch):
    class OrderedSettings:
        managed_group_ids = (1001, 1002, 1003)

        def managed_order(self, group_ids):
            return managed_group_order(group_ids, self.managed_group_ids)

    monkeypatch.setattr(activities_module, "settings", OrderedSettings())
    assert parse_group_ids("", (1003, 1001, 1002)) == (1001, 1002, 1003)
    assert parse_group_ids("1003,1001", (1001, 1002, 1003)) == (1003, 1001)


def test_activity_update_parser_supports_zero_as_unchanged():
    activity_id, payload = parse_update_payload(
        "301 | 新标题 | 0 | 0 | 新说明 | 1001,1002 | 抽奖 | 一等奖=1;二等奖=2 | 公开"
    )
    assert activity_id == 301
    assert payload["title"] == "新标题"
    assert payload["starts_at"] is None
    assert payload["ends_at"] is None
    assert payload["description"] == "新说明"
    assert payload["groups_raw"] == "1001,1002"
    assert payload["activity_type"] == ACTIVITY_LOTTERY
    assert payload["prizes"] == (("一等奖", 1), ("二等奖", 2))
    assert payload["visibility"] == "public"


def test_activity_update_parser_ignores_blank_labeled_fields():
    activity_id, payload = parse_update_payload(
        """活动ID：517
活动名：
开始时间：
结束时间：2026-07-26 20:30
说明：
参与群：
类型：
奖项：
隐私：公开"""
    )
    assert activity_id == 517
    assert payload["title"] is None
    assert payload["starts_at"] is None
    assert payload["ends_at"] is not None
    assert payload["description"] is None
    assert payload["groups_raw"] is None
    assert payload["activity_type"] is None
    assert payload["prizes"] is None
    assert payload["visibility"] == "public"


def test_activity_id_parser_accepts_compact_and_common_separators():
    assert parse_activity_id("517") == 517
    assert parse_activity_id(" 517 ") == 517
    assert parse_activity_id("：517") == 517
    assert parse_activity_id("#ID:517") == 517
    assert parse_activity_id("报名") is None


def test_activity_update_changes_groups_type_prizes_and_broadcasts(monkeypatch, tmp_path):
    role_settings = SimpleNamespace(
        operator_ids=frozenset({99}), activity_admin_ids=frozenset(), command_prefix="#"
    )
    monkeypatch.setattr(activities_module, "settings", role_settings)
    monkeypatch.setattr(roles_module, "settings", role_settings)
    db = make_db(tmp_path)
    activity_id = db.create_activity(
        "旧活动",
        "旧说明",
        "announcement",
        "2026-07-20T12:00:00+00:00",
        "2026-07-20T14:00:00+00:00",
        99,
        1001,
        (1001, 1002),
        (),
        "活动 {activity_id}",
    )
    service = ActivityService(db, lambda: (1001, 1002, 1003))
    result = service.update(
        SimpleNamespace(user_id=99),
        activity_id,
        {
            "title": "新活动",
            "starts_at": None,
            "ends_at": None,
            "description": None,
            "groups_raw": "1002,1003",
            "activity_type": ACTIVITY_LOTTERY,
            "prizes": (("一等奖", 1), ("二等奖", 2)),
            "visibility": "public",
        },
    )
    assert result == {
        "old_group_ids": (1001, 1002),
        "new_group_ids": (1002, 1003),
        "removed_group_ids": (1001,),
    }
    activity = db.activity(activity_id)
    assert activity["title"] == "新活动"
    assert activity["activity_type"] == ACTIVITY_LOTTERY
    assert activity["visibility"] == "public"
    assert [row["group_id"] for row in db.activity_groups(activity_id)] == [1002, 1003]
    assert [(row["prize_name"], row["quantity"]) for row in db.activity_prizes(activity_id)] == [
        ("一等奖", 1),
        ("二等奖", 2),
    ]
    pending = db.pending_activity_broadcasts()
    assert {int(row["group_id"]) for row in pending} == {1001, 1002, 1003}
    assert any(str(row["kind"]).startswith("unsupported:") and int(row["group_id"]) == 1001 for row in pending)
    assert all(
        str(row["kind"]).startswith("updated:")
        for row in pending
        if int(row["group_id"]) in {1002, 1003}
    )


def test_only_super_admin_can_update_an_active_activity(monkeypatch, tmp_path):
    role_settings = SimpleNamespace(
        operator_ids=frozenset({99}), activity_admin_ids=frozenset({77}), command_prefix="#"
    )
    monkeypatch.setattr(activities_module, "settings", role_settings)
    monkeypatch.setattr(roles_module, "settings", role_settings)
    db = make_db(tmp_path)
    activity_id = db.create_activity(
        "进行中的活动",
        "旧说明",
        "announcement",
        "2026-07-20T12:00:00+00:00",
        "2026-07-20T14:00:00+00:00",
        77,
        1001,
        (1001, 1002),
        (),
        "活动 {activity_id}",
    )
    assert db.transition_activity(activity_id, "scheduled", "active", "活动已开始")
    service = ActivityService(db, lambda: (1001, 1002, 1003))
    payload = {
        "title": "更新后的活动",
        "starts_at": None,
        "ends_at": None,
        "description": None,
        "groups_raw": None,
        "activity_type": None,
        "prizes": None,
        "visibility": None,
    }

    assert service.update(SimpleNamespace(user_id=77), activity_id, payload) is None
    assert service.update(SimpleNamespace(user_id=66), activity_id, payload) is None
    assert service.update(SimpleNamespace(user_id=99), activity_id, payload) is not None
    assert db.activity(activity_id)["title"] == "更新后的活动"

    pending = db.pending_activity_broadcasts()
    assert {int(row["group_id"]) for row in pending} == {1001, 1002}
    assert all(str(row["kind"]).startswith("updated:") for row in pending)


def test_activity_visibility_is_persisted(tmp_path):
    db = make_db(tmp_path)
    activity_id = db.create_activity(
        "公开名单测试",
        "",
        "announcement",
        "2026-07-20T12:00:00+00:00",
        "2026-07-20T14:00:00+00:00",
        99,
        1001,
        (1001,),
        (),
        "活动 {activity_id}",
        visibility="public",
    )
    assert db.activity(activity_id)["visibility"] == "public"


def test_activity_removed_group_notice_is_a_local_image(tmp_path):
    path = ReportRenderer(tmp_path).render_activity_unsupported_notice("周末联动")
    assert path.exists()
    with Image.open(path) as image:
        assert image.width == ReportRenderer.WIDTH
        assert image.height > ReportRenderer.HEADER_HEIGHT
        assert image.getbbox() is not None


def test_existing_activity_database_migrates_to_masked_visibility(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE activities (
                activity_id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                activity_type TEXT NOT NULL DEFAULT 'announcement',
                status TEXT NOT NULL DEFAULT 'scheduled',
                starts_at TEXT NOT NULL,
                ends_at TEXT NOT NULL,
                creator_id INTEGER NOT NULL,
                creator_group_id INTEGER NOT NULL,
                draw_seed TEXT NOT NULL DEFAULT '',
                cancelled_reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
    db = Database(path)
    with db.connect() as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(activities)")}
    assert "visibility" in columns


def test_operator_can_create_activity_without_a_group_context(tmp_path):
    allowed_group = settings.activity_group_ids[0]
    db = Database(tmp_path / "bot.db")
    db.configure_groups((allowed_group,))
    service = ActivityService(db)
    payload = parse_create_payload(
        f"私聊创建 | 2026-07-20 20:00 | 2026-07-20 22:00 | {allowed_group} | 通报 | 测试"
    )
    activity_id = service.create(SimpleNamespace(user_id=99), payload, creator_group_id=None)
    assert db.activity(activity_id)["creator_group_id"] == 0


def test_activity_admin_can_create_without_group_role(monkeypatch, tmp_path):
    role_settings = SimpleNamespace(operator_ids=frozenset({99}), activity_admin_ids=frozenset({77}))
    monkeypatch.setattr(
        activities_module,
        "settings",
        role_settings,
    )
    monkeypatch.setattr(roles_module, "settings", role_settings)
    service = ActivityService(Database(tmp_path / "bot.db"))
    event = SimpleNamespace(user_id=77, sender=SimpleNamespace(role="member"))
    assert service.can_create(event)


def test_activity_admin_only_manages_own_activity(monkeypatch, tmp_path):
    role_settings = SimpleNamespace(operator_ids=frozenset({99}), activity_admin_ids=frozenset({77}))
    monkeypatch.setattr(
        activities_module,
        "settings",
        role_settings,
    )
    monkeypatch.setattr(roles_module, "settings", role_settings)
    service = ActivityService(Database(tmp_path / "bot.db"))
    own = {"creator_id": 77}
    other = {"creator_id": 88}
    event = SimpleNamespace(user_id=77)
    assert service.can_manage(event, own)
    assert not service.can_manage(event, other)


def test_ordinary_member_cannot_create_or_manage(monkeypatch, tmp_path):
    role_settings = SimpleNamespace(operator_ids=frozenset({99}), activity_admin_ids=frozenset({77}))
    monkeypatch.setattr(
        activities_module,
        "settings",
        role_settings,
    )
    monkeypatch.setattr(roles_module, "settings", role_settings)
    service = ActivityService(Database(tmp_path / "bot.db"))
    event = SimpleNamespace(user_id=66, sender=SimpleNamespace(role="member"))
    assert not service.can_create(event)
    assert not service.can_manage(event, {"creator_id": 77})


def test_activity_group_owner_or_admin_defaults_to_activity_admin(monkeypatch, tmp_path):
    role_settings = SimpleNamespace(
        operator_ids=frozenset({99}),
        activity_admin_ids=frozenset(),
        activity_admin_blacklist_ids=frozenset({88}),
    )
    monkeypatch.setattr(
        activities_module,
        "settings",
        role_settings,
    )
    monkeypatch.setattr(roles_module, "settings", role_settings)
    service = ActivityService(Database(tmp_path / "bot.db"), lambda: (1001,))
    for role in ("owner", "admin"):
        event = SimpleNamespace(user_id=66, group_id=1001, sender=SimpleNamespace(role=role))
        assert service.can_create(event)
        assert service.can_manage(event, {"creator_id": 66})
        assert not service.can_manage(event, {"creator_id": 77})

    outside_scope = SimpleNamespace(user_id=67, group_id=1002, sender=SimpleNamespace(role="admin"))
    blacklisted = SimpleNamespace(user_id=88, group_id=1001, sender=SimpleNamespace(role="owner"))
    assert not service.can_create(outside_scope)
    assert not service.can_create(blacklisted)


def test_activity_admin_blacklist_overrides_configured_activity_admin(monkeypatch, tmp_path):
    role_settings = SimpleNamespace(
        operator_ids=frozenset({99}),
        activity_admin_ids=frozenset({77}),
        activity_admin_blacklist_ids=frozenset({77}),
    )
    monkeypatch.setattr(activities_module, "settings", role_settings)
    monkeypatch.setattr(roles_module, "settings", role_settings)
    service = ActivityService(Database(tmp_path / "bot.db"))
    assert not service.can_create(SimpleNamespace(user_id=77, sender=SimpleNamespace(role="member")))


def test_activity_registration_sequence_and_rejoin(tmp_path):
    db = make_db(tmp_path)
    activity_id = db.create_activity(
        "测试活动",
        "说明",
        "announcement",
        "2026-07-20T12:00:00+00:00",
        "2026-07-20T14:00:00+00:00",
        99,
        1001,
        (1001, 1002),
        (),
        "活动 {activity_id}",
    )
    first = db.register_activity(activity_id, 7, 1001, "A", "")
    second = db.register_activity(activity_id, 8, 1002, "B", "")
    duplicate = db.register_activity(activity_id, 7, 1002, "A2", "")
    assert first["registration_no"] == 1
    assert second["registration_no"] == 2
    assert duplicate["status"] == "already"
    assert db.cancel_activity_registration(activity_id, 7)
    rejoin = db.register_activity(activity_id, 7, 1002, "A3", "")
    assert rejoin["registration_no"] == 3
    assert db.activity(activity_id)["participant_count"] == 2
    assert db.pending_activity_broadcasts()[0]["message"] == f"活动 {activity_id}"


def test_lottery_is_idempotent_and_never_awards_user_twice(tmp_path):
    db = make_db(tmp_path)
    activity_id = db.create_activity(
        "抽奖",
        "",
        "lottery",
        "2026-07-20T12:00:00+00:00",
        "2026-07-20T14:00:00+00:00",
        99,
        1001,
        (1001,),
        (("一等奖", 1), ("二等奖", 2)),
        "开奖 {activity_id}",
    )
    for user_id in (7, 8, 9, 10):
        db.register_activity(activity_id, user_id, 1001, str(user_id), "")
    assert db.transition_activity(activity_id, "scheduled", "ended", "ended")
    first = db.draw_activity(activity_id)
    second = db.draw_activity(activity_id)
    assert len(first) == 3
    assert [row["user_id"] for row in first] == [row["user_id"] for row in second]
    assert len({row["user_id"] for row in first}) == 3


def test_ended_activity_participant_list_keeps_winners(tmp_path):
    db = make_db(tmp_path)
    activity_id = db.create_activity(
        "结束名单测试",
        "",
        "lottery",
        "2026-07-20T12:00:00+00:00",
        "2026-07-20T14:00:00+00:00",
        99,
        1001,
        (1001,),
        (("一等奖", 2),),
        "结束 {activity_id}",
    )
    for user_id in (7, 8, 9, 10):
        db.register_activity(activity_id, user_id, 1001, str(user_id), "")
    db.transition_activity(activity_id, "scheduled", "ended", "ended")
    winners = db.draw_activity(activity_id)

    assert len(winners) == 2
    assert len(db.activity_participants(activity_id)) == 2
    assert len(db.activity_participants(activity_id, include_winners=True)) == 4
    assert db.activity(activity_id)["participant_count"] == 4


def test_activity_service_cancel_freezes_participants(tmp_path):
    db = make_db(tmp_path)
    service = ActivityService(db)
    activity_id = db.create_activity(
        "取消测试",
        "",
        "announcement",
        "2026-07-20T12:00:00+00:00",
        "2026-07-20T14:00:00+00:00",
        99,
        1001,
        (1001,),
        (),
        "创建 {activity_id}",
    )
    db.register_activity(activity_id, 7, 1001, "A", "")
    assert service.cancel(activity_id, "时间调整")
    assert db.activity(activity_id)["status"] == "cancelled"
    assert db.activity_participants(activity_id, active_only=False)[0]["status"] == "event_cancelled"


def test_activity_reports_are_local_png(tmp_path):
    renderer = ReportRenderer(tmp_path)
    hall = renderer.render_activity_hall(
        [{
            "activity_id": 1,
            "title": "测试活动",
            "status_label": "进行中",
            "participant_count": 3,
            "group_count": 2,
            "starts_text": "2026-07-20 20:00",
            "ends_text": "2026-07-20 22:00",
        }]
    )
    detail = renderer.render_activity_detail(
        {
            "activity_id": 1,
            "title": "测试活动",
            "activity_type": "announcement",
            "status_label": "进行中",
            "starts_text": "2026-07-20 20:00",
            "ends_text": "2026-07-20 22:00",
            "participant_count": 3,
            "group_count": 2,
            "description": "说明",
        },
        "群一(1001)、群二(1002)",
        "无",
    )
    participants = renderer.render_activity_participants(
        {"activity_id": 1, "title": "测试活动", "participant_count": 1},
        [{
            "registration_no": 1,
            "display_user_id": "123****789",
            "nickname": "测试用户",
            "group_label": "其他活动群",
            "user_id": 123456789,
        }],
    )
    winner_list = renderer.render_activity_winners(
        {
            "activity_id": 500,
            "title": "测试抽奖",
            "status_label": "已结束",
        },
        [
            {
                "prize_name": "一等奖",
                "nickname": "获奖用户甲",
                "display_user_id": "123****789",
                "registration_no": 1,
                "user_id": 123456789,
            },
            {
                "prize_name": "二等奖",
                "nickname": "获奖用户乙",
                "display_user_id": "987****321",
                "registration_no": 2,
                "user_id": 987654321,
            },
        ],
    )
    help_image = renderer.render_activity_help()
    for path in (hall, detail, participants, winner_list, help_image):
        assert path.exists()
        with Image.open(path) as image:
            assert image.width == ReportRenderer.WIDTH
            assert image.getbbox() is not None
    with Image.open(detail) as image:
        assert image.getpixel((0, 0))[:3] == ImageColor.getrgb(ReportRenderer.BACKGROUND)


def test_winner_report_reserves_height_for_all_winner_rows(tmp_path):
    renderer = ReportRenderer(tmp_path)
    rows = [
        {
            "prize_name": "二等奖",
            "nickname": f"获奖成员{index}",
            "display_user_id": f"123****{index:03d}",
            "registration_no": index,
            "user_id": 100000000 + index,
        }
        for index in range(1, 17)
    ]
    report = renderer.render_activity_winners({"activity_id": 508, "title": "长名单"}, rows)
    with Image.open(report) as image:
        # Header 48 + 16 minimum 78 px winner rows plus their 8 px gaps,
        # with the report header/footer and a final safety margin.
        assert image.height >= 146 + 50 + 16 * 86 + 48 + 64


def test_activity_broadcast_uses_supplied_local_media_factory(tmp_path):
    db = make_db(tmp_path)
    activity_id = db.create_activity(
        "图片通知",
        "",
        "announcement",
        "2026-07-20T12:00:00+00:00",
        "2026-07-20T14:00:00+00:00",
        99,
        1001,
        (1001,),
        (),
        "不应作为用户通知发送的文字 {activity_id}",
    )
    sent = []

    class FakeBot:
        async def call_api(self, action, **kwargs):
            sent.append((action, kwargs))

    async def deliver():
        await ActivityService(db).deliver_broadcasts(
            FakeBot(), lambda row, _bot: {"type": "local_image", "activity_id": row["activity_id"]}
        )

    asyncio.run(deliver())
    assert sent == [
        (
            "send_group_msg",
            {"group_id": 1001, "message": {"type": "local_image", "activity_id": activity_id}},
        )
    ]
    assert db.pending_activity_broadcasts() == []


def test_activity_broadcast_can_send_a_merged_forward_message(tmp_path):
    db = make_db(tmp_path)
    db.create_activity(
        "合并转发",
        "",
        "lottery",
        "2026-07-20T12:00:00+00:00",
        "2026-07-20T14:00:00+00:00",
        99,
        1001,
        (1001,),
        (("一等奖", 1),),
        "活动 {activity_id}",
    )
    sent = []

    class FakeBot:
        async def call_api(self, action, **kwargs):
            sent.append((action, kwargs))

    asyncio.run(
        ActivityService(db).deliver_broadcasts(
            FakeBot(), lambda _row, _bot: {"_activity_forward_messages": [{"type": "node"}]}
        )
    )
    assert sent == [
        ("send_group_forward_msg", {"group_id": 1001, "messages": [{"type": "node"}]})
    ]


def test_activity_broadcast_stops_retrying_after_the_configured_limit(tmp_path):
    db = make_db(tmp_path)
    activity_id = db.create_activity(
        "重试上限",
        "",
        "announcement",
        "2026-07-20T12:00:00+00:00",
        "2026-07-20T14:00:00+00:00",
        99,
        1001,
        (1001,),
        (),
        "活动 {activity_id}",
    )

    for _ in range(3):
        db.mark_activity_broadcast_error(activity_id, 1001, "created", "timeout", 3)

    with db.connect() as connection:
        row = connection.execute(
            "SELECT status,attempts,last_error FROM activity_broadcasts WHERE activity_id=?",
            (activity_id,),
        ).fetchone()
    assert row["status"] == "failed"
    assert row["attempts"] == 3
    assert row["last_error"] == "timeout"
    assert db.pending_activity_broadcasts() == []
