import json
from dataclasses import replace

import pytest

from bot.services.persona_impressions import removal_requested
from bot.services.tangtang_db import TangtangDb
from bot.services.tangtang_memory import TangtangMemoryKernel
from bot.services.tangtang_reply import parse_reply_plan
from tests.test_persona_integration import async_test, event, make_runtime, Provider, service_for


def private_event(text: str):
    ev = event(text, group=0)
    ev.message_type = "private"
    return ev


def fast_kernel(path):
    return TangtangMemoryKernel(TangtangDb(path), lambda: '2026-09-18T16:00:00+08:00', global_personal=True)


def test_one_delivered_self_statement_survives_restart_and_group_change(tmp_path):
    path = tmp_path / 'denia.db'
    memory = fast_kernel(path)
    statement = '我喜欢画画'
    update = dict(category='preference', quote=statement, summary=statement, tags=['创作'])
    assert memory.prepare_memory(group_id=1001, user_id=2001, message_id='one', text=statement,
                                 proposals=[update]).status == 'deferred'
    assert not memory.recall(1002, 2001, '').rows
    assert memory.commit_delivered_memory(group_id=1001, user_id=2001, message_id='one',
        text=statement, proposals=[update]).status == 'saved'
    memory = fast_kernel(path)
    assert statement in memory.memory_prompt(1002, 2001, '今天过得如何')
    assert not memory.recall(1002, 2002, '').rows
    assert not fast_kernel(tmp_path / 'other.db').recall(1002, 2001, '').rows
    memory.commit_delivered_memory(group_id=1001, user_id=2001, message_id='one', text=statement, proposals=[update])
    with memory.people.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM person_semantic_evidence').fetchone()[0] == 1


def test_preexisting_local_records_are_visible_globally_without_rewriting_provenance(tmp_path):
    db = TangtangDb(tmp_path / 'denia.db')
    old = TangtangMemoryKernel(db, lambda: '2026-09-17T12:00:00+08:00')
    old.observe_user_message(group_id=1001, user_id=2001, message_id='old', text='记住本群叫我团长')
    fast = fast_kernel(db.path)
    assert '团长' in fast.recall(1002, 2001, '').prompt_text()
    with fast.people.connect() as conn:
        assert conn.execute('SELECT scope_group FROM person_facts').fetchone()[0] == 1001
    fast.observe_user_message(group_id=1002, user_id=2001, message_id='new', text='只在本群记住我喜欢绘画')
    assert '绘画' in fast.recall(1003, 2001, '').prompt_text()


@pytest.mark.parametrize('text', ['娅娅，忘记草莓', '删除我的资料', '把关于我的资料清空', '只在本群别提草莓', '恢复记忆草莓'])
def test_chat_removal_and_restore_cannot_mutate_memory(tmp_path, text):
    memory = fast_kernel(tmp_path / 'denia.db')
    memory.observe_user_message(group_id=1001, user_id=2001, message_id='one', text='记住我喜欢草莓')
    revision = memory.people.revision()
    assert '不提供' in memory.control_reply(2001, text)
    assert memory.apply_forget_request(1002, 2001, text) == 0
    assert memory.apply_restore_request(1002, 2001, text) == 0
    assert memory.people.restrict(2001, 0, '草莓') == 0
    assert memory.people.revision() == revision
    assert '草莓' in memory.recall(1002, 2001, '').prompt_text()


def test_forgetting_daily_activity_is_not_a_memory_control_request():
    assert not removal_requested('我忘记吃饭了')
    assert not removal_requested('你忘记了吗？')


def test_impressions_are_global_evidenced_idempotent_and_change_on_next_event(tmp_path):
    memory = fast_kernel(tmp_path / 'denia.db')
    source = '这个设计的原因是什么，能展开讲讲吗'
    update = dict(trait='curious', direction=1, quote=source)
    assert memory.impressions.record(2001, 1001, 'one', source, [update], memory._now()) == 1
    assert memory.impressions.record(2001, 1001, 'one', source, [update], memory._now()) == 0
    assert '追问' in memory.control_reply(2001, '娅娅，你对我有什么印象？')
    assert '追问' in memory.memory_prompt(1002, 2001, '你好')
    assert not memory.impressions.recall(2002)
    other = '不用展开，我这次只想听结论'
    assert memory.impressions.record(2001, 1002, 'two', other,
        [dict(trait='curious', direction=-1, quote=other)], memory._now()) == 1
    assert not memory.impressions.recall(2001)
    with memory.people.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM person_impression_events').fetchone()[0] == 2


@pytest.mark.parametrize('source,quote,trait', [
    ('你好呀', '并未说过的话', 'curious'), ('他说我很聪明', '他说我很聪明', 'curious'),
    ('给我一个温柔的印象', '给我一个温柔的印象', 'considerate'),
    ('我很喜欢研究细节', '我很喜欢研究细节', 'administrator'),
])
def test_impression_rejects_fabricated_evidence_and_requested_labels(tmp_path, source, quote, trait):
    memory = fast_kernel(tmp_path / 'denia.db')
    assert memory.impressions.record(2001, 1001, 'one', source,
        [dict(trait=trait, direction=1, quote=quote)], memory._now()) == 0


