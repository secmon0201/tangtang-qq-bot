from datetime import datetime
import json

import pytest

from bot.services.tangtang_db import TangtangDb
from scripts.report_reply_style import build_report
from tests.test_reply_style import add_reply


def test_report_uses_delivered_turns_and_disjoint_modes_without_private_text(tmp_path):
    db = TangtangDb(tmp_path/'history.db')
    add_reply(db, '唔，PRIVATE——……', delivered=True)
    add_reply(db, '主动回复', kind='proactive', delivered=True)
    call_id = add_reply(db, '续聊', delivered=True)
    with db._connect() as conn:
        conn.execute("UPDATE tangtang_calls SET mode='continuation' WHERE id=?", (call_id,))
    add_reply(db, '失败不计', delivered=False)
    add_reply(db, '功能不计', kind='feature')
    add_reply(db, '边界不计', timestamp='2026-09-23T00:00:00+08:00')
    result = build_report(db.path, since=datetime.fromisoformat('2026-09-22T00:00:00+08:00'),
                          until=datetime.fromisoformat('2026-09-23T00:00:00+08:00'))
    assert result['overall']['reply_count'] == 3
    assert sum(mode['reply_count'] for mode in result['by_mode'].values()) == 3
    assert all(result['by_mode'][key]['reply_count'] == 1 for key in ('call', 'continuation', 'proactive'))
    assert result['overall']['patterns']['wu_opening'] == {'count': 1, 'percent': 33.33}
    assert result['observation']['frequency_targets_met'] is None
    assert not result['observation']['eligible']
    assert 'PRIVATE' not in json.dumps(result)
    assert '2001' not in json.dumps(result)


@pytest.mark.parametrize('hours', [23, 24])
def test_report_waits_for_both_time_and_volume_without_claiming_naturalness(tmp_path, hours):
    from datetime import timedelta
    db = TangtangDb(tmp_path/'history.db')
    add_reply(db, '普通回复')
    with db._connect() as conn:
        conn.executemany(
            "INSERT INTO tangtang_calls(group_id,user_id,reply_text,reply_kind,mode,call_text,created_at) "
            "VALUES(1001,2001,'普通回复','model','d','test','2026-09-22T10:00:00+08:00')",
            [()]*199,
        )
    since = datetime.fromisoformat('2026-09-22T00:00:00+08:00')
    result = build_report(db.path, since=since, until=since+timedelta(hours=hours))
    assert result['observation']['eligible'] == (hours == 24)
    assert result['observation']['frequency_targets_met'] is (True if hours == 24 else None)
    assert result['observation']['qualitative_review'].startswith('pending_')
