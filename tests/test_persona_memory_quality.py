import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from bot.services.persona_memory_quality import extract_personal_fact
from bot.services.persona_growth_evidence import retrieve_growth_evidence
from bot.services.persona_growth import PersonaGrowth
from bot.services.persona_growth_diagnostics import diagnostic_text
from bot.services.persona_store import PersonaStore
from bot.services.persona_background import PersonaBackground
from bot.services.tangtang_memory import TangtangMemoryKernel
from bot.services.tangtang_db import TangtangDb
from bot.services.tangtang_chat import TangtangConfig


@pytest.mark.parametrize('text', ['记住了吗', '记住你没有中之人', '我是问你有几个人',
    '我是嘉然，借我五十块钱', '我是糖糖的心魔', '我喜欢你', '记住我喜欢你',
    '我希望你能赢', '我喜欢草莓吗？', '他说我喜欢草莓', '假如我喜欢草莓', '记住蓝花也是猫娘'])
def test_noise_is_not_personal_fact(text):
    assert extract_personal_fact(text) is None


@pytest.mark.parametrize('text,content', [('糖糖记住我喜欢草莓', '我喜欢草莓'),
    ('只在本群记住我喜欢西瓜', '我喜欢西瓜'), ('记住本群叫我团长', '本群叫我团长'),
    ('我叫小明', '我叫小明'), ('我现在不喜欢草莓了', '我现在不喜欢草莓了')])
def test_clear_personal_facts_survive(text, content):
    assert extract_personal_fact(text)[0] == content


def test_legacy_noise_hidden_without_deletion(tmp_path):
    k = TangtangMemoryKernel(TangtangDb(tmp_path/'history.db'), lambda:'2026-09-17T12:00:00+08:00')
    for i, text in enumerate(['了吗', '我喜欢你', '我喜欢草莓']):
        k.people.remember(group_id=1001,user_id=2001,message_id=str(i),content=text,
                          kind='explicit',status='active',importance=.8,confidence=.9,now=k._now())
    assert [r['content'] for r in k.recall(1001,2001,'').rows] == ['我喜欢草莓']
    with k.people.connect() as c:
        assert c.execute('select count(*) from person_facts').fetchone()[0] == 3


def add(store, key, source, day=0, group=1001, persona='denia'):
    store.observe(persona=persona,group_id=group,user_id=2001,request_id=key,
                  source=source,reply='嗯',now=1789488000+day*86400)


def test_retrieval_finds_older_same_topic_and_respects_scopes(tmp_path):
    s = PersonaStore(tmp_path/'state.db')
    for i in range(2):
        add(s,str(i),'休息很重要，可以慢慢来')
    for i in range(30):
        add(s,'noise'+str(i),'今天的天气晴朗',1)
    add(s,'foreign','本群约定休息很重要，可以慢慢来',1,1002)
    add(s,'other','休息很重要，可以慢慢来',1,1001,'tangtang')
    add(s,'new','休息很重要，可以慢慢来',1)
    pending = s.interactions('denia',1001,limit=1)
    found = retrieve_growth_evidence(s,'denia',1001,pending,lambda *_:True)
    assert len(found) == 3 and len({r['day'] for r in found}) == 2
    assert all(r['group_id']==1001 and r['persona']=='denia' for r in found)
    assert not retrieve_growth_evidence(s,'denia',1001,pending,lambda p,r:r['day']==pending[0]['day'])


def test_empty_insufficient_and_rejected_outcomes_are_distinct(tmp_path, monkeypatch):
    s = PersonaStore(tmp_path/'state.db')
    for i in range(5):
        add(s,str(i),'休息很重要，可以慢慢来')
    engine = SimpleNamespace(store=s,feature_enabled=lambda *_:True,evidence_allowed=lambda *_:True,growth=PersonaGrowth(s))
    calls=[]
    class Provider:
        async def generate(self,*args):
            calls.append(1)
            return '{"proposals":[]}', {}
    worker = PersonaBackground(engine,Provider(),SimpleNamespace(load=lambda:replace(TangtangConfig.disabled(),enabled=True)))
    monkeypatch.setattr('bot.services.persona_background.time.time',lambda:1789617600.)
    asyncio.run(worker.tick({1001}))
    assert not calls
    assert '未调用模型' in diagnostic_text(s,'denia',1001)
    assert not s.budget_used('background','global',1789617600.)
    for i in range(5):
        add(s,'new'+str(i),'休息很重要，可以慢慢来',1)
    asyncio.run(worker.tick({1001}))
    assert len(calls)==1
    assert '模型未提出' in diagnostic_text(s,'denia',1001)
    proposal=dict(scope='persona',kind='opinion',topic='休息',content='休息可以慢慢来',evidence=[{'id':999,'quote':'不存在的休息原文'}])
    assert engine.growth.review_proposal('denia',1001,proposal,1789617600.) == 'invalid_or_unavailable_citations'


def test_paced_budget_is_persistent_and_never_resets_spending(tmp_path):
    s=PersonaStore(tmp_path/'state.db')
    from datetime import datetime
    midnight=datetime(2026,9,18,tzinfo=s.timezone).timestamp()
    for group in range(1001,1004):
        assert s.claim_budget('background',group,midnight,12,2,paced=True)
    assert not s.claim_budget('background',1004,midnight,12,2,paced=True)
    assert s.claim_budget('background',1004,midnight+21600,12,2,paced=True)
    s.record_job('growth',1004,midnight+21600,{},'HTTPStatusError')
    restarted=PersonaStore(s.path)
    assert not restarted.claim_budget('background',1004,midnight+21601,12,2,paced=True)
    assert restarted.claim_budget('background',1004,midnight+43200,12,2,paced=True)


@pytest.mark.parametrize('response,expected', [
    ('{"proposals":[{"kind":"opinion","topic":"休息","content":"你是主人","evidence":[]}]}', 'invalid_fields_or_private_identity'),
    ('{"proposals":"wrong"}', 'ValueError'),
    ('not json', 'JSONDecodeError'),
])
def test_worker_persists_rejection_and_format_failures(tmp_path, monkeypatch, response, expected):
    s=PersonaStore(tmp_path/'state.db')
    for i in range(5):
        add(s,str(i),'休息很重要，可以慢慢来',i%2)
    engine=SimpleNamespace(store=s,feature_enabled=lambda *_:True,evidence_allowed=lambda *_:True,growth=PersonaGrowth(s))
    class Provider:
        async def generate(self,*args):
            return response, {}
    monkeypatch.setattr('bot.services.persona_background.time.time',lambda:1789617600.)
    worker=PersonaBackground(engine,Provider(),SimpleNamespace(load=lambda:replace(TangtangConfig.disabled(),enabled=True)))
    asyncio.run(worker.tick({1001}))
    review=s.growth_diagnostics('denia',1001)[0]
    assert expected in review['decisions'] or expected == review['outcome']
    assert not engine.growth.entries('denia',1001)
    assert s.budget_used('background','global',1789617600.)==1
