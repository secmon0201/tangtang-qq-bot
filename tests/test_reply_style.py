from __future__ import annotations

import sqlite3

import pytest

from bot.services.reply_style import expression_counts, repetition_reminder
from bot.services.tangtang_db import TangtangDb


@pytest.mark.parametrize('text,threshold,label', [
    ('唔，今天不错。', 3, '语气词开场'),
    ('今天——挺好。', 2, '破折号'),
    ('今天……还行。', 3, '省略号'),
    ('这个我收下了。', 2, '收下式回应'),
    ('我就当没听到。', 2, '假装没看见或没听见'),
])
def test_reminder_thresholds(text, threshold, label):
    assert repetition_reminder([text] * (threshold - 1)) == ''
    assert label in repetition_reminder([text] * threshold)


def test_counts_are_per_turn_and_reminder_never_copies_source():
    assert repetition_reminder([]) == ''
    texts = ['唔，PRIVATE_SOURCE——……这份收下，就当没看到。'] * 8
    assert expression_counts(texts) == dict(reply_count=8, wu_opening=8, dash=8,
                                           ellipsis=8, acceptance=8, dismissal=8)
    reminder = repetition_reminder(texts)
    assert 'PRIVATE_SOURCE' not in reminder
    assert len(reminder) <= 200
    assert expression_counts(['——重复——'])['dash'] == 1


def add_reply(db, text, *, group=1001, user=2001, kind='model',
              timestamp='2026-09-22T10:00:00+08:00', delivered=None):
    call_id = db.insert_call(group_id=group, user_id=user, message_id='', call_text='test',
                            reply_text=text, reply_kind=kind, mode='d', created_at=timestamp)
    if delivered is not None:
        db.insert_reply_parts(call_id, [{'part_index': 0, 'text': text, 'delivered': delivered}],
                              created_at=timestamp)
    return call_id


def test_style_history_filters_before_limit_and_keeps_confirmed_legacy(tmp_path):
    db = TangtangDb(tmp_path/'history.db')
    for index in range(10):
        add_reply(db, f'valid-{index}', kind='proactive' if index == 9 else 'model', delivered=True)
    add_reply(db, 'legacy confirmed')
    add_reply(db, 'too old', timestamp='2026-09-21T09:59:59+08:00')
    add_reply(db, 'future', timestamp='2026-09-22T10:00:01+08:00')
    add_reply(db, 'other group', group=1002)
    add_reply(db, 'blocked', user=2002)
    add_reply(db, 'failed', delivered=False)
    add_reply(db, 'fixed', kind='canned')
    add_reply(db, 'feature', kind='feature')
    db.blocked_users = lambda _: frozenset({2002})
    assert db.recent_style_replies(1001, now='2026-09-22T10:00:00+08:00') == (
        'legacy confirmed', *(f'valid-{i}' for i in range(9, 2, -1)))


def test_style_history_time_boundary_and_persona_database_isolation(tmp_path):
    denia = TangtangDb(tmp_path/'denia.db')
    other = TangtangDb(tmp_path/'other.db')
    add_reply(denia, 'boundary', timestamp='2026-09-21T02:00:00+00:00')
    add_reply(other, 'different persona')
    assert denia.recent_style_replies(1001, now='2026-09-22T10:00:00+08:00') == ('boundary',)
    assert denia.recent_style_replies(1002, now='2026-09-22T10:00:00+08:00') == ()


def test_style_history_never_initializes_missing_database(tmp_path):
    path = tmp_path/'missing.db'
    with pytest.raises(sqlite3.OperationalError):
        TangtangDb(path).recent_style_replies(1001, now='2026-09-22T10:00:00+08:00')
    assert not path.exists()
