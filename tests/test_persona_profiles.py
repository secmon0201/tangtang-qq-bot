"""Independent evidence review, automatic rebuild and competing updates."""
import json

import pytest

from bot.services.persona_cognition import CognitionStore
from bot.services.persona_profile_contract import validate_draft
from bot.services.persona_profile_history import rebuild_from_history
from bot.services.persona_profile_store import ProfileStore
from bot.services.persona_store import PersonaStore
from bot.services.tangtang_db import TangtangDb
from tests.test_persona_cognition import source, claim
from tests.test_persona_integration import async_test


def draft_for(s, statement='这次在认真修改画稿', scope='event'):
    return {'observations': [dict(statement=statement, scope=scope, basis='observed',
        context='本次绘画交流', evidence=[{'event_key': s['event_key'], 'quote': s['text']}])],
        'portrait': [{'text': statement, 'observations': [0]}]}


def review_for(draft, verdict='supported', previous=()):
    result = {key: [dict(index=i, verdict=verdict, reason='依据原话复核') for i in range(len(draft[field]))]
        for key, field in [('decisions', 'observations'), ('portrait_decisions', 'portrait')]}
    result['prior_decisions'] = [dict(index=i, verdict='replaced', observations=[0], reason='本人新发言明确纠正') for i in range(len(previous))]
    return result


def publish(cognition, s, statement='这次在认真修改画稿'):
    cognition.import_sources([s])
    profiles = ProfileStore(cognition)
    batch = profiles.claim()
    draft = draft_for(s, statement)
    profiles.publish(batch, draft, review_for(draft, previous=batch.previous['observations']))
    return batch


@pytest.fixture
def cognition(tmp_path):
    return CognitionStore(TangtangDb(tmp_path / 'denia.db'))


def test_unsupported_chat_impression_never_enters_profile(cognition):
    s = source(text='娅娅用语音跟大家说会晚安吧')
    snap = cognition.snapshot('turn', 201, 101, [s])
    result = cognition.merge({'claims': [claim(s, '喜欢轻松打趣')]}, snap)
    assert result.accepted == ['queued:201']
    assert not cognition.snapshot('cross', 201, 102, []).claims
    assert '喜欢轻松打趣' not in cognition.own_impression(201)
    profiles = ProfileStore(cognition)
    batch = profiles.claim()
    draft = draft_for(s, '喜欢轻松打趣')
    profiles.publish(batch, draft, review_for(draft, 'reject'))
    assert '喜欢轻松打趣' not in cognition.own_impression(201)


def test_single_message_builds_specific_cross_group_profile(cognition):
    s = source()
    publish(cognition, s)
    assert '认真修改画稿' in cognition.own_impression(201)
    assert '认真修改画稿' not in cognition.own_impression(202)
    snap = cognition.snapshot('cross', 201, 102, [])
    assert snap.claims[0]['content'] == '这次在认真修改画稿'
    assert '修改画稿' in snap.claims[0]['basis'][0]['quote']


def test_single_event_cannot_be_generalized_and_subjects_are_isolated(cognition):
    s = source()
    cognition.import_sources([s, source('102:2', user=202, text='我喜欢钓鱼')])
    profiles = ProfileStore(cognition)
    batch = profiles.claim()
    assert batch.user_id == 201 and all(s['user_id'] == 201 for s in batch.sources)
    assert '钓鱼' not in batch.prompt()
    with pytest.raises(ValueError, match='single_event'):
        validate_draft(draft_for(s, scope='person'), batch)
    bad = draft_for(s)
    bad['observations'][0]['evidence'][0]['event_key'] = '102:2'
    with pytest.raises(ValueError, match='evidence'):
        validate_draft(bad, batch)


def test_correction_reorganizes_whole_profile_and_preserves_history(cognition):
    publish(cognition, source(), '愿意在这次交流中分享画稿')
    newer = source('102:2', text='别再说我喜欢分享画稿，我只是在提问怎么修改', occurred=200)
    publish(cognition, newer, '本次只想讨论修改问题，不希望被概括为喜欢分享')
    text = cognition.own_impression(201)
    assert '不希望被概括' in text and '愿意在这次' not in text
    with cognition.connect() as conn:
        assert conn.execute('SELECT count(*) FROM persona_profile_versions').fetchone()[0] == 2
        assert conn.execute("SELECT count(*) FROM person_semantic_memory WHERE status='superseded'").fetchone()[0] == 1


def test_old_inflight_review_cannot_overwrite_new_source_revision(cognition):
    s = source(attribution='ambient')
    cognition.import_sources([s])
    profiles = ProfileStore(cognition)
    batch = profiles.claim()
    cognition.import_sources([{**s, 'revision': 2, 'attribution': 'direct'}])
    draft = draft_for(s)
    with pytest.raises(ValueError, match='source_changed'):
        profiles.publish(batch, draft, review_for(draft))
    assert not cognition.snapshot('cross', 201, 102, []).claims
    profiles.recover()
    assert profiles.claim().sources[0]['revision'] == 2


