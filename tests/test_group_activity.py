from datetime import date

import pytest

from bot.db import Database
from bot.services.group_activity import ActivityResponseError, parse_activity_payload


def test_parse_activity_windows_and_stable_top_order():
    windows = parse_activity_payload(
        {
            "data": {
                "yesterday": {
                    "active_member_count": 2,
                    "members": [
                        {"uin": 9, "nickname": "B", "message_count": 3},
                        {"uin": 7, "nickname": "A", "message_count": 3},
                    ],
                },
                "seven_days": {
                    "active_count": 3,
                    "member_list": [{"user_id": 7, "nick": "A", "count": 12}],
                },
            }
        }
    )

    assert [window.name for window in windows] == ["yesterday", "seven_days"]
    assert windows[0].active_member_count == 2
    assert [row["user_id"] for row in windows[0].members] == [7, 9]
    assert windows[1].members[0]["activity_count"] == 12


def test_parse_activity_requires_both_windows():
    with pytest.raises(ActivityResponseError):
        parse_activity_payload({"yesterday": {"members": []}})


def test_activity_snapshot_replaces_rows_without_duplicates(tmp_path):
    db = Database(tmp_path / "bot.db")
    db.configure_groups((1001,))
    target = date(2026, 7, 16)
    db.save_activity_snapshot(
        1001,
        target,
        "yesterday",
        2,
        [{"user_id": 7, "nickname": "A", "activity_count": 5}],
        "test",
    )
    db.save_activity_snapshot(
        1001,
        target,
        "yesterday",
        1,
        [{"user_id": 8, "nickname": "B", "activity_count": 9}],
        "test",
    )

    snapshot = db.activity_snapshot(1001, target, "yesterday")
    rows = db.activity_rows(1001, target, "yesterday")
    assert snapshot["active_member_count"] == 1
    assert [row["user_id"] for row in rows] == [8]
    assert rows[0]["message_count"] == 9
