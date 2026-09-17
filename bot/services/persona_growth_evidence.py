"""Bounded lexical topic retrieval with day diversity and group boundaries."""
from __future__ import annotations

import re

from bot.services.persona_memory_store import LOCAL_SCOPE, THIRD_PARTY, STABLE_PERSON, SENSITIVE

_STOP = {'娅娅', '糖糖', '什么', '怎么', '这个', '那个', '可以', '一下', '就是', '我们', '你们', '他们', '觉得', '真的', '还是', '不是', '没有'}


def topic_terms(text: str) -> set[str]:
    return {text[i:i+2] for i in range(len(text)-1)
            if re.fullmatch(r'[\u4e00-\u9fff]{2}', text[i:i+2])} - _STOP


def portable_source(text: str) -> bool:
    return not (LOCAL_SCOPE.search(text) or THIRD_PARTY.search(text) or STABLE_PERSON.search(text)
                or SENSITIVE.search(text) or '记住' in text or '我是' in text)


def retrieve_growth_evidence(store, persona: str, group_id: int, pending: list[dict], allowed) -> list[dict]:
    # Read only delivered evidence, including previously processed rows. Old topics
    # can regain support when a new matching interaction arrives on another day.
    with store.connect() as conn:
        history = [dict(r) for r in conn.execute(
            'SELECT * FROM evidence WHERE persona=? ORDER BY id DESC LIMIT 500', (persona,))]
    history = [r for r in history if r['group_id'] == group_id or portable_source(r['source'])]
    indexed = {r['id']: topic_terms(r['source']) for r in history}
    permitted = {}

    def usable(row):
        if row['id'] not in permitted:
            permitted[row['id']] = allowed(persona, row)
        return permitted[row['id']]

    result = {}
    for seed in pending:
        if not usable(seed):
            continue
        terms = topic_terms(seed['source'])
        if len(terms) < 2:
            continue
        matches = [r for r in history if len(terms & indexed[r['id']]) >= 2
                   and (r['group_id'] == group_id or portable_source(seed['source']))]
        # Select a different-day example first, then closest lexical matches.
        matches.sort(key=lambda r: (r['day'] != seed['day'], len(terms & indexed[r['id']])), reverse=True)
        bundle = {seed['id']: seed}
        for row in matches[:40]:
            if not usable(row):
                continue
            bundle[row['id']] = row
            if len(bundle) == 4:
                break
        if len(bundle) < 3 or len({r['day'] for r in bundle.values()}) < 2:
            continue
        result.update(bundle)
        if len(result) >= 8:
            break
    return list(result.values())[:12]
