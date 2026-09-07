from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.db import Database
from bot.services.a_coast_archive import ACoastArchiveService, bounded_evidence
from bot.services.tangtang_db import TangtangDb


ZONE = ZoneInfo("Asia/Shanghai")


def test_bounded_evidence_truncates_and_drops_empty():
    results = ["", "12345", "abcdefghijklmnopqrstuvwxyz", ""]
    assert bounded_evidence(results, 10) == ["12345", "abcdefghij"]


def _make_store(tmp_path: Path) -> tuple[Database, TangtangDb, ACoastArchiveService]:
    db = Database(tmp_path / "bot.db")
    db.configure_groups((1001, 1002))
    tangtang_db = TangtangDb(tmp_path / "tangtang.db")
    service = ACoastArchiveService(db, tangtang_db=tangtang_db)
    return db, tangtang_db, service


def _insert(
    tangtang_db: TangtangDb,
    *,
    group_id: int,
    user_id: int,
    text: str,
    message_id: str,
    minute: int,
) -> None:
    created_at = datetime(2026, 8, 7, 12, minute, tzinfo=ZONE).isoformat()
    tangtang_db.insert_group_message(
        group_id=group_id,
        user_id=user_id,
        nickname=f"用户{user_id}",
        text=text,
        message_id=message_id,
        created_at=created_at,
    )


def test_records_filter_by_user_group_keyword_and_page(tmp_path):
    _db, tangtang_db, service = _make_store(tmp_path)
    _insert(tangtang_db, group_id=1001, user_id=7, text="嘉然", message_id="m1", minute=1)
    _insert(tangtang_db, group_id=1002, user_id=7, text="嘉然今天直播", message_id="m2", minute=2)
    _insert(tangtang_db, group_id=1002, user_id=8, text="无关内容", message_id="m3", minute=3)

    rows = service.records(7, (1002,), keyword="嘉然")
    assert [row["content"] for row in rows] == ["嘉然今天直播"]
    assert rows[0]["group_id"] == 1002
    assert service.records(7, (1001, 1002), page=1) != []
    assert service.records(7, (1001, 1002), page=2) == []


def test_total_pages_uses_matching_count(tmp_path):
    _db, tangtang_db, service = _make_store(tmp_path)
    for index in range(201):
        _insert(
            tangtang_db,
            group_id=1001,
            user_id=7,
            text=f"消息{index}",
            message_id=f"m{index}",
            minute=index % 60,
        )
    assert service.total_pages(7, (1001,), keyword="消息") == 3


def test_summary_counts_groups_hours_and_repeats(tmp_path):
    _db, tangtang_db, service = _make_store(tmp_path)
    _insert(tangtang_db, group_id=1001, user_id=7, text="重复内容", message_id="s1", minute=10)
    _insert(tangtang_db, group_id=1001, user_id=7, text="重复内容", message_id="s2", minute=10)
    _insert(tangtang_db, group_id=1002, user_id=7, text="单独一条", message_id="s3", minute=20)

    summary = service.summary(7, (1001, 1002))
    assert summary["message_count"] == 3
    assert len(summary["groups"]) == 2
    assert any(row["hour"] == 12 for row in summary["hours"])
    assert any(row["content"] == "重复内容" and row["message_count"] == 2 for row in summary["repeats"])


def test_consumption_marks_each_source_message_only_once(tmp_path):
    _db, tangtang_db, service = _make_store(tmp_path)
    _insert(tangtang_db, group_id=1001, user_id=7, text="第一条用于增量画像", message_id="c1", minute=1)
    _insert(tangtang_db, group_id=1001, user_id=7, text="第二条用于增量画像", message_id="c2", minute=2)

    pending = service.unconsumed(7, (1001,))
    assert [row["id"] for row in pending] == sorted(row["id"] for row in pending)
    assert len(pending) == 2
    assert service.consume(7, pending) == 2
    assert service.unconsumed(7, (1001,)) == []


def test_only_registered_group_members_can_request_profile(monkeypatch):
    import nonebot

    nonebot.init()
    from bot.plugins import a_coast_archive

    class GroupEvent:
        def __init__(self, group_id: int) -> None:
            self.group_id = group_id
            self.user_id = 123456

    monkeypatch.setattr(a_coast_archive, "GroupMessageEvent", GroupEvent)
    monkeypatch.setattr(
        a_coast_archive,
        "domains",
        type(
            "Domains",
            (),
            {"domain_for_group": staticmethod(lambda group_id: object() if group_id == 910000101 else None)},
        )(),
    )

    assert a_coast_archive.can_request_profile(GroupEvent(910000101))
    assert not a_coast_archive.can_request_profile(GroupEvent(1))


def test_profile_target_accepts_one_qq_number_or_one_at_mention():
    import nonebot
    from nonebot.adapters.onebot.v11 import Message, MessageSegment

    nonebot.init()
    from bot.plugins import a_coast_archive

    assert a_coast_archive.profile_target_from_args(Message("903848042")) == 903848042
    assert a_coast_archive.profile_target_from_args(
        Message([MessageSegment.text(" "), MessageSegment.at("903848042")])
    ) == 903848042


def test_profile_target_rejects_extra_text_multiple_mentions_and_non_text_segments():
    import nonebot
    from nonebot.adapters.onebot.v11 import Message, MessageSegment

    nonebot.init()
    from bot.plugins import a_coast_archive

    assert a_coast_archive.profile_target_from_args(
        Message([MessageSegment.at("903848042"), MessageSegment.text(" 看看")])
    ) is None
    assert a_coast_archive.profile_target_from_args(
        Message([MessageSegment.at("903848042"), MessageSegment.at("123456789")])
    ) is None
    assert a_coast_archive.profile_target_from_args(
        Message([MessageSegment.at("903848042"), MessageSegment.image("example.png")])
    ) is None


def test_profile_requires_more_than_one_hundred_archived_messages():
    import nonebot

    nonebot.init()
    from bot.plugins import a_coast_archive

    assert not a_coast_archive.has_enough_profile_messages(100)
    assert a_coast_archive.has_enough_profile_messages(101)
