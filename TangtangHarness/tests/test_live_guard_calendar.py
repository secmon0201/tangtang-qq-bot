from datetime import datetime, timedelta
import json
from zoneinfo import ZoneInfo

import httpx
import pytest

from tangtang_harness.business.asoul import ASoulService
from tangtang_harness.business.db import Database
from tangtang_harness.business.passive_settings import PassiveSettingsStore
from tangtang_harness.business.zhijiang_live_guard import (
    CACHE_KEY, LAST_ERROR_KEY, LAST_REFRESH_KEY, PAUSED_UNTIL_KEY, ZhijiangLiveGuard,
)
from tangtang_harness.store import Store


TIMEZONE = ZoneInfo('Asia/Shanghai')
NOW = datetime(2030, 1, 1, 16, 0, tzinfo=TIMEZONE)
ICS = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:synthetic-live@example.invalid
DTSTART:20300101T090000Z
SUMMARY:【游戏】嘉然直播: 今晚轻松游戏
DESCRIPTION:嘉然\\n直播间：https://live.bilibili.com/1
 01
STATUS:CONFIRMED
URL:https://live.bilibili.com/101
END:VEVENT
BEGIN:VEVENT
UID:cancelled@example.invalid
DTSTART:20300101T100000Z
SUMMARY:贝拉直播: 取消的歌会
STATUS:CANCELLED
URL:https://live.bilibili.com/102
END:VEVENT
BEGIN:VEVENT
UID:future@example.invalid
DTSTART:20300201T100000Z
SUMMARY:乃琳直播: 下个月
URL:https://live.bilibili.com/103
END:VEVENT
END:VCALENDAR
"""


def guard_fixture(tmp_path, source='https://example.invalid/calendar.ics'):
    store = Store(tmp_path)
    db = Database(tmp_path / 'guard.db')
    guard = ZhijiangLiveGuard(db, PassiveSettingsStore(store), enabled=True, source_url=source,
                              timezone='Asia/Shanghai', pause_minutes=60, lookahead_days=7)
    return db, store, guard


@pytest.mark.asyncio
@pytest.mark.parametrize('source,content_type', [
    ('https://example.invalid/calendar.ics', 'application/octet-stream'),
    ('https://example.invalid/feed', 'text/calendar; charset=utf-8'),
    ('https://example.invalid/feed', 'text/plain'),
])
async def test_guard_refreshes_calendar_with_stable_identity_utc_time_and_member_semantics(tmp_path, monkeypatch, source, content_type):
    db, store, guard = guard_fixture(tmp_path, source)
    db.set_passive_setting(PAUSED_UNTIL_KEY, 'existing-state')
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, content=ICS.encode(), headers={'Content-Type': content_type, 'ETag': 'calendar-v1'})

    client_class = httpx.AsyncClient
    monkeypatch.setattr('tangtang_harness.business.zhijiang_live_guard.httpx.AsyncClient',
                        lambda **kwargs: client_class(transport=httpx.MockTransport(respond), **kwargs))
    entries = await guard.refresh(NOW)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.event_id == 'ics:synthetic-live@example.invalid'
    assert entry.starts_at == NOW + timedelta(hours=1)
    assert entry.category == '嘉然' and entry.title == '今晚轻松游戏' and entry.event_type == '游戏'
    assert entry.live_room_url == 'https://live.bilibili.com/101'
    settings = db.passive_settings()
    assert settings[LAST_ERROR_KEY] == '' and settings[LAST_REFRESH_KEY] == NOW.isoformat()
    assert settings[PAUSED_UNTIL_KEY] == 'existing-state' and store.get_setting('mini_games_enabled') is None
    assert len(requests) == 1 and requests[0].headers['Accept'] == 'application/json, text/calendar'
    assert entry.as_cache() in json.loads(settings[CACHE_KEY])


@pytest.mark.asyncio
async def test_ics_conversion_keeps_existing_calendar_highlight_keys_and_combined_hosts(tmp_path):
    db, store, guard = guard_fixture(tmp_path)
    source = ICS.replace('嘉然直播: 今晚轻松游戏', '心宜思诺直播: 今晚轻松游戏')
    calendar = ASoulService(db)

    async def events():
        return calendar._parse_ics(source)

    calendar._calendar_events = events
    item = (await calendar.schedule_for_day(NOW.date()))[0]
    calendar.set_highlight(item, '粉色')
    entries = guard._normalise_ics(source, NOW)
    refreshed = (await calendar.schedule_for_day(NOW.date()))[0]
    assert entries[0].title == item.content and entries[0].event_type == item.label
    assert '心宜' in entries[0].category and '思诺' in entries[0].category
    assert refreshed.key == item.key and refreshed.highlighted and refreshed.highlight_style == '粉色'


def test_ics_room_can_come_from_folded_description_and_unknown_room_is_excluded(tmp_path):
    db, store, guard = guard_fixture(tmp_path)
    source = ICS.replace('URL:https://live.bilibili.com/101', 'URL:https://example.invalid/schedule')
    assert guard._normalise_ics(source, NOW)[0].live_room_url == 'https://live.bilibili.com/101'
    source = source.replace('live.bilibili.com/1\n 01', 'example.invalid/no-room')
    assert guard._normalise_ics(source, NOW) == ()


@pytest.mark.asyncio
async def test_calendar_failure_keeps_true_last_refresh_and_previous_cache(tmp_path, monkeypatch):
    db, store, guard = guard_fixture(tmp_path)
    bodies = iter([ICS, '<html>source unavailable</html>'])

    def respond(request):
        return httpx.Response(200, text=next(bodies), headers={'Content-Type': 'text/html'})

    client_class = httpx.AsyncClient
    monkeypatch.setattr('tangtang_harness.business.zhijiang_live_guard.httpx.AsyncClient',
                        lambda **kwargs: client_class(transport=httpx.MockTransport(respond), **kwargs))
    first = await guard.refresh(NOW)
    with pytest.raises(RuntimeError, match='iCalendar'):
        await guard.refresh(NOW + timedelta(minutes=5))
    assert guard.entries == first
    settings = db.passive_settings()
    assert settings[LAST_REFRESH_KEY] == NOW.isoformat() and settings[LAST_ERROR_KEY]


@pytest.mark.asyncio
async def test_guard_json_protocol_and_conditional_refresh_remain_compatible(tmp_path, monkeypatch):
    db, store, guard = guard_fixture(tmp_path, 'https://example.invalid/schedules.json')
    requests = []

    def respond(request):
        requests.append(request)
        if len(requests) == 2:
            return httpx.Response(304)
        return httpx.Response(200, json={'schedules': [dict(id='json-source', date='2030/01/01', time='17:00',
            category='嘉然', title='原JSON直播', liveRoomUrl='https://live.bilibili.com/101', type='游戏')]},
            headers={'ETag': 'json-v1'})

    client_class = httpx.AsyncClient
    monkeypatch.setattr('tangtang_harness.business.zhijiang_live_guard.httpx.AsyncClient',
                        lambda **kwargs: client_class(transport=httpx.MockTransport(respond), **kwargs))
    first = await guard.refresh(NOW)
    assert first[0].event_id == 'json-source'
    assert await guard.refresh(NOW + timedelta(minutes=5)) == first
    assert requests[1].headers['If-None-Match'] == 'json-v1'


@pytest.mark.asyncio
async def test_github_contents_source_requests_original_raw_json_not_file_metadata(tmp_path, monkeypatch):
    source = 'https://api.github.com/repos/synthetic/calendar/contents/base-schedules.json?ref=main'
    db, store, guard = guard_fixture(tmp_path, source)
    requests = []

    def respond(request):
        requests.append(request)
        assert request.headers['Accept'] == 'application/vnd.github.raw+json'
        return httpx.Response(200, json={'schedules': [dict(id='same-original-id', date='2030/01/01', time='17:00',
            category='嘉然', title='原始数据', liveRoomUrl='https://live.bilibili.com/101', type='游戏')]},
            headers={'Content-Type': 'application/vnd.github.raw+json; charset=utf-8'})

    client_class = httpx.AsyncClient
    monkeypatch.setattr('tangtang_harness.business.zhijiang_live_guard.httpx.AsyncClient',
                        lambda **kwargs: client_class(transport=httpx.MockTransport(respond), **kwargs))
    assert (await guard.refresh(NOW))[0].event_id == 'same-original-id'
    assert len(requests) == 1
