"""Independent evidence review, automatic rebuild and competing updates."""
import json

import httpx
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
    assert not snap.claims


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


def test_live_messages_take_priority_without_starving_historical_rebuild(cognition):
    from bot.services.persona_profile_store import enqueue
    cognition.import_sources([source(user=201), source('102:2', user=202), source('103:3', user=203)])
    with cognition.connect() as conn:
        enqueue(conn, 203, 100, priority=2)
        enqueue(conn, 202, 100, priority=2)
    profiles = ProfileStore(cognition)
    assert profiles.claim(now=1000).user_id == 202
    assert profiles.claim(now=1001).user_id == 203
    # The third dispatch deliberately services the oldest waiting person.
    assert profiles.claim(now=1002).user_id == 201


def test_existing_profile_queue_gains_priority_without_losing_work(cognition):
    cognition.import_sources([source()])
    with cognition.connect() as conn:
        for column in ('priority', 'stage', 'draft', 'draft_versions'):
            conn.execute(f'ALTER TABLE persona_profile_jobs DROP COLUMN {column}')
    with cognition.connect() as conn:
        row = conn.execute('SELECT * FROM persona_profile_jobs WHERE user_id=201').fetchone()
        assert row['state'] == 'pending'
        assert (row['priority'], row['stage'], row['draft'], row['draft_versions']) == (0, 'draft', '', '')


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


def test_retry_state_is_not_reported_as_initial_processing(cognition):
    cognition.import_sources([source()])
    profiles = ProfileStore(cognition)
    batch = profiles.claim(now=100)
    profiles.fail(batch, 'HTTPStatusError', now=101)

    text = cognition.own_impression(201)

    assert '暂时不可用' in text
    assert '发言已保留' in text
    assert '正在根据' not in text


def test_validation_failure_stops_after_three_attempts_and_new_evidence_resumes(cognition):
    cognition.import_sources([source()])
    profiles = ProfileStore(cognition)
    for attempt, now in enumerate((100, 200, 300), start=1):
        batch = profiles.claim(now=now)
        assert batch.stage == 'draft'
        profiles.fail(batch, 'invalid_profile_evidence', now=now + 1, terminal=True)
        with cognition.connect() as conn:
            job = conn.execute('SELECT state,attempts FROM persona_profile_jobs WHERE user_id=201').fetchone()
        assert job['state'] == ('retry' if attempt < 3 else 'failed')

    failed = cognition.own_impression(201)
    assert '没能形成可靠' in failed
    assert '正在根据' not in failed

    cognition.import_sources([source('101:2', text='我改用电脑画图了', occurred=400)])
    with cognition.connect() as conn:
        job = conn.execute('SELECT state,attempts,error,stage FROM persona_profile_jobs WHERE user_id=201').fetchone()
    assert dict(job) == {'state': 'pending', 'attempts': 0, 'error': '', 'stage': 'draft'}


def test_review_retry_reuses_validated_draft(cognition):
    s = source()
    cognition.import_sources([s])
    profiles = ProfileStore(cognition)
    draft_batch = profiles.claim(now=100)
    assert draft_batch.stage == 'draft'

    draft = draft_for(s)
    profiles.save_draft(draft_batch, draft, now=101)
    profiles.fail(draft_batch, 'ReadTimeout', now=102)

    review_batch = profiles.claim(now=200)
    assert review_batch.stage == 'review'
    assert review_batch.draft == draft
    assert review_batch.sources[0]['event_key'] == s['event_key']

    profiles.publish(review_batch, review_batch.draft, review_for(review_batch.draft))
    assert '认真修改画稿' in cognition.own_impression(201)
    with cognition.connect() as conn:
        job = conn.execute('SELECT stage,draft,draft_versions FROM persona_profile_jobs WHERE user_id=201').fetchone()
    assert dict(job) == {'stage': 'draft', 'draft': '', 'draft_versions': ''}


