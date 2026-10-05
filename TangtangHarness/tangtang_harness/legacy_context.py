"""Explicit legacy context fallback; never used by the cache-first path."""
from __future__ import annotations

import copy
import hashlib
import re
from dataclasses import asdict
from typing import Any

from .config import HarnessConfig, ModelProfile
from .media import enforce_payload_limits
from .models import build_payload
from .store import Store, encode
from .types import InboundEvent
from .reply_media import expression_prompt
from .topics import PersonaTopics, TopicSelection
from .message_text import normalize_voice
from .context import (ContextBudgetError, PreparedContext, context_history,
                      fixed_prefix, content_token_estimate, text_token_estimate,
                      message_tokens, frozen_event_text, visible_event,
                      dialogue_examples, without_images)


def build_legacy_context(config: HarnessConfig, store: Store, event: InboundEvent,
                         profile: ModelProfile, *, tool_facts: Any = None,
                         images: list[dict[str, Any]] | None = None,
                         input_budget_tokens: int | None = None, proactive: bool = False) -> PreparedContext:
    if profile.context_limit is None:
        raise ContextBudgetError('请先填写模型档案的真实 context_limit；旧配置没有该容量，不能按模型名称猜测')
    blocked = store.blocked_users(event.group_id or 0)
    event = visible_event(event, blocked)
    snapshot, history, excluded_turns = context_history(store, event.session_key, blocked)
    revision = snapshot["revision"] if snapshot else 0
    cutoff = snapshot["cutoff_turn_id"] if snapshot else 0
    legacy = store.legacy_personal_context(event.session_key, event.user_id, persona=config.persona)
    selected_records = {'memory': [], 'profile': [], 'cognition': [], 'growth': []}
    selected_legacy_sources = []
    restrictions = store.memory_restrictions(event.session_key) + legacy['restrictions']
    forgotten_text = '[已停用的个人记忆]'
    def recalled(value: Any) -> Any:
        if isinstance(value, str):
            value = normalize_voice(value)
            return '\n'.join(forgotten_text if any(needle in line for needle in restrictions) else line
                             for line in value.split('\n'))
        if isinstance(value, list):
            return [recalled(item) for item in value]
        if isinstance(value, dict):
            return {key: recalled(item) if key not in {'url', 'image_url'} else item for key, item in value.items()}
        return value
    def has_visible_text(value: Any) -> bool:
        if isinstance(value, str):
            return any(line.strip() and line.strip() != forgotten_text for line in value.split('\n'))
        if isinstance(value, list):
            return any(has_visible_text(item) for item in value)
        if isinstance(value, dict):
            text_keys = [key for key in ('content', 'text') if key in value]
            return any(has_visible_text(value[key]) for key in text_keys) if text_keys else any(
                has_visible_text(item) for item in value.values())
        return False
    def recalled_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [row for row in recalled(rows) if has_visible_text(row['content'])]
    prefix = fixed_prefix(config)
    prefix_hash = hashlib.sha256(prefix.encode()).hexdigest()
    messages: list[dict[str, Any]] = [{"role": "system", "content": prefix}]
    layers = [{"name": "fixed", "source": "persona + output protocol", "text": prefix,
               "estimated_tokens": text_token_estimate(prefix, profile.tokenizer)[0], "stable": True}]
    if snapshot:
        text = "[已完成历史快照，revision=" + str(revision) + "]\n" + encode(recalled(snapshot["content"]))
        messages.append({"role": "user", "content": text})
        layers.append({"name": "snapshot", "source": f"snapshot:{revision}", "text": text,
                       "estimated_tokens": text_token_estimate(text, profile.tokenizer)[0], "stable": True})
    rounds: list[list[dict[str, Any]]] = []
    for turn in history:
        rounds.append([{"role": "user", "content": recalled(copy.deepcopy(turn["user_content"]))},
                       {"role": "assistant", "content": recalled("\n".join(turn["messages"]))}])
    session = store.session(event.session_key)
    cursor = int(session.get("context_cursor", 0))
    raw = store.events(event.session_key, limit=30, after_id=cursor)
    raw_scopes = store.event_scopes([row['event_key'] for row in raw])
    excluded = [row["event_key"] for row in raw
                if row['user_id'] in blocked or not raw_scopes[row['event_key']]]
    relevant = [row for row in raw if row["event_key"] != event.key and row["event_key"] not in excluded]
    dynamic_parts = ["[本轮动态状态]\n" + ("一对一私聊必须回应。" if event.group_id is None else "只使用当前群资料交流。")]
    if relevant:
        dynamic_parts.append(recalled("[自上次送达后新增消息]\n" + "\n".join(
            frozen_event_text(visible_event(InboundEvent.from_dict(row["payload"]), blocked)) for row in relevant)))
    if event.group_id is None:
        own_rows = store.own_events(event.user_id, 30)
        own_scopes = store.event_scopes([row['event_key'] for row in own_rows])
        own = [row for row in own_rows
               if row["session_key"] != event.session_key
               and row['user_id'] not in blocked
               and own_scopes[row['event_key']]]
        if own:
            dynamic_parts.append(recalled("[该用户本人的群发言，不包含其他成员或群摘要]\n" + "\n".join(
                frozen_event_text(visible_event(InboundEvent.from_dict(row["payload"]), blocked)) for row in own)))
    if config.memory_enabled:
        memories = recalled_records(store.get_setting("memory:" + event.session_key + ":" + str(event.user_id), []))
        terms = {event.text[index:index + 2] for index in range(len(event.text) - 1)
                 if re.fullmatch(r'[\u4e00-\u9fff]{2}', event.text[index:index + 2])}
        related = sorted(memories, key=lambda row: -sum(term in str(row.get('content', '')) for term in terms))
        basic = [row for row in memories if row.get('kind') in {'alias', 'self_description', 'identity'}]
        chosen = [*related[:4], *basic[:4], *related]
        seen_ids = set()
        memories = []
        for row in chosen:
            identity = row.get('id', row.get('content'))
            if identity not in seen_ids:
                memories.append(row)
                seen_ids.add(identity)
            if len(memories) == 8:
                break
        if memories:
            dynamic_parts.append("[当前用户记忆，作为资料]\n" + encode(memories))
            selected_records['memory'] = [{'id': row.get('id'), 'version': row.get('version'),
                'scope': event.session_key, 'kind': row.get('kind')} for row in memories]
        impressions = recalled(store.impressions(event.session_key, event.user_id))
        if impressions:
            dynamic_parts.append("[当前用户交流印象，暂时观察并非身份结论]\n" + encode(impressions))
        legacy_memory = {key: recalled_records(legacy[key]) for key in ('memory', 'profile')}
        if any(legacy_memory.values()):
            dynamic_parts.append('[旧资料已导入，保留原版本和来源证据]\n' + encode(legacy_memory))
            selected_legacy_sources.extend(item['source'] for rows in legacy_memory.values() for item in rows)
        profile_state = recalled(store.get_setting('profile:' + event.session_key + ':' + str(event.user_id)))
        if profile_state and has_visible_text(profile_state.get('profile', profile_state.get('content', ''))):
            dynamic_parts.append('[独立复核通过的当前用户画像]\n' + encode(profile_state))
            selected_records['profile'] = [{'version': profile_state.get('version'), 'scope': event.session_key,
                'user_id': event.user_id}]
    if config.cognition_enabled:
        cognition = recalled_records(store.cognition(event.session_key, event.user_id))
        if cognition:
            dynamic_parts.append("[当前用户的约定、意图与状态，含版本和证据]\n" + encode(cognition))
            selected_records['cognition'] = [{'id': row['id'], 'version': row['version'],
                'scope': event.session_key, 'kind': row['kind']} for row in cognition]
        legacy_cognition = recalled_records(legacy['cognition'])
        if legacy_cognition:
            dynamic_parts.append('[导入的本人旧认知资料，不自动执行待办]\n' + encode(legacy_cognition))
            selected_legacy_sources.extend(item['source'] for item in legacy_cognition)
    if config.growth_enabled and store.group_feature_enabled(event.group_id, 'persona_growth'):
        growth = store.growth(event.session_key)
        suppressed = store.get_setting("growth_disabled:" + event.session_key, [])
        growth = [row for row in growth if row['id'] not in suppressed][:20]
        def visible_growth(row):
            if not blocked:
                return True
            if row.get('provenance'):
                evidence = row['provenance']['evidence']
                return bool(evidence) and all(item.get('user_id') and int(item['user_id']) not in blocked for item in evidence)
            return (store.event(row['event_key']) or {}).get('user_id') not in blocked
        growth = [row for row in growth if visible_growth(row)]
        public_growth = recalled_records([{key: row[key] for key in ('id', 'scope', 'content', 'kind', 'version')} for row in growth])
        if public_growth:
            dynamic_parts.append("[已保存的公共表达成长，不修改核心人格]\n" + encode(public_growth))
            selected_records['growth'] = [{'id': row['id'], 'version': row['version'], 'scope': row['scope']} for row in public_growth]
    group_state = recalled(store.get_setting("group_state:" + event.session_key, {}))
    summary_revision = group_state.get('summary_revision')
    group_state = {key: item for key, item in group_state.items() if key not in {'summary_revision', 'summary_published_at'}}
    if blocked and ('summary_source_users' not in group_state or any(user in blocked for user in group_state['summary_source_users'])):
        group_state = {key: item for key, item in group_state.items() if key != 'summary'}
    if group_state:
        dynamic_parts.append("[当前会话状态]\n" + encode(group_state))
    if tool_facts:
        visible_tool_facts = recalled(tool_facts)
        dynamic_parts.append("[本地工具真实结果]\n" + (visible_tool_facts if isinstance(visible_tool_facts, str) else encode(visible_tool_facts)))
    public_topic = (PersonaTopics(store).select(event, event.text, persona=config.persona, proactive=proactive)
                    if store.group_feature_enabled(event.group_id, 'persona_topics') else TopicSelection())
    if public_topic.text:
        dynamic_parts.append(public_topic.text)
    examples = dialogue_examples(config, event.text)
    if examples:
        dynamic_parts.append(recalled('[本地相关对白风格参考，不能当成本轮事实或机械照抄]\n' + '\n'.join(examples)))
    expressions = (expression_prompt(config.root, event.text, config.persona)
                   if store.group_feature_enabled(event.group_id, 'persona_expressions') else '')
    if expressions:
        dynamic_parts.append(expressions)
    dynamic_parts.append('[本轮语音设置]\n' + ('语音已开启，短对白可以选择语音；最终合成及送达由程序完成。'
                           if config.speech_enabled and store.group_feature_enabled(event.group_id, 'persona_voice')
                           else '语音已关闭，本轮使用文字。'))
    user_text = "\n\n".join((*dynamic_parts, frozen_event_text(event)))
    user_content: Any = user_text
    if images:
        user_content = [{"type": "text", "text": user_text}, *copy.deepcopy(images)]
    capacity = profile.context_limit - profile.max_output_tokens - 1024
    budget = min(input_budget_tokens or config.input_budget_tokens, capacity)
    hard = int(budget * config.hard_budget_ratio)
    soft = int(budget * config.soft_budget_ratio)
    all_messages = [*messages, *(message for round_items in rounds for message in round_items),
                    {"role": "user", "content": user_content}]
    if not profile.vision:
        messages = without_images(messages)
        rounds = [without_images(items) for items in rounds]
        user_content = without_images([{'role': 'user', 'content': user_content}])[0]['content']
        all_messages = without_images(all_messages)
    else:
        enforce_payload_limits({"messages": all_messages})
    round_tokens = [sum(content_token_estimate(item['content'], profile.tokenizer)[0] + 8 for item in items) for items in rounds]
    fixed_tokens = message_tokens(messages, profile)
    history_tokens = sum(round_tokens)
    current_tokens = content_token_estimate(user_content, profile.tokenizer)[0] + 8
    before = fixed_tokens + history_tokens + current_tokens
    trimmed_events = []
    while relevant and fixed_tokens + history_tokens + current_tokens > hard:
        trimmed_events.append(relevant.pop(0)['event_key'])
        index = next((index for index, part in enumerate(dynamic_parts) if part.startswith('[自上次送达后新增消息]')), None)
        if index is None:
            break
        if relevant:
            dynamic_parts[index] = recalled('[自上次送达后新增消息]\n' + '\n'.join(
                frozen_event_text(visible_event(InboundEvent.from_dict(row['payload']), blocked)) for row in relevant))
        else:
            dynamic_parts.pop(index)
        user_text = '\n\n'.join((*dynamic_parts, frozen_event_text(event)))
        if isinstance(user_content, list):
            user_content[0]['text'] = user_text
        else:
            user_content = user_text
        current_tokens = content_token_estimate(user_content, profile.tokenizer)[0] + 8
    removed: list[int] = []
    start = 0
    while start < len(rounds) and fixed_tokens + history_tokens + current_tokens > hard:
        removed.append(history[start]['id'])
        history_tokens -= round_tokens[start]
        start += 1
    selected = rounds[start:]
    actual_messages = [*messages, *(item for rows in selected for item in rows),
                       {"role": "user", "content": user_content}]
    estimated = fixed_tokens + history_tokens + current_tokens
    if estimated > budget:
        raise ContextBudgetError(f"当前输入与固定资料预计 {estimated} token，超过输入预算 {budget}；请调大预算或更换模型")
    history_view = [item for rows in selected for item in rows]
    layers.append({"name": "history", "source": f"completed turns after {cutoff}", "text": encode(history_view),
                   "estimated_tokens": sum(content_token_estimate(item["content"], profile.tokenizer)[0] for item in history_view),
                   "stable": True})
    component_names = ('routing', 'raw_events', 'own_events', 'memory', 'impressions', 'legacy_memory',
                       'profile', 'cognition', 'legacy_cognition', 'growth', 'group_state', 'tools', 'dialogue_examples')
    headings = ('[本轮动态状态]', '[自上次送达后新增消息]', '[该用户本人的群发言', '[当前用户记忆',
                '[当前用户交流印象', '[旧资料已导入', '[独立复核通过', '[当前用户的约定', '[导入的本人旧认知',
                '[已保存的公共表达成长', '[当前会话状态]', '[本地工具真实结果]', '[本地相关对白风格参考')
    for part in dynamic_parts:
        name = ('public_topics' if public_topic.text and part == public_topic.text else
                next((component_names[index] for index, heading in enumerate(headings) if part.startswith(heading)), 'dynamic'))
        layers.append({'name': name, 'source': event.session_key, 'text': part,
                       'estimated_tokens': text_token_estimate(part, profile.tokenizer)[0], 'stable': False})
    current_text = frozen_event_text(event)
    current_content = [{'type': 'text', 'text': current_text}, *copy.deepcopy(user_content[1:])] if isinstance(user_content, list) else current_text
    layers.append({"name": "current", "source": event.key, "text": current_text,
                   "estimated_tokens": content_token_estimate(current_content, profile.tokenizer)[0], "stable": False})
    actual_event = store.event(event.key)
    next_cursor = max((row["id"] for row in raw), default=cursor)
    if actual_event:
        next_cursor = max(next_cursor, actual_event["id"])
    telemetry = {"layers": layers, "static_prefix_hash": prefix_hash,
                 "estimated_input_tokens": estimated, "input_tokens_before_trim": before,
                 "token_count_source": "local_estimate", "input_budget_tokens": budget,
                 "soft_watermark": soft, "hard_watermark": hard, "snapshot_revision": revision,
                 "trimmed_turn_ids": removed, "compaction_due": before >= soft and bool(history),
                 'trimmed_event_keys': trimmed_events,
                 'excluded_turn_ids': excluded_turns, 'blocked_user_count': len(blocked),
                 'context_filter_users': sorted(blocked),
                 "context_cursor": next_cursor,
                 "excluded_event_keys": excluded,
                 'public_topic': asdict(public_topic) if public_topic.text else None,
                 'excluded_sources': [
                     *({'event_key': row['event_key'], 'reason': 'filtered_user' if row['user_id'] in blocked else 'not_chat_scope'}
                       for row in raw if row['event_key'] in excluded),
                     *({'event_key': key, 'reason': 'input_budget'} for key in trimmed_events),
                     *({'turn_id': identity, 'reason': 'input_budget'} for identity in removed),
                     *({'turn_id': identity, 'reason': 'filtered_source'} for identity in excluded_turns)],
                 "sources": {"events": [row["event_key"] for row in relevant],
                             'records': selected_records, 'summary_revision': summary_revision if 'summary' in group_state else None,
                             "history_turn_ids": [turn["id"] for turn in history if turn["id"] not in removed],
                             "memory_scope": event.session_key + ":" + str(event.user_id),
                             'legacy_sources': selected_legacy_sources,
                             "tools_included": bool(tool_facts)},
                 "budget_action": "trimmed" if removed or trimmed_events else "none"}
    key = hashlib.sha256(f"{event.session_key}:{profile.id}:{prefix_hash}:{revision}:{encode(sorted(blocked))}".encode()).hexdigest()
    payload = build_payload(profile, actual_messages, cache_key=key)
    return PreparedContext(actual_messages, payload, user_content, layers, telemetry, revision)

