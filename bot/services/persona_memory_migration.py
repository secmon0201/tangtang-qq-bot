"""Additive, idempotent import of legacy character memories and relationships."""
from __future__ import annotations

from bot.services.persona_memory_store import PersonMemoryStore, SENSITIVE, fact_scope


def migrate_people(db) -> dict[str, int]:
    store = PersonMemoryStore(db)
    version = "shared-people-v1"
    with store.connect() as conn:
        if conn.execute("SELECT 1 FROM person_memory_migrations WHERE version=?", (version,)).fetchone():
            return {"already_applied": 1}
        facts = [dict(r) for r in conn.execute("SELECT * FROM tangtang_memories ORDER BY id")]
        relations = [dict(r) for r in conn.execute("SELECT * FROM tangtang_relationship_state ORDER BY updated_at")]
    count = 0
    for r in facts:
        if SENSITIVE.search(r['content']):
            continue
        if r['status'] == 'deleted':
            store.restrict(r['user_id'], 0, r['content'])
            continue
        if r['status'] not in {'active', 'candidate'}:
            continue
        result = store.remember(group_id=r['group_id'], user_id=r['user_id'],
            message_id=r['source_message_id'] or f"legacy:{r['id']}", content=r['content'],
            kind=r['kind'], status=r['status'], importance=r['importance'], confidence=r['confidence'],
            now=r['updated_at'], scope_group=fact_scope(r['content'], r['group_id']))
        count += result is not None
    grouped = {}
    for r in relations:
        old = grouped.get(r['user_id'])
        grouped[r['user_id']] = dict(r, familiarity=max(r['familiarity'], old['familiarity'] if old else 0))
    with store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for uid, r in grouped.items():
            conn.execute("INSERT OR IGNORE INTO person_relations VALUES(?,?,?,?,?,?)",
                         (uid, r['familiarity'], r['warmth'], .5, r['interaction_count'], r['updated_at']))
        conn.execute("INSERT OR IGNORE INTO person_events SELECT user_id,CAST(group_id AS TEXT)||':'||message_id FROM tangtang_calls WHERE message_id<>''")
        conn.execute("INSERT OR IGNORE INTO person_memory_migrations VALUES(?)", (version,))
    return {"facts_processed": count, "people_processed": len(grouped)}
