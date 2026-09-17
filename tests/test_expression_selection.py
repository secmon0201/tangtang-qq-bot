from dataclasses import replace
import json

from bot.services.expression_selection import ExpressionSelection
from bot.services.persona_profiles import ChatContext, PersonaProfile
from bot.services.persona_store import PersonaStore
from bot.services.tangtang_reply import parse_reply_plan


def setup_catalog(tmp_path):
    rows = [dict(id=k, name=name, file=k+'.png', use='友善回应', avoid='悲伤', group=group,
                 aliases=[], explicit_only=restricted)
            for k, name, group, restricted in [('a', '浅笑', 'gentle_smile', False),
                                              ('b', '侧脸笑', 'gentle_smile', False),
                                              ('c', '正面笑', 'gentle_smile', False),
                                              ('sad', '悲伤', 'sad', False),
                                              ('taunt', '杂鱼', 'teasing', True)]]
    (tmp_path/'expression_catalog.json').write_text(json.dumps(rows), encoding='utf-8')
    profile = PersonaProfile('denia', '达妮娅', '娅娅', tmp_path, 'v1')
    context = ChatContext(profile, 1001, 2001, 'req1', 0, 0, 'model')
    store = PersonaStore(tmp_path/'state.db')
    return ExpressionSelection(store), context, ('a', 'b', 'c', 'sad', 'taunt')


def test_weighting_uses_only_successes_and_isolates_groups(tmp_path, monkeypatch):
    service, context, available = setup_catalog(tmp_path)
    for i in range(6):
        turn = replace(context, request_id=f'old{i}')
        assert service.choose(turn, '发个浅笑表情', '', (), available=available, roll=1, now=100+i) == 'a'
        service.result(turn, 'delivered', message_id=str(i+1), now=100+i)
    for j, key in enumerate(['b', 'c']):
        turn = replace(context, request_id=f'recent{j}')
        service.choose(turn, f'发个{key}表情', '', (), available=available, roll=1, now=110+j)
        service.result(turn, 'delivered', message_id=str(20+j), now=110+j)
    seen = []
    monkeypatch.setattr('bot.services.expression_selection.random.choices',
                        lambda ids, weights, k: seen.append((ids, weights)) or [ids[0]])
    assert service.choose(context, '你好', 'a', ('b', 'c'), available=available, roll=.1, now=120) == 'a'
    assert seen[-1] == (['a'], [1/3])
    counts, recent = service.history(replace(context, group_id=1002), 120)
    assert not counts and not recent
    # Even a failed or uncertain attempt cannot change weights; acknowledgements are idempotent.
    service.result(context, 'uncertain', now=121)
    assert not service.result(context, 'delivered', message_id='later', now=122)
    assert service.history(context, 123)[0]['a'] == 6
    assert not service.history(context, 90000)[0]


def test_cross_group_candidates_preserve_validation_and_recent_exclusion(tmp_path, monkeypatch):
    service, context, available = setup_catalog(tmp_path)
    monkeypatch.setattr('bot.services.expression_selection.random.choices',
                        lambda ids, weights, k: ['sad'] if 'sad' in ids else [ids[0]])
    assert service.choose(context, '你好', 'a', ('sad', 'invalid', 'taunt'), available=available, roll=0, now=10) == 'sad'
    service.result(context, 'delivered', message_id='1', now=11)
    with service.store.connect() as conn:
        detail = json.loads(conn.execute('SELECT decision_json FROM expression_events').fetchone()[0])
    assert 'sad' not in detail['rejected']
    assert detail['rejected']['taunt'] == 'explicit_only'
    assert detail['rejected']['invalid'] == 'invalid_id'
    last = service.history(context, 12)[1][0]
    later = replace(context, request_id='later')
    assert service.choose(later, '你好', last, (), available=available, roll=0, now=12) == ''
    # Explicit exact ID may repeat; generic category rotates.
    explicit = replace(context, request_id='exact')
    assert service.choose(explicit, f'发个{last}表情', '', (), available=available, roll=1, now=13) == last
    category = replace(context, request_id='category')
    assert service.choose(category, '发个微笑表情', '', (), available=available, roll=1, now=13) in set(available[:3])-{last}


def test_gates_and_restricted_image(tmp_path):
    service, context, available = setup_catalog(tmp_path)
    for i, (text, roll, blocked, structured, key) in enumerate([
        ('你好', .6, '', True, 'a'), ('别发表情', 0, '', True, 'a'),
        ('你好', 0, 'voice', True, 'a'), ('发个a表情', 0, 'disabled', True, 'a'),
        ('你好', 0, '', False, 'a'), ('你好', 0, '', True, 'taunt'),
    ]):
        assert service.choose(replace(context, request_id=str(i)), text, key, (), available=available,
                              roll=roll, blocked=blocked, structured=structured, now=1) == ''
    assert service.choose(context, '发个杂鱼表情', '', (), available=available, roll=1, now=1) == 'taunt'
    service.result(context, 'delivered', now=2)  # no acknowledgement
    assert not service.history(context, 3)[0]
    assert service.choose(context, '发个杂鱼表情', '', (), available=available, roll=1, now=4) == ''


def test_full_metadata_persisted_and_unsafe_paths_rejected(tmp_path):
    service, context, _ = setup_catalog(tmp_path)
    rows = service.catalog(context.persona)
    with service.store.connect() as conn:
        saved = json.loads(conn.execute("SELECT metadata FROM expression_metadata WHERE expression_id='a'").fetchone()[0])
    assert saved['name'] == '浅笑' and saved['group'] == 'gentle_smile'
    rows[0]['file'] = '../secret.png'
    (tmp_path/'expression_catalog.json').write_text(json.dumps(rows), encoding='utf-8')
    refreshed = service.catalog(replace(context.persona, version='v2'))
    assert 'a' not in [r['id'] for r in refreshed]


def test_reply_plan_parses_bounded_candidates():
    raw = dict(decision='reply', messages=['你好'], expression='a', expression_candidates=['b','c','sad','extra'])
    assert parse_reply_plan(json.dumps(raw)).expression_candidates == ('b','c','sad')
    raw['expression_candidates'] = 'not-a-list'
    assert parse_reply_plan(json.dumps(raw)).expression_candidates == ()
