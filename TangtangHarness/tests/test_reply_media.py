import json
from dataclasses import replace

from tangtang_harness.chat import reply_metadata
from tangtang_harness.context import fixed_prefix, build_context
from tangtang_harness.reply_media import ExpressionSelector, expression_candidates, voice_decision
from tangtang_harness.store import Store
from tangtang_harness.types import InboundEvent
from test_runtime_and_console import configured


def catalog(tmp_path):
    root = tmp_path / 'resources' / 'personas' / 'denia'
    (root / 'expressions').mkdir(parents=True)
    rows = [dict(id='smile', name='微笑', file='smile.gif', group='gentle_smile',
                 use='温和问候、普通闲聊', avoid='严肃悲伤'),
            dict(id='laugh', name='大笑', file='laugh.gif', group='happy',
                 use='开心、轻松玩笑', avoid='真实难过'),
            dict(id='think', name='思考', file='think.gif', group='thinking',
                 use='好奇思考问题', avoid='告别'),
            dict(id='sad', name='难过', file='missing.gif', group='sad', use='难过', avoid='开心'),
            dict(id='secret', name='特定表情', file='secret.gif', group='secret',
                 use='明确指定', avoid='普通聊天', explicit_only=True)]
    for row in rows:
        if row['file'] != 'missing.gif':
            (root / 'expressions' / row['file']).write_bytes(b'fixture image')
    (root / 'expression_catalog.json').write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
    return rows


def event(text='娅娅今天好开心'):
    return InboundEvent('1', 103, 101, 102, text)


def test_expression_candidates_enter_dynamic_tail_without_changing_persona_prefix(tmp_path):
    catalog(tmp_path)
    config = configured(tmp_path, 'live', context_mode='legacy')
    store = Store(tmp_path)
    prefix = fixed_prefix(config)
    prepared = build_context(config, store, event(), config.profile())
    tail = prepared.messages[-1]['content']
    assert 'laugh（大笑' in tail and '适用=开心' in tail
    assert 'sad（难过' not in tail and 'secret（特定表情' not in tail
    directory = tmp_path / 'resources' / 'personas' / 'denia'
    rows = json.loads((directory / 'expression_catalog.json').read_text(encoding='utf-8'))
    rows[0]['use'] = '新的素材说明'
    (directory / 'expression_catalog.json').write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
    assert fixed_prefix(config) == prefix
    assert expression_candidates(tmp_path, '开心')[0]['id'] == 'laugh'


def test_expression_alternatives_avoid_two_recent_delivered_images_and_respect_switches(tmp_path):
    catalog(tmp_path)
    selector = ExpressionSelector(tmp_path, Store(tmp_path))
    source = event()
    selector.delivered(source, 'smile', now=100)
    selector.delivered(source, 'laugh', now=101)
    metadata = {'expression': 'smile', 'expression_candidates': ['laugh', 'think', 'secret', 'sad']}
    selected = selector.choose(source, metadata, roll=.1, now=102)
    assert selected[0] == 'think' and selected[1].suffix == '.gif'
    assert selector.choose(source, metadata, roll=.8, now=102) is None
    assert selector.choose(source, metadata, enabled=False, roll=.1, now=102) is None
    assert selector.choose(source, metadata, voice=True, roll=.1, now=102) is None
    assert selector.choose(replace(source, text='娅娅不要表情包'), metadata, roll=.1, now=102) is None
    assert selector.resolve('发一个思考表情包').name == 'think.gif'
    assert selector.resolve('不要表情包') is None


def test_reply_metadata_keeps_expression_alternatives_and_voice_fallback():
    parsed = reply_metadata('{"messages":["你好"],"voice":"accept",'
                            '"speech_text":"你好呀", "text_fallback":["你好"],'
                            '"expression_candidates":["smile","laugh"]}')
    assert parsed['voice'] == 'accept' and parsed['text_fallback'] == ['你好']
    assert parsed['expression_candidates'] == ['smile', 'laugh']


def test_explicit_voice_uses_full_spoken_text_and_supports_legacy_accept():
    source = event('娅娅用语音回复我')
    args = dict(enabled=True, ready=True, random_candidate=False)
    result = voice_decision(source, ['你好', '今天过得怎么样'], {'voice': 'accept'}, **args)
    assert result.voice and result.explicit and result.text == '你好\n今天过得怎么样'
    result = voice_decision(source, ['第一条', '第二条'], {'voice': 'accept', 'speech_text': '完整口语回答'}, **args)
    assert result.voice and result.text == '完整口语回答'
    assert not voice_decision(source, ['今天先打字吧'], {'voice': 'decline'}, **args).voice
    assert not voice_decision(source, ['我才不发语音'], {'voice': 'accept'}, **args).voice
    assert not voice_decision(replace(source, text='娅娅只用文字'), ['你好'], {'voice': 'accept'}, **args).voice
    assert not voice_decision(source, ['代码如下```print(1)```'], {'voice': 'accept'}, **args).voice
    assert not voice_decision(source, ['非常长' * 100], {'voice': 'accept'}, **args).voice
    assert not voice_decision(source, ['你好'], {'voice': 'accept'}, **{**args, 'ready': False}).voice


def test_ordinary_voice_sampling_obeys_candidate_and_text_decline():
    source = event()
    args = dict(enabled=True, ready=True)
    assert voice_decision(source, ['你好呀'], {'voice': 'auto'}, random_candidate=True, **args).voice
    assert not voice_decision(source, ['你好呀'], {'voice': 'auto'}, random_candidate=False, **args).voice
    assert not voice_decision(source, ['你好呀'], {'voice': 'text'}, random_candidate=True, **args).voice
