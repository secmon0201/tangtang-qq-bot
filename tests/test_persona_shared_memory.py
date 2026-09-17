from concurrent.futures import ThreadPoolExecutor

from bot.services.tangtang_db import TangtangDb
from bot.services.tangtang_memory import TangtangMemoryKernel
from bot.services.persona_memory_migration import migrate_people
from bot.services.persona_store import PersonaStore
from bot.services.persona_growth import PersonaGrowth


def kernel(tmp_path, persona='denia'):
    return TangtangMemoryKernel(TangtangDb(tmp_path / f'{persona}.db'), lambda: '2026-09-18T12:00:00+08:00')


def remember(k, text, group=1001, event='one'):
    return k.observe_user_message(group_id=group, user_id=2001, message_id=event, text=text)


def test_same_person_across_groups_but_local_alias_and_other_persona_are_separate(tmp_path):
    k = kernel(tmp_path)
    remember(k, '记住我喜欢草莓')
    remember(k, '记住本群叫我团长', event='two')
    remember(k, '只在本群记住我喜欢西瓜', event='three')
    assert len(k.recall(1001, 2001, '').rows) == 3
    assert len(k.recall(1002, 2001, '').rows) == 1
    assert not k.recall(1002, 2002, '').rows
    assert not kernel(tmp_path, 'tangtang').recall(1002, 2001, '').rows


def test_independent_evidence_and_explicit_correction(tmp_path):
    k = kernel(tmp_path)
    for _ in range(3):
        remember(k, '我喜欢草莓')
    assert not k.recall(1001, 2001, '草莓').rows
    remember(k, '我喜欢草莓', group=1002, event='two')
    assert k.recall(1002, 2001, '草莓').rows
    remember(k, '我现在不喜欢草莓了', group=1002, event='three')
    rows = k.recall(1001, 2001, '草莓').rows
    assert len(rows) == 1 and '不喜欢' in rows[0]['content']


def test_profession_is_shared_while_conversation_history_stays_local(tmp_path):
    k = kernel(tmp_path)
    remember(k, '记住我是教师')
    assert '我是教师' in k.recall(1002, 2001, '我的职业').prompt_text()
    assert not k.recall(1002, 2002, '我的职业').rows
    assert not kernel(tmp_path, 'tangtang').recall(1002, 2001, '我的职业').rows
    for group, text in ((1001, '抽卡五星先聊配队'), (1002, '抽卡五星先聊养成')):
        k.db.insert_call(group_id=group, user_id=2001, message_id=str(group), call_text=text,
                         reply_text='嗯', reply_kind='model', mode='d', created_at=k._now())
    first = k.episode_prompt(1001, 2001, '抽卡五星')
    second = k.episode_prompt(1002, 2001, '抽卡五星')
    assert '配队' in first and '养成' not in first
    assert '养成' in second and '配队' not in second


def test_forgetting_blocks_history_relearning_and_rollback_until_explicit_restore(tmp_path):
    k = kernel(tmp_path)
    remember(k, '记住我喜欢草莓')
    revision = k.people.revision()
    assert k.apply_forget_request(1002, 2001, '忘记我喜欢草莓这件事') == 1
    assert k.people.revision() > revision
    assert not k.safe_text(1001, 2001, '以前说过我喜欢草莓')
    for i in range(4):
        remember(k, '我喜欢草莓', event=str(i))
        remember(k, '我最喜欢草莓', event='paraphrase'+str(i))
    assert not k.recall(1001, 2001, '草莓').rows
    remember(k, '只在本群记住我喜欢草莓', event='local-remember')
    assert not k.recall(1001, 2001, '草莓').rows
    assert not k.recall(1002, 2001, '草莓').rows
    k.people.set_enabled(False)
    assert not k.safe_text(1001, 2001, '我喜欢草莓')
    k.people.set_enabled(True)
    k.apply_restore_request(1001, 2001, '恢复记忆草莓')
    assert k.recall(1001, 2001, '草莓').rows
    k.apply_forget_request(1001, 2001, '只在本群别提草莓')
    assert not k.recall(1001, 2001, '草莓').rows
    assert k.recall(1002, 2001, '草莓').rows