def test_changed_review_source_invalidates_saved_draft(cognition):
    s = source()
    cognition.import_sources([s])
    profiles = ProfileStore(cognition)
    draft_batch = profiles.claim(now=100)
    profiles.save_draft(draft_batch, draft_for(s), now=101)
    profiles.fail(draft_batch, 'ReadTimeout', now=102)

    cognition.import_sources([{**s, 'revision': 2, 'attribution': 'ambient'}])
    resumed = profiles.claim(now=200)

    assert resumed.stage == 'draft'
    assert resumed.draft is None
    assert resumed.sources[0]['revision'] == 2


def test_empty_valid_draft_survives_review_retry(cognition):
    cognition.import_sources([source()])
    profiles = ProfileStore(cognition)
    draft_batch = profiles.claim(now=100)
    empty = {'observations': [], 'portrait': []}
    profiles.save_draft(draft_batch, empty, now=101)
    profiles.fail(draft_batch, 'ReadTimeout', now=102)

    review_batch = profiles.claim(now=200)

    assert review_batch.stage == 'review'
    assert review_batch.draft == empty


def test_real_model_evaluation_resolves_active_profile_before_sanitizing_groups(tmp_path):
    from scripts.evaluate_persona_profiles import evaluation_config
    path = tmp_path / '.env'
    path.write_text('\n'.join(['TANGTANG_ENABLED=1', 'TANGTANG_MODEL=baseline',
        'TANGTANG_API_KEY=synthetic', 'TANGTANG_API_URL=https://example.invalid/base',
        'TANGTANG_MODEL_ACTIVE_PROFILE=selected', 'TANGTANG_MODEL_PROFILE_1_NAME=selected',
        'TANGTANG_MODEL_PROFILE_1_PROVIDER=synthetic', 'TANGTANG_MODEL_PROFILE_1_MODEL=selected-model',
        'TANGTANG_MODEL_PROFILE_1_API_STYLE=chat_completions',
        'TANGTANG_MODEL_PROFILE_1_API_URL=https://example.invalid/selected',
        'TANGTANG_MODEL_PROFILE_1_API_KEY=synthetic', 'TANGTANG_GROUP_IDS=9001']), encoding='utf-8')
    config = evaluation_config(path)
    assert config.model == 'selected-model'
    assert config.api_style == 'chat_completions'
    assert config.api_url == 'https://example.invalid/selected'
    assert config.group_ids == frozenset({101})


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


@async_test
async def test_worker_resumes_review_with_stage_specific_budgets(tmp_path, cognition, monkeypatch):
    from types import SimpleNamespace
    from bot.services.persona_profile_worker import ProfileWorker
    from bot.services.tangtang_chat import TangtangConfig
    s = source()
    cognition.import_sources([s])
    central = PersonaStore(tmp_path / 'central.db')
    central.set_option('denia_v2_enabled', True)
    config = TangtangConfig.from_values({'TANGTANG_ENABLED': '1', 'TANGTANG_API_KEY': 'synthetic',
        'TANGTANG_API_URL': 'https://example.invalid', 'TANGTANG_GROUP_IDS': '101'}, (101,))
    clock = [100.0]
    monkeypatch.setattr('bot.services.persona_profile_worker.time.time', lambda: clock[0])
    draft = draft_for(s)

    class Provider:
        def __init__(self):
            self.calls = []
            self.review_failed = False

        async def generate(self, config, instruction, prompt):
            stage = 'draft' if instruction.startswith('为达妮娅独立整理') else 'review'
            self.calls.append((stage, config.max_output_tokens, config.max_response_chars))
            if stage == 'draft':
                return json.dumps(draft), {'total_tokens': 10}
            if not self.review_failed:
                self.review_failed = True
                raise TimeoutError
            return json.dumps(review_for(draft)), {'total_tokens': 10}

    provider = Provider()
    worker = ProfileWorker(cognition, central, provider, SimpleNamespace(load=lambda: config))
    await worker.tick()
    assert worker.retry.max_delay == 21600

    clock[0] += 11
    await worker.tick()

    assert provider.calls == [('draft', 8000, 32000), ('review', 6000, 24000),
                              ('review', 6000, 24000)]
    assert '认真修改画稿' in cognition.own_impression(201)


