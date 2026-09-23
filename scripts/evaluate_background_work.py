"""Opt-in paid synthetic summary/profile checks; never send QQ messages."""
import argparse
import asyncio
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.evaluate_persona_profiles import evaluation_config
from bot.services.group_summary import GroupSummaryService
from bot.services.persona_cognition import CognitionStore
from bot.services.persona_profile_worker import ProfileWorker
from bot.services.persona_store import PersonaStore
from bot.services.tangtang_chat import TangtangProvider
from bot.services.tangtang_db import TangtangDb


async def evaluate(env):
    config = replace(evaluation_config(env), group_summary_enabled=True)
    results = []
    with tempfile.TemporaryDirectory(prefix='background-evaluation-') as temp:
        root = Path(temp)
        central = PersonaStore(root / 'state.db')
        central.set_option('denia_v2_enabled', True)
        provider = TangtangProvider()
        for index, messages in enumerate((
            ['聚会原计划周六下午三点。', '更正：改成周日下午四点，地点还没确定。',
             '游戏服务器维护到晚上八点，维护结束前不能登录。', '好的'],
            ['小林猜测活动已经取消。', '主持人还没通知，取消的消息尚未确认。',
             '另一件事：茶叶订单已经退款，不要再说还在等退款。'],
        )):
            db = TangtangDb(root / f'summary-{index}.db')
            stamp = datetime.now().astimezone().isoformat()
            for mid, text in enumerate(messages, 1):
                db.insert_group_message(group_id=101, user_id=200+mid, nickname=f'成员{mid}', text=text,
                                        message_id=str(mid), created_at=stamp)
            service = GroupSummaryService(db, provider, SimpleNamespace(load=lambda: config),
                                          chat_id=lambda: stamp, central=central)
            try:
                await service.apply_batch(101, db.group_summary_pending(101))
                summaries = [dict(summary=r['summary'], unresolved=r['unresolved']) for r in db.group_summary_sources(101)]
                text = json.dumps(summaries, ensure_ascii=False)
                if index == 0:
                    checks = {'corrected_day': '周日' in text, 'corrected_time': '四点' in text or '16:00' in text,
                              'location_unresolved': any(w in text for w in ('地点未', '地点尚未', '地点待', '地点还未')),
                              'separate_topics': len(summaries) >= 2}
                else:
                    checks = {'cancellation_uncertain': any(w in text for w in ('未确认', '未证实', '未通知', '待确认')),
                              'refund_completed': any(w in text for w in ('已退款', '已经退款', '退款已', '退款完成')),
                              'separate_topics': len(summaries) >= 2}
                results.append({'kind': 'summary', 'case': index, 'checks': checks, 'summaries': summaries,
                                'pending': len(db.group_summary_pending(101))})
            except Exception as exc:
                results.append({'kind': 'summary', 'case': index, 'error': type(exc).__name__})
            print(json.dumps(results[-1], ensure_ascii=False), flush=True)
        cognition = CognitionStore(TangtangDb(root / 'profile.db'))
        worker = ProfileWorker(cognition, central, provider, SimpleNamespace(load=lambda: config))
        for mid, text in enumerate(('我喝饮料时只喝茶，不喝咖啡。', '更正我之前的说法：我现在改喝咖啡，不喝茶了。'), 1):
            now = time.time()
            cognition.import_sources([dict(event_key=f'101:{mid}', group_id=101, user_id=201, message_id=str(mid),
                text=text, occurred_at=now, received_at=now, attribution='direct', revision=1, route_version='synthetic')])
            for attempt in range(3):
                await worker.tick()
                with cognition.connect() as conn:
                    job = dict(conn.execute('SELECT state,error,next_attempt FROM persona_profile_jobs WHERE user_id=201').fetchone())
                if job['state'] in {'complete', 'failed'}:
                    break
                delay = max(job['next_attempt'], central.option('profile_provider_retry', {}).get('next_attempt_at', 0)) - time.time()
                if delay > 60:
                    break
                if attempt < 2:
                    await asyncio.sleep(max(.1, delay + .1))
            with cognition.connect() as conn:
                row = conn.execute('SELECT content FROM persona_profile_versions ORDER BY generation DESC LIMIT 1').fetchone()
            results.append({'kind': 'profile', 'case': mid, 'state': job['state'], 'error': job['error'],
                            'content': json.loads(row[0]) if row else None})
            print(json.dumps(results[-1], ensure_ascii=False), flush=True)
        with central.connect() as conn:
            counts = [dict(r) for r in conn.execute('SELECT kind,status,count(*) n,sum(charged_tokens) tokens '
                                                   'FROM background_work_calls GROUP BY kind,status')]
        return {'model': config.model, 'results': results, 'requests': counts, 'qq_messages_sent': 0,
                'note': 'Synthetic checks require semantic review; not a production savings measurement.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--env', type=Path, default=ROOT / '.env')
    parser.add_argument('--report', type=Path, default=ROOT / 'reports/background-work-evaluation.json')
    args = parser.parse_args()
    if not args.live:
        parser.exit(message='No requests sent. Use --live for paid synthetic checks.\n')
    report = asyncio.run(evaluate(args.env))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
