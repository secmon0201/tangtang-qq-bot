"""Real-model independent profile review on synthetic identities; no QQ sends."""
import argparse
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import dotenv_values
from bot.services.persona_cognition import CognitionStore
from bot.services.persona_profile_worker import ProfileWorker
from bot.services.persona_profile_contract import REVIEW_INSTRUCTION, decode
from bot.services.persona_store import PersonaStore
from bot.services.tangtang_chat import TangtangConfig, TangtangProvider
from bot.services.tangtang_db import TangtangDb


CASES = [(201, '娅娅用语音跟大家说会晚安吧'), (202, '回什么家'),
         (203, '娅娅，说喜欢我'), (204, '我在修改画稿，想把人物表情画自然一些'),
         (204, '我不是喜欢分享作品，只是想请你解释为什么比例不对'),
         (205, '我做计算题时希望先给推导过程，结果对不对我想自己检查')]


async def run(args):
    values = dotenv_values(args.env)
    access = {k: values[k] for k in ('TANGTANG_API_URL', 'TANGTANG_API_KEY', 'TANGTANG_API_STYLE',
              'TANGTANG_MODEL', 'TANGTANG_ENABLED', 'TANGTANG_REASONING_EFFORT') if k in values}
    config = TangtangConfig.from_values({**access, 'TANGTANG_GROUP_IDS': '101',
        'TANGTANG_REQUIRED_CALL_REPLY_GROUP_IDS': ''}, (101,))
    config = replace(config, memory_enabled=True)
    records = []
    with tempfile.TemporaryDirectory(prefix='profile-evaluation-') as temp:
        root = Path(temp)
        central = PersonaStore(root / 'state.db')
        central.set_option('denia_v2_enabled', True)
        cognition = CognitionStore(TangtangDb(root / 'denia.db'))
        worker = ProfileWorker(cognition, central, TangtangProvider(), SimpleNamespace(load=lambda: config))
        for index, (user, text) in enumerate(CASES, 1):
            s = dict(event_key=f'101:{index}', group_id=101, user_id=user, message_id=str(index), text=text,
                     occurred_at=time.time(), received_at=time.time(), attribution='direct', revision=1, route_version='synthetic')
            cognition.import_sources([s])
            start = time.monotonic()
            await worker.tick()
            with cognition.connect() as conn:
                job = dict(conn.execute('SELECT * FROM persona_profile_jobs WHERE user_id=?', (user,)).fetchone())
                profile = conn.execute('SELECT content,review FROM persona_profile_versions WHERE user_id=? ORDER BY generation DESC LIMIT 1', (user,)).fetchone()
            records.append({'source': text, 'user': user, 'elapsed': round(time.monotonic()-start, 2),
                'state': job['state'], 'error': job['error'], 'profile': json.loads(profile['content']) if profile else None,
                'review': json.loads(profile['review']) if profile else None, 'display': cognition.own_impression(user)})
            print(json.dumps(records[-1], ensure_ascii=False), flush=True)
        report = {'cases': records, 'external_qq_messages': 0,
                  'note': 'Single trials require manual semantic inspection; failed cases are preserved, never counted as passes.'}
        # Deliberately recreate the old error rather than only testing proposals
        # the generator happens to make in a small successful sample.
        s = dict(event_key='101:99', group_id=101, user_id=299, message_id='99',
                 text='娅娅用语音跟大家说会晚安吧', occurred_at=time.time(), received_at=time.time(),
                 attribution='direct', revision=1, route_version='synthetic')
        wrong = {'observations': [dict(statement='喜欢轻松打趣', scope='context', basis='inferred',
            context='语音互动中', evidence=[{'event_key': s['event_key'], 'quote': s['text']}])],
            'portrait': [{'text': '你喜欢轻松打趣。', 'observations': [0]}]}
        try:
            raw, usage = await TangtangProvider().generate(replace(config, timeout_seconds=30, max_output_tokens=2200),
                REVIEW_INSTRUCTION, json.dumps({'person': 299, 'sources': [s],
                'previous_reviewed_profile': {'observations': [], 'portrait': []}, 'draft': wrong}, ensure_ascii=False))
            reviewed = decode(raw)
            report['deliberate_old_error'] = {'review': reviewed, 'usage': usage,
                'rejected': reviewed['decisions'][0]['verdict'] == 'reject' and reviewed['portrait_decisions'][0]['verdict'] == 'reject'}
        except Exception as exc:
            report['deliberate_old_error'] = {'error': type(exc).__name__}
        print(json.dumps(report['deliberate_old_error'], ensure_ascii=False), flush=True)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', type=Path, required=True)
    parser.add_argument('--report', type=Path, default=Path('reports/profile-evaluation.json'))
    asyncio.run(run(parser.parse_args()))