@async_test
async def test_truncated_draft_retry_escalates_output_budget(tmp_path, cognition, monkeypatch):
    from types import SimpleNamespace
    from bot.services.persona_profile_worker import ProfileWorker
    from bot.services.tangtang_chat import TangtangConfig
    s = source()
    cognition.import_sources([s])
    central = PersonaStore(tmp_path / 'central.db')
    central.set_option('denia_v2_enabled', True)
    config = TangtangConfig.from_values({'TANGTANG_ENABLED': '1', 'TANGTANG_API_KEY': 'synthetic',
        'TANGTANG_API_URL': 'https://example.invalid', 'TANGTANG_GROUP_IDS': '101'}, (101,))
    clock = [100.0]
    monkeypatch.setattr('bot.services.persona_profile_worker.time.time', lambda: clock[0])
    draft = draft_for(s)

    class Provider:
        def __init__(self):
            self.budgets = []

        async def generate(self, config, instruction, prompt):
            self.budgets.append((config.max_output_tokens, config.max_response_chars))
            if instruction.startswith('为达妮娅独立整理') and len(self.budgets) == 1:
                return '{"observations":', {'total_tokens': 10}
            if instruction.startswith('为达妮娅独立整理'):
                return json.dumps(draft), {'total_tokens': 10}
            return json.dumps(review_for(draft)), {'total_tokens': 10}

    provider = Provider()
    worker = ProfileWorker(cognition, central, provider, SimpleNamespace(load=lambda: config))
    await worker.tick()
    clock[0] += 11
    await worker.tick()

    assert provider.budgets == [(8000, 32000), (16000, 48000), (6000, 24000)]
    assert '认真修改画稿' in cognition.own_impression(201)


@async_test
async def test_worker_honors_retry_after_from_429(tmp_path, cognition, monkeypatch):
    from types import SimpleNamespace
    from bot.services.persona_profile_worker import ProfileWorker
    from bot.services.tangtang_chat import TangtangConfig
    cognition.import_sources([source()])
    central = PersonaStore(tmp_path / 'central.db')
    central.set_option('denia_v2_enabled', True)
    config = TangtangConfig.from_values({'TANGTANG_ENABLED': '1', 'TANGTANG_API_KEY': 'synthetic',
        'TANGTANG_API_URL': 'https://example.invalid', 'TANGTANG_GROUP_IDS': '101'}, (101,))
    clock = [100.0]
    monkeypatch.setattr('bot.services.persona_profile_worker.time.time', lambda: clock[0])
    empty = {'observations': [], 'portrait': []}

    class Provider:
        def __init__(self):
            self.calls = 0

        async def generate(self, config, instruction, prompt):
            self.calls += 1
            if self.calls == 1:
                response = httpx.Response(
                    429, request=httpx.Request('POST', 'https://example.invalid'),
                    headers={'Retry-After': '7200'},
                    json={'error': {'code': 'rate_limit_exceeded'}},
                )
                response.raise_for_status()
            if instruction.startswith('为达妮娅独立整理'):
                return json.dumps(empty), {'total_tokens': 10}
            return json.dumps(review_for(empty)), {'total_tokens': 10}

    provider = Provider()
    worker = ProfileWorker(cognition, central, provider, SimpleNamespace(load=lambda: config))
    await worker.tick()

    clock[0] += 7199
    await worker.tick()
    assert provider.calls == 1

    clock[0] += 1
    await worker.tick()
    assert provider.calls > 1


def test_profile_report_includes_sanitized_provider_failure(tmp_path):
    from scripts.report_persona_memory import report
    central = PersonaStore(tmp_path / 'data/personas/state.db')
    central.set_option('profile_provider_retry', {
        'reason': 'provider_http', 'http_status': 429, 'error_code': 'rate_limit_exceeded',
        'retryable': True, 'next_attempt_at': 500, 'failures': 2,
        'blocked_until_config_change': False, 'private': 'DO NOT REPORT',
    })

    result = report(tmp_path)

    assert result['profile_provider'] == {
        'reason': 'provider_http', 'http_status': 429, 'error_code': 'rate_limit_exceeded',
        'retryable': True, 'next_attempt_at': 500, 'failures': 2,
        'blocked_until_config_change': False,
    }