def test_new_messages_during_review_remain_pending(cognition):
    s = source()
    cognition.import_sources([s])
    profiles = ProfileStore(cognition)
    batch = profiles.claim()
    cognition.import_sources([source('101:2', text='我现在改想法了', occurred=200)])
    draft = draft_for(s)
    profiles.publish(batch, draft, review_for(draft))
    next_batch = profiles.claim()
    assert next_batch and any(s['event_key'] == '101:2' for s in next_batch.sources)
    with pytest.raises(ValueError, match='lease_changed'):
        profiles.publish(batch, draft, review_for(draft))


def test_rebuild_uses_full_history_not_legacy_label(tmp_path, cognition):
    s = source(text='说会晚安吧')
    cognition.import_sources([s])
    db = TangtangDb(tmp_path / 'raw.db')
    db.insert_group_message(group_id=101, user_id=201, nickname='甲', message_id='1',
                            text='娅娅用语音跟大家说会晚安吧', created_at='2026-09-19T00:00:00+00:00')
    central = PersonaStore(tmp_path / 'central.db')
    with cognition.connect() as conn:
        conn.execute("""INSERT INTO person_semantic_memory(user_id,scope_group,category,content,normalized,tags,status,created_at,updated_at)
            VALUES(201,0,'old','喜欢轻松打趣','喜欢轻松打趣','[]','active','100','100')""")
        conn.execute("INSERT INTO persona_claim_metadata VALUES(1,'impression','playful','','inference',.4,100,'legacy',0)")
    result = rebuild_from_history(cognition, db, central)
    assert result['recovered_full_messages'] == result['old_impressions_under_review'] == 1
    assert rebuild_from_history(cognition, db, central) == {'already_prepared': True}
    batch = ProfileStore(cognition).claim()
    assert '用语音跟大家' in batch.prompt()
    assert '轻松打趣' not in batch.prompt()
    assert '轻松打趣' not in cognition.own_impression(201)


def test_history_coverage_is_paged_and_quiet_users_not_starved(cognition):
    cognition.import_sources([source(f'101:{i}', text=f'第{i}次修改画稿', occurred=i) for i in range(1, 110)])
    cognition.import_sources([source('102:1', user=202)])
    profiles = ProfileStore(cognition)
    first = profiles.claim(now=1000)
    assert first.user_id == 201 and len(first.sources) <= 48
    empty = {'observations': [], 'portrait': []}
    profiles.publish(first, empty, review_for(empty), now=1001)
    assert profiles.claim(now=1002).user_id == 202
    assert profiles.claim(now=1003).user_id == 201


def test_long_message_tail_is_processed_not_silently_marked_complete(cognition):
    s = source(text='开头' + '谈论画稿。' * 350 + '最后我想改用水彩')
    cognition.import_sources([s])
    profiles = ProfileStore(cognition)
    batch = profiles.claim()
    assert '改用水彩' not in batch.sources[0]['text']
    empty = {'observations': [], 'portrait': []}
    profiles.publish(batch, empty, review_for(empty))
    next_batch = profiles.claim()
    assert next_batch and '最后我想改用水彩' in next_batch.sources[0]['text']


def test_previous_profile_cannot_silently_disappear(cognition):
    publish(cognition, source())
    cognition.import_sources([source('101:2', text='嗯', occurred=200)])
    profiles = ProfileStore(cognition)
    batch = profiles.claim()
    empty = {'observations': [], 'portrait': []}
    with pytest.raises(ValueError, match='previous_observations_require_review'):
        profiles.publish(batch, empty, review_for(empty))
    assert '认真修改画稿' in cognition.own_impression(201)


def test_viewing_own_profile_does_not_enqueue_new_personality_evidence(cognition):
    cognition.import_sources([source(text='娅娅，你对我有什么印象？')])
    assert ProfileStore(cognition).claim() is None
    with cognition.connect() as conn:
        assert conn.execute('SELECT count(*) FROM persona_sources').fetchone()[0] == 0


@async_test
async def test_worker_publishes_only_after_separate_review_and_tracks_usage(tmp_path, cognition):
    from types import SimpleNamespace
    from bot.services.persona_profile_worker import ProfileWorker
    from bot.services.tangtang_chat import TangtangConfig
    s = source()
    cognition.import_sources([s])
    central = PersonaStore(tmp_path / 'central.db')
    central.set_option('denia_v2_enabled', True)
    config = TangtangConfig.from_values({'TANGTANG_ENABLED': '1', 'TANGTANG_API_KEY': 'synthetic',
        'TANGTANG_API_URL': 'https://example.invalid', 'TANGTANG_GROUP_IDS': '101'}, (101,))
    draft = draft_for(s)
    class Provider:
        calls = 0
        async def generate(self, config, system, prompt):
            self.calls += 1
            assert not cognition.snapshot('before_publish', 201, 102, []).claims
            return json.dumps(draft if self.calls == 1 else review_for(draft)), {'total_tokens': 50}
    provider = Provider()
    await ProfileWorker(cognition, central, provider, SimpleNamespace(load=lambda: config)).tick()
    assert provider.calls == 2
    assert '认真修改画稿' in cognition.own_impression(201)
    with central.connect() as conn:
        assert conn.execute("SELECT count(*) FROM jobs WHERE kind IN ('profile_draft','profile_review')").fetchone()[0] == 2
