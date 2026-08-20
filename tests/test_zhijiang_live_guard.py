import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from bot.db import Database
from bot.services.passive_settings import PassiveSettingsStore
import bot.services.zhijiang_live_guard as live_guard_module
from bot.services.zhijiang_live_guard import LiveSchedule, ZhijiangLiveGuard


TIMEZONE = ZoneInfo("Asia/Shanghai")
SOURCE_URL = "https://example.test/base-schedules.json"


def make_guard(tmp_path) -> tuple[Database, PassiveSettingsStore, ZhijiangLiveGuard]:
    db = Database(tmp_path / "bot.db")
    scopes = PassiveSettingsStore(db)
    return (
        db,
        scopes,
        ZhijiangLiveGuard(
            db,
            scopes,
            enabled=True,
            source_url=SOURCE_URL,
            timezone="Asia/Shanghai",
            pause_minutes=60,
            lookahead_days=7,
        ),
    )


def entry(event_id: str, starts_at: datetime) -> LiveSchedule:
    return LiveSchedule(
        event_id,
        starts_at,
        "嘉然",
        "测试直播",
        "https://live.bilibili.com/22637261",
        "日常",
    )


def test_live_guard_closes_games_and_extends_from_the_last_started_live(tmp_path):
    _db, scopes, guard = make_guard(tmp_path)
    first_start = datetime(2026, 7, 25, 20, 0, tzinfo=TIMEZONE)
    second_start = first_start + timedelta(minutes=30)
    first, second = entry("first", first_start), entry("second", second_start)
    guard.entries = (first, second)

    assert guard.apply_due(first_start) == (first,)
    assert not scopes.is_game_globally_enabled()

    assert guard.apply_due(second_start) == (second,)
    assert not scopes.is_game_globally_enabled()

    guard.tick(second_start + timedelta(minutes=59, seconds=59))
    assert not scopes.is_game_globally_enabled()
    guard.tick(second_start + timedelta(hours=1))
    assert scopes.is_game_globally_enabled()


def test_live_guard_respects_manual_open_until_a_new_schedule_starts(tmp_path):
    _db, scopes, guard = make_guard(tmp_path)
    starts_at = datetime(2026, 7, 25, 20, 0, tzinfo=TIMEZONE)
    guard.entries = (entry("first", starts_at),)

    guard.apply_due(starts_at)
    assert not scopes.is_game_globally_enabled()
    scopes.set_game_globally_enabled(True)

    guard.tick(starts_at + timedelta(minutes=15))
    assert scopes.is_game_globally_enabled()


def test_active_entries_include_all_streams_started_in_the_current_pause_window(tmp_path):
    _db, _scopes, guard = make_guard(tmp_path)
    first_start = datetime(2026, 7, 25, 20, 0, tzinfo=TIMEZONE)
    second_start = first_start + timedelta(minutes=30)
    first, second = entry("first", first_start), entry("second", second_start)
    guard.entries = (first, second)

    guard.apply_due(first_start)
    guard.apply_due(second_start)

    assert guard.active_entries(second_start + timedelta(minutes=15)) == (first, second)
    assert guard.active_entries(second_start + timedelta(hours=1)) == ()


def test_live_guard_keeps_cache_and_seen_events_across_restarts(tmp_path):
    db, scopes, guard = make_guard(tmp_path)
    starts_at = datetime(2026, 7, 25, 20, 0, tzinfo=TIMEZONE)
    known = entry("first", starts_at)
    guard._write_entries((known,))
    guard.apply_due(starts_at)

    restored = ZhijiangLiveGuard(
        db,
        scopes,
        enabled=True,
        source_url=SOURCE_URL,
        timezone="Asia/Shanghai",
    )
    assert restored.entries == (known,)
    scopes.set_game_globally_enabled(True)
    assert restored.apply_due(starts_at + timedelta(minutes=10)) == ()
    assert scopes.is_game_globally_enabled()


def test_schedule_payload_only_accepts_current_bilibili_live_rooms(tmp_path):
    _db, _scopes, guard = make_guard(tmp_path)
    now = datetime(2026, 7, 25, 12, 0, tzinfo=TIMEZONE)
    payload = {
        "schedules": [
            {
                "id": "good",
                "date": "2026/07/25",
                "time": "13:05",
                "category": "嘉然",
                "title": "正常直播",
                "type": "日常",
                "liveRoomUrl": "https://live.bilibili.com/22637261",
            },
            {
                "id": "external",
                "date": "2026/07/25",
                "time": "13:05",
                "category": "A-SOUL",
                "title": "线下活动",
                "liveRoomUrl": "https://space.bilibili.com/703007996",
            },
            {
                "id": "too-far",
                "date": "2026/08/03",
                "time": "13:05",
                "category": "嘉然",
                "title": "八天后的直播",
                "liveRoomUrl": "https://live.bilibili.com/22637261",
            },
        ]
    }
    schedules = guard._normalise_entries(payload, now)
    assert [schedule.event_id for schedule in schedules] == ["good"]
    assert schedules[0].starts_at == datetime(2026, 7, 25, 13, 5, tzinfo=TIMEZONE)


def test_refresh_uses_json_source_etag_and_persists_filtered_schedule(monkeypatch, tmp_path):
    db, _scopes, guard = make_guard(tmp_path)
    now = datetime(2026, 7, 25, 12, 0, tzinfo=TIMEZONE)
    request_headers = {}

    class Response:
        status_code = 200
        headers = {"ETag": '"schedule-v1"'}

        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {
                "schedules": [
                    {
                        "id": "good",
                        "date": "2026/07/25",
                        "time": "13:05",
                        "category": "嘉然",
                        "title": "正常直播",
                        "liveRoomUrl": "https://live.bilibili.com/22637261",
                    },
                    {
                        "id": "not-live",
                        "date": "2026/07/25",
                        "time": "13:05",
                        "category": "嘉然",
                        "title": "不应触发",
                        "liveRoomUrl": "https://space.bilibili.com/672328094",
                    },
                ]
            }

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, _url, *, headers):
            request_headers.update(headers)
            return Response()

    monkeypatch.setattr(live_guard_module.httpx, "AsyncClient", Client)
    schedules = asyncio.run(guard.refresh(now))

    assert [schedule.event_id for schedule in schedules] == ["good"]
    assert request_headers["Cache-Control"] == "no-cache"
    values = db.passive_settings()
    assert values["zhijiang_live_schedule_etag"] == '"schedule-v1"'
    assert '"good"' in values["zhijiang_live_schedule_cache"]
