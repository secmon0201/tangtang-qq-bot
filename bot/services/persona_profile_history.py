"""Recover original own messages behind old impressions, without old labels."""
import time

from bot.services.persona_profile_store import enqueue


def rebuild_from_history(cognition, source_db, central):
    with cognition.connect() as conn:
        if conn.execute("SELECT 1 FROM persona_profile_migrations WHERE version='independent-profile-1'").fetchone():
            return {'already_prepared': True}
        sources = [dict(r) for r in conn.execute('SELECT * FROM persona_sources WHERE user_id>0')]
    recovered = 0
    with source_db._connect() as raw, central.connect() as history, cognition.connect() as conn:
        conn.execute('BEGIN IMMEDIATE')
        for source in sources:
            args = (source['group_id'], source['user_id'], source['message_id'])
            row = raw.execute('''SELECT text FROM tangtang_group_messages
                WHERE group_id=? AND user_id=? AND message_id=?''', args).fetchone()
            text = row['text'] if row else ''
            if not text:
                row = history.execute('''SELECT source FROM evidence WHERE persona='denia'
                    AND group_id=? AND user_id=? AND request_id=?''',
                    (source['group_id'], source['user_id'], source['event_key'])).fetchone()
                text = row['source'] if row else ''
            if not text:
                row = conn.execute('''SELECT call_text FROM tangtang_calls WHERE group_id=? AND user_id=?
                    AND message_id=? ORDER BY id DESC LIMIT 1''', args).fetchone()
                text = row['call_text'] if row else ''
            if text and text != source['text'] and source['text'] in text:
                conn.execute('UPDATE persona_sources SET text=?,revision=revision+1 WHERE event_key=?', (text, source['event_key']))
                recovered += 1
        users = {s['user_id'] for s in sources}
        users.update(r[0] for r in conn.execute('''SELECT DISTINCT m.user_id FROM person_semantic_memory m
            JOIN persona_claim_metadata c ON c.memory_id=m.id WHERE c.kind='impression' AND m.status='active' '''))
        for user in users:
            enqueue(conn, user, time.time())
        changed = conn.execute("""UPDATE person_semantic_memory SET status='review' WHERE status IN ('active','candidate')
            AND id IN (SELECT memory_id FROM persona_claim_metadata WHERE kind='impression')
            AND id NOT IN (SELECT memory_id FROM persona_profile_approved)""").rowcount
        conn.execute("INSERT INTO persona_profile_migrations VALUES('independent-profile-1',?)", (time.time(),))
    return {'queued_people': len(users), 'recovered_full_messages': recovered, 'old_impressions_under_review': changed}