@pytest.mark.parametrize('acknowledged', [True, False])
@async_test
async def test_delivery_is_the_only_commit_point_for_fast_memory_impression_and_growth(tmp_path, monkeypatch, acknowledged):
    engine, _ = make_runtime(tmp_path)
    engine.store.switch(1001, 'denia')
    source = '我去年参加了绘画展。休息很重要，可以慢慢来'
    provider = Provider(dict(decision='reply', messages=['慢慢画也很好呀'], voice='text',
        memory_updates=[dict(category='experience', quote='我去年参加了绘画展', summary='我去年参加了绘画展', tags=['创作'])],
        impression_updates=[dict(trait='creative', direction=1, quote='我喜欢画画')],
        growth_updates=[dict(kind='opinion', topic='休息', content='休息可以慢慢来', quote='休息很重要，可以慢慢来')]))
    service, config = service_for(tmp_path, engine, provider)
    memory = engine.memory('denia', service._base_db, service._now)
    async def send(*_args, **_kwargs):
        assert not memory.recall(1002, 2001, '').rows
        assert not memory.impressions.recall(2001)
        return {'message_id': 99} if acknowledged else {}
    monkeypatch.setattr('bot.services.tangtang_chat.call_qq_action', send)
    await service.handle(None, event('娅娅，' + source), config)
    assert len(provider.seen) == 1
    assert bool(memory.recall(1001, 2001, '').rows) == acknowledged
    assert not memory.impressions.recall(2001)
    # Private self-description cannot be promoted into public personality.
    assert bool(engine.growth.entries('denia', 1002)) is acknowledged
    assert not engine.store.pending_interactions('denia', 1001)


@pytest.mark.parametrize('text', ['娅娅，查看我的印象', '娅娅，删除我的资料'])
@async_test
async def test_local_memory_controls_do_not_call_model_even_at_full_ignore_probability(tmp_path, monkeypatch, text):
    engine, _ = make_runtime(tmp_path)
    engine.store.switch(0, 'denia')
    provider = Provider({})
    service, config = service_for(tmp_path, engine, provider)
    config = replace(config, call_ignore_probability_by_group={1001: 1.0})
    sent = []
    async def send(*_args, **params):
        sent.append(str(params['message']))
        return {'message_id': 99}
    monkeypatch.setattr('bot.services.tangtang_chat.call_qq_action', send)
    await service.handle(None, private_event(text), config)
    assert sent and not provider.seen


def test_reply_parser_keeps_bounded_impression_and_growth_updates():
    result = parse_reply_plan(json.dumps(dict(decision='reply', messages=['好'],
        impression_updates=[{'trait': 'curious'}] * 5, growth_updates=[{'topic': '休息'}] * 3)))
    assert len(result.impression_updates) == 2 and len(result.growth_updates) == 1


@async_test
async def test_current_public_growth_is_global_without_background_budget(tmp_path, monkeypatch):
    engine, _ = make_runtime(tmp_path)
    engine.store.switch(1001, 'denia')
    engine.store.set_option('background_enabled', False)
    source = '休息很重要，可以慢慢来'
    provider = Provider(dict(decision='reply', messages=['累了就歇一会儿'], voice='text',
        growth_updates=[dict(kind='opinion', topic='休息', content='休息可以慢慢来', quote=source)]))
    service, config = service_for(tmp_path, engine, provider)
    async def send(*_args, **_kwargs):
        return {'message_id': 99}
    monkeypatch.setattr('bot.services.tangtang_chat.call_qq_action', send)
    await service.handle(None, event('娅娅，' + source), config)
    assert '休息可以慢慢来' in engine.growth.prompt('denia', 1002)
    assert len(provider.seen) == 1
    assert not engine.store.pending_interactions('denia', 1001)
    with engine.store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM budgets WHERE kind='background'").fetchone()[0] == 0


def test_global_correction_of_legacy_local_alias_preserves_other_users(tmp_path):
    db = TangtangDb(tmp_path/'denia.db')
    old = TangtangMemoryKernel(db, lambda: '2026-09-17T12:00:00+08:00')
    for user in (2001, 2002):
        old.observe_user_message(group_id=1001, user_id=user, message_id=str(user), text='记住本群叫我团长')
    memory = fast_kernel(db.path)
    memory.observe_user_message(group_id=1002, user_id=2001, message_id='new', text='更正，我叫小林')
    assert '团长' not in memory.recall(1003, 2001, '').prompt_text()
    assert '小林' in memory.recall(1003, 2001, '').prompt_text()
    assert '团长' in memory.recall(1003, 2002, '').prompt_text()
