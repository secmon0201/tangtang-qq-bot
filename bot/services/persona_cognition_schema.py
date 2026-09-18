"""Additive schema in the existing persona history, extending semantic memory."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS persona_sources(
 event_key TEXT PRIMARY KEY, user_id INTEGER NOT NULL, group_id INTEGER NOT NULL,
 message_id TEXT NOT NULL, text TEXT NOT NULL, occurred_at REAL NOT NULL,
 received_at REAL NOT NULL, attribution TEXT NOT NULL, revision INTEGER NOT NULL,
 route_version TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS persona_processing_receipts(
 event_key TEXT NOT NULL, revision INTEGER NOT NULL, extractor TEXT NOT NULL,
 processed_at REAL NOT NULL, PRIMARY KEY(event_key,revision,extractor));
CREATE TABLE IF NOT EXISTS persona_claim_metadata(
 memory_id INTEGER PRIMARY KEY, kind TEXT NOT NULL, topic TEXT NOT NULL,
 applicability TEXT NOT NULL, assertion_type TEXT NOT NULL, confidence REAL NOT NULL,
 occurred_at REAL NOT NULL, provenance TEXT NOT NULL, valid_until REAL NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_claim_topic ON persona_claim_metadata(kind,topic);
CREATE TABLE IF NOT EXISTS persona_claim_support(
 memory_id INTEGER NOT NULL, version INTEGER NOT NULL, event_key TEXT NOT NULL,
 quote TEXT NOT NULL, stance TEXT NOT NULL, PRIMARY KEY(memory_id,version,event_key,quote,stance));
CREATE TABLE IF NOT EXISTS persona_state_factors(
 id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, topic TEXT NOT NULL,
 cause TEXT NOT NULL, label TEXT NOT NULL, strength REAL NOT NULL,
 half_life REAL NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS persona_episodes(
 id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, group_id INTEGER NOT NULL,
 source_key TEXT NOT NULL, action_id TEXT NOT NULL DEFAULT '',
 summary TEXT NOT NULL, outcome TEXT NOT NULL, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_episode_person ON persona_episodes(user_id,created_at);
CREATE TABLE IF NOT EXISTS persona_intents(
 id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, topic TEXT NOT NULL,
 description TEXT NOT NULL, source_key TEXT NOT NULL, state TEXT NOT NULL,
 due_at REAL NOT NULL, expires_at REAL NOT NULL, version INTEGER NOT NULL,
 reserved_action TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL,
 UNIQUE(user_id,topic));
CREATE TABLE IF NOT EXISTS persona_intent_versions(
 intent_id TEXT NOT NULL, version INTEGER NOT NULL, state TEXT NOT NULL,
 source_key TEXT NOT NULL, updated_at REAL NOT NULL, PRIMARY KEY(intent_id,version));
CREATE INDEX IF NOT EXISTS idx_intent_due ON persona_intents(state,due_at);
CREATE TABLE IF NOT EXISTS persona_actions(
 id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, group_id INTEGER NOT NULL,
 state TEXT NOT NULL, expected TEXT NOT NULL, intent_id TEXT NOT NULL DEFAULT '',
 created_at REAL NOT NULL, updated_at REAL NOT NULL, intent_version INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS persona_action_parts(
 action_id TEXT NOT NULL, part INTEGER NOT NULL, text TEXT NOT NULL,
 state TEXT NOT NULL, message_id TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
 updated_at REAL NOT NULL, PRIMARY KEY(action_id,part));
CREATE TABLE IF NOT EXISTS persona_exposures(
 action_id TEXT NOT NULL, part INTEGER NOT NULL, group_id INTEGER NOT NULL,
 user_id INTEGER NOT NULL, message_id TEXT NOT NULL, created_at REAL NOT NULL,
 PRIMARY KEY(action_id,part));
CREATE TABLE IF NOT EXISTS persona_cognition_reviews(
 id INTEGER PRIMARY KEY, turn_id TEXT NOT NULL, status TEXT NOT NULL,
 detail TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS persona_portraits(
 user_id INTEGER PRIMARY KEY, content TEXT NOT NULL, dependencies TEXT NOT NULL,
 updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS persona_migration_map(
 source TEXT PRIMARY KEY, memory_id INTEGER NOT NULL, migrated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS persona_feedback(
 event_key TEXT PRIMARY KEY, user_id INTEGER NOT NULL, action_id TEXT NOT NULL,
 part INTEGER NOT NULL, created_at REAL NOT NULL);
"""
