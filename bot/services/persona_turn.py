"""Foreground memory proposal lifecycle, injected through the existing chat service."""
from __future__ import annotations

import json
import re

from bot.services.persona_actions import PersonaActions
from bot.services.persona_contracts import MergeResult, proposal_object
from bot.services.persona_inbox import ObservationInbox
from bot.services.persona_memory_contract import MemoryWriteResult, memory_requested
from bot.services.persona_impressions import impression_requested


class PersonaTurn:
    def __init__(self, engine, db, context, event):
        self.context = context
        self.store = engine.cognition(context.persona.key, db)
        self.inbox = ObservationInbox(db)
        ids = getattr(event, 'source_message_ids', (event.message_id,))
        self.sources = self.inbox.sources(context.group_id, ids, context.persona.key,
                                         direct=not context.proactive, owner=context.request_id)
        # Do not adopt other speakers in a merged group observation.
        self.sources = [s for s in self.sources if s['user_id'] == context.user_id]
        frozen_version = f'{context.selection_revision}:{context.persona.version}'
        if any(s['route_version'] != frozen_version for s in self.sources):
            raise ValueError('persona_route_changed_after_receive')
        self.snapshot = self.store.snapshot(context.request_id, context.user_id, context.group_id,
                                            self.sources, event.get_plaintext())
        self.actions = PersonaActions(self.store)
        self.proposal = {}
        self.result = MergeResult()
        self.expected = {}
        self.action_id = ''
        self.intent_version = None
        self.source_text = event.get_plaintext()

    def current(self):
        return self.store.current(self.expected)

    def release(self):
        self.inbox.release(self.sources, self.context.request_id)

    def apply(self, output: str) -> str:
        self.proposal = proposal_object(output)
        self.result = self.store.merge(self.proposal, self.snapshot)
        self._dependencies()
        # An answer might already claim a rejected update; regenerate only the
        # answer from the accepted snapshot, then leave extraction to the worker.
        if self.result.rejected:
            raise ValueError('rejected_proposal_requires_regeneration')
        self.inbox.acknowledge(self.sources)
        return self._reply_json()

    def _dependencies(self):
        refs = self.proposal.get('used_refs', [])
        if not isinstance(refs, list) or any(str(ref) not in self.snapshot.expected for ref in refs):
            raise ValueError('unknown_used_reference')
        hard_refs = [str(r['id']) for r in self.snapshot.claims if r['kind'] in {'fact', 'impression'}]
        versions = {**self.snapshot.expected, **self.result.expected}
        self.expected = {str(ref): versions[str(ref)] for ref in (*refs, *hard_refs)}
        intent = self.proposal.get('use_intent', '')
        if intent:
            known = next((r for r in self.snapshot.intents if r['id'] == intent), None)
            if not known:
                raise ValueError('unknown_intent_reference')
            self.intent_version = self.result.intent_versions.get(intent, known['version'])

    def _reply_json(self):
        normalized = dict(self.proposal)
        normalized['decision'] = 'reply' if normalized['decision'] in {'reply', 'clarify', 'resume'} else 'silent'
        return json.dumps(normalized, ensure_ascii=False)

    def repaired_reply(self, output):
        self.proposal = proposal_object(output)
        # Repair may change wording, never persist a second batch of learning.
        for key in ('claims', 'states', 'intents'):
            self.proposal[key] = []
        self.result.expected = dict(self.snapshot.expected)
        self._dependencies()
        return self._reply_json()

    def receipt(self):
        ids = tuple(i for i in self.result.accepted if i.startswith('s:'))
        requested = memory_requested(self.source_text) and bool(re.search(r'记住|记下|记一下|存入|保存|更正|纠正', self.source_text))
        return MemoryWriteResult(requested, 'saved' if ids else 'deferred', ids,
                                 tuple(self.result.rejected))

    def repair_prompt(self, reason):
        self.expected = {}
        self.snapshot = self.store.snapshot(self.context.request_id, self.context.user_id,
                                            self.context.group_id, self.sources, self.source_text)
        return (self.snapshot.prompt() + '\n上一份提议被校验拒绝，本轮还未发送。重新生成完整JSON，'
                '证据键和原话必须逐字取自目录；已有成功写入内容已在下面快照中，不重复新增。'
                '本次仅修复回复，claims/states/intents全部填[]，后台稍后继续整理失败项。'
                '只基于当前原话和最新有效记忆回答，禁止声称已保存未在快照中的资料。'
                '不为修复而补造证据，不使用此前草稿作为事实。拒绝原因：' + reason[:300])

    def control_reply(self, user_id, text, fallback):
        return self.store.own_impression(user_id) if impression_requested(text) else fallback

    def prepare(self, parts, suffix='text'):
        self.action_id = self.context.request_id + ':' + suffix
        return self.actions.prepare(self.action_id, self.context.user_id, self.context.group_id,
                                    parts, self.expected, intent_id=self.proposal.get('use_intent', ''),
                                    intent_version=self.intent_version)

    def start(self, index):
        return self.actions.start(self.action_id, index)

    def delivered(self, index, message_id):
        self.actions.result(self.action_id, index, message_id=message_id)

    def failed(self, index, reason, *, definite=False):
        self.actions.result(self.action_id, index, error=reason, definite_failure=definite)