def test_shared_relationship_concurrent_updates_dedup_and_mood_decay(tmp_path):
    k = kernel(tmp_path)
    k.people.revision()  # initialize before exercising concurrent writers
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: k.update_states_after_reply(1001+i%2, 2001, '烦死', event_id=str(i)), range(12)))
    k.update_states_after_reply(1001, 2001, '烦死', event_id='0')
    r = k.people.relationship(2001)
    assert r['interaction_count'] == 12
    assert .11 < r['familiarity'] < .13
    assert '别扭' in k.state_prompt(1002, 2001)
    assert '别扭' not in k.state_prompt(1002, 2002)
    from bot.services.persona_mood import mood_decay
    assert mood_decay(r['mood'], r['updated_at'], '2026-09-18T18:00:00+08:00') > r['mood']


def test_episode_recall_is_relevant_bounded_and_keeps_current_topics_separate(tmp_path):
    k = kernel(tmp_path)
    for i, text in enumerate(('我这次抽卡出了两个五星', '我上次抽卡没出五星', '群友说他抽卡出了五星', '我喜欢吃晚饭')):
        k.db.insert_call(group_id=1001, user_id=2001, message_id=str(i), call_text=text,
                         reply_text='嗯', reply_kind='model', mode='c', created_at=k._now())
    assert not k.episode_prompt(1002, 2001, '抽卡五星')
    prompt = k.episode_prompt(1001, 2001, '抽卡五星')
    assert prompt.count('互动片段') == 2
    assert '本群' in prompt and '群友说他' not in prompt
    assert not k.episode_prompt(1002, 2001, '晚饭吃啥')
    assert not k.episode_prompt(1002, 2002, '抽卡五星')
    k.apply_forget_request(1002, 2001, '忘记抽卡')
    assert not k.episode_prompt(1002, 2001, '抽卡五星')


def test_additive_migration_is_idempotent_and_does_not_sum_familiarity(tmp_path):
    k = kernel(tmp_path)
    k.db.update_relationship_state(1001, 2001, warmth_delta=.01, now=k._now())
    k.db.update_relationship_state(1002, 2001, warmth_delta=.02, now=k._now())
    k.db.upsert_memory(group_id=1001, user_id=2001, kind='explicit', content='我喜欢草莓',
        normalized_content='我喜欢草莓', status='active', importance=.8, confidence=.9,
        source_message_id='m', source_hash='hash', now=k._now())
    assert migrate_people(k.db)['people_processed'] == 1
    assert migrate_people(k.db) == {'already_applied': 1}
    assert k.people.relationship(2001)['familiarity'] == .01
    assert k.recall(1002, 2001, '草莓').rows
    assert k.db.active_memories(1001, 2001)  # legacy rows retained


def test_global_growth_evidence_and_group_override_do_not_change_other_groups(tmp_path):
    store = PersonaStore(tmp_path/'state.db')
    growth = PersonaGrowth(store)
    for i, offset in enumerate((0, 10, 86400)):
        store.observe(persona='denia', group_id=1001+i%2, user_id=2001, request_id=str(i),
                      source='休息很重要，可以慢慢来', reply='嗯', now=1789488000+offset)
    rows = store.interactions('denia', 0)
    proposal = dict(scope='persona', kind='opinion', topic='休息', content='休息可以慢慢来',
                    evidence=[{'id':r['id'], 'quote':r['source']} for r in rows])
    assert growth.propose('denia', 1001, proposal, 1789580000)
    assert not growth.propose('tangtang', 1001, proposal, 1789580000)
    entry = growth.entries('denia', 1001)[0]
    assert growth.disable('denia', 1001, entry['id'])
    assert not growth.prompt('denia', 1001)
    assert growth.prompt('denia', 1002)
    assert not growth.rollback('denia', 1001, entry['id'], 1, 1789580001)
    assert growth.disable('denia', 0, entry['id'])
    assert not growth.prompt('denia', 1002)
