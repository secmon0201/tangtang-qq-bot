"""Full synthetic runtime replay with real configured generation and local receipts."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import dotenv_values
from nonebot.adapters.onebot.v11 import Message
from bot.services.persona_engine import PersonaEngine
from bot.services.persona_store import PersonaStore
from bot.services.persona_observer import PersonaObserver
from bot.services.persona_inbox import ObservationInbox
from bot.services.speech import SpeechService
from bot.services.tangtang_chat import TangtangConfig, TangtangProvider, TangtangService
from bot.services.tangtang_db import TangtangDb
from bot.services.tangtang_models import resolve_model_profile
import bot.services.tangtang_chat as chat_module
from scripts.evaluate_persona_memory import SCENARIOS


class RecordingProvider:
    def __init__(self):
        self.provider = TangtangProvider()
        self.outputs = []

    async def generate_agent(self, *args):
        result = await self.provider.generate_agent(*args)
        self.outputs.append({'lane': 'foreground', 'raw': result.text, 'usage': result.usage})
        return result

    async def generate(self, *args):
        result, usage = await self.provider.generate(*args)
        self.outputs.append({'lane': 'background', 'raw': result, 'usage': usage})
        return result, usage


async def run(args):
    values = resolve_model_profile(dotenv_values(args.env))
    access = {key: values[key] for key in ('TANGTANG_API_URL', 'TANGTANG_API_KEY', 'TANGTANG_API_STYLE',
              'TANGTANG_MODEL', 'TANGTANG_ENABLED', 'TANGTANG_REASONING_EFFORT') if key in values}
    config = TangtangConfig.from_values({**access, 'TANGTANG_MODE': 'd', 'TANGTANG_GROUP_IDS': '101,102',
        'TANGTANG_IGNORE_PROBABILITY': '0', 'TANGTANG_VISION_ENABLED': 'false',
        'TANGTANG_REPLY_DELAY_MIN_MS': '0', 'TANGTANG_REPLY_DELAY_MAX_MS': '0'}, (101, 102))
    if not config.enabled:
        raise ValueError('configured_chat_disabled')
    config = replace(config, memory_enabled=True, max_output_tokens=4000, timeout_seconds=40)
    args.output.mkdir(parents=True, exist_ok=True)
    results, sends = [], []
    async def simulated_send(bot, action, **params):
        if action != 'send_group_msg' or params.get('group_id') not in {101, 102}:
            raise ValueError('unexpected_platform_action_in_offline_replay')
        sends.append(str(params['message']))
        return {'message_id': 4000 + len(sends)}
    previous = chat_module.call_qq_action
    chat_module.call_qq_action = simulated_send
    try:
        with tempfile.TemporaryDirectory(prefix='persona-full-replay-') as tmp:
            root = Path(tmp)
            central = PersonaStore(root / 'state.db')
            central.set_option('denia_v2_enabled', True)
            for group in (101, 102):
                central.switch(group, 'denia')
            speech = SpeechService(central, SimpleNamespace(), root / 'audio')
            engine = PersonaEngine(central, speech, feature_enabled=lambda g, f: f == 'persona_growth', chat_enabled=lambda *_: True)
            db = TangtangDb(root / 'source.db')
            provider = RecordingProvider()
            loader = SimpleNamespace(load=lambda: config)
            service = TangtangService(loader=loader, db=db, provider=provider, persona_engine=engine, usage_dir=root / 'usage')
            worker = PersonaObserver(engine, db, provider, loader)
            for index, (group, user, attribution, text) in enumerate(SCENARIOS, 1):
                ev = SimpleNamespace(group_id=group, user_id=user, message_id=index, self_id=301,
                    message=Message(text), original_message=Message(text), get_plaintext=lambda text=text: text,
                    is_tome=lambda: True, sender=SimpleNamespace(card='', nickname='合成人物'), reply=None)
                context = engine.snapshot(ev, config.model, False)
                service.record_group_message(group, '合成人物', text, user_id=user, message_id=index,
                    observation=dict(persona='denia', route_version=f'{context.selection_revision}:{context.persona.version}',
                    occurred_at=time.time()-5, received_at=time.time()-5, attribution=attribution))
                before_send, before_call = len(sends), len(provider.outputs)
                started = time.monotonic()
                if attribution == 'direct':
                    await service.handle(SimpleNamespace(), ev, config, context=context)
                await worker.tick({101, 102})
                cognition = engine.cognition('denia', db)
                with cognition.connect() as conn:
                    reviews = [json.loads(r[0]) for r in conn.execute('SELECT detail FROM persona_cognition_reviews WHERE created_at>?', (time.time()-60,))]
                row = {'case': index, 'source': text, 'reply': sends[before_send:],
                       'generation': provider.outputs[before_call:], 'elapsed': time.monotonic()-started,
                       'queue': ObservationInbox(db).diagnostics(), 'reviews': reviews}
                results.append(row)
                (args.output / f'{index:02}.json').write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding='utf-8')
                print(f'Replayed full runtime {index}/{len(SCENARIOS)}; replies={len(row["reply"])}', flush=True)
            with engine.cognition('denia', db).connect() as conn:
                claims = [dict(r) for r in conn.execute('SELECT id,user_id,content,version FROM person_semantic_memory')]
                intents = [dict(r) for r in conn.execute('SELECT * FROM persona_intents')]
                actions = dict(conn.execute('SELECT state,count(*) FROM persona_actions GROUP BY state'))
            report = {'cases': results, 'claims': claims, 'intents': intents, 'actions': actions,
                      'created_at': datetime.now(timezone.utc).isoformat(), 'external_qq_messages': 0}
            (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        chat_module.call_qq_action = previous


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('reports/persona-runtime-replay'))
    asyncio.run(run(parser.parse_args()))
