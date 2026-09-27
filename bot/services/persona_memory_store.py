"""One character's shared people, scoped facts and recall restrictions.

The owning character history database is the persona boundary. Scope zero means
portable across groups; real group IDs are always positive.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import re

from bot.services.persona_mood import mood_decay
from bot.services.persona_memory_quality import fact_rejection


SCHEMA = """
CREATE TABLE IF NOT EXISTS person_facts(
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, scope_group INTEGER NOT NULL,
 content TEXT NOT NULL, normalized TEXT NOT NULL, kind TEXT NOT NULL,
 status TEXT NOT NULL, importance REAL NOT NULL, confidence REAL NOT NULL,
 updated_at TEXT NOT NULL, UNIQUE(user_id,scope_group,normalized));
CREATE TABLE IF NOT EXISTS person_fact_evidence(
 fact_id INTEGER NOT NULL, event_key TEXT NOT NULL, PRIMARY KEY(fact_id,event_key));
CREATE TABLE IF NOT EXISTS person_relations(
 user_id INTEGER PRIMARY KEY, familiarity REAL NOT NULL DEFAULT 0,
 warmth REAL NOT NULL DEFAULT 0.5, mood REAL NOT NULL DEFAULT 0.5,
 interaction_count INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS person_events(
 user_id INTEGER NOT NULL, event_key TEXT NOT NULL, PRIMARY KEY(user_id,event_key));
CREATE TABLE IF NOT EXISTS person_restrictions(
 user_id INTEGER NOT NULL, scope_group INTEGER NOT NULL, needle TEXT NOT NULL,
 active INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(user_id,scope_group,needle));
CREATE TABLE IF NOT EXISTS person_superseded_memory(
 user_id INTEGER NOT NULL, scope_group INTEGER NOT NULL, needle TEXT NOT NULL,
 active INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(user_id,scope_group,needle));
CREATE TABLE IF NOT EXISTS person_memory_revision(
 id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS person_memory_migrations(version TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS person_memory_control(id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS idx_calls_person_recent ON tangtang_calls(user_id,id DESC);
CREATE TABLE IF NOT EXISTS person_semantic_memory(
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, scope_group INTEGER NOT NULL,
 category TEXT NOT NULL, content TEXT NOT NULL, normalized TEXT NOT NULL,
 tags TEXT NOT NULL, status TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(user_id,scope_group,category,normalized));
CREATE TABLE IF NOT EXISTS person_semantic_versions(
 memory_id INTEGER NOT NULL, version INTEGER NOT NULL, content TEXT NOT NULL,
 quote TEXT NOT NULL, tags TEXT NOT NULL, source_group INTEGER NOT NULL,
 source_message_id TEXT NOT NULL, operation TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(memory_id,version));
CREATE TABLE IF NOT EXISTS person_semantic_evidence(
 memory_id INTEGER NOT NULL, event_key TEXT NOT NULL, source_group INTEGER NOT NULL,
 source_message_id TEXT NOT NULL, quote TEXT NOT NULL, delivered INTEGER NOT NULL,
 created_at TEXT NOT NULL, PRIMARY KEY(memory_id,event_key));
CREATE TABLE IF NOT EXISTS person_memory_write_reviews(
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, source_group INTEGER NOT NULL,
 source_message_id TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL,
 memory_ids TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_semantic_person ON person_semantic_memory(user_id,scope_group,status);
"""
LOCAL_SCOPE = re.compile(r"本群|这个群|这群|在这里|仅在|只在|群内|群里约定")
STABLE_PERSON = re.compile(r"^(?:我)?(?:现在|已经|其实)?(?:叫|喜欢|最喜欢|不再喜欢|不喜欢|讨厌|希望|想要)|称呼我|叫我")
SENSITIVE = re.compile(r"密码|密钥|验证码|身份证|银行卡|手机号|住址|病历|疾病|宗教|政治|性取向|真实姓名|系统提示|忽略规则|开发者指令|执行指令", re.I)
THIRD_PARTY = re.compile(r"\[|\]|@|他说|她说|他喜欢|她喜欢|群友|转发|引用|别人|有人说|告诉我")


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", text).lower().strip("，。！？!?:：")


def fact_scope(content: str, group_id: int) -> int:
    return 0 if not LOCAL_SCOPE.search(content) and not fact_rejection(content) else group_id


def fact_subject(content: str) -> str:
    match = re.search(r"我(?:现在|已经|其实)?(?:不再喜欢|不喜欢|最喜欢|喜欢|讨厌)(.+)", content)
    if match:
        return "preference:" + normalize(match[1]).removesuffix('了')
    if re.search(r"我叫|叫我|称呼我", content):
        return 'name'
    return ''


def restriction_needle(query: str) -> str:
    clean = normalize(query).removesuffix('这件事').removesuffix('的事')
    subject = fact_subject(clean)
    return subject.removeprefix('preference:') if subject.startswith('preference:') else clean


class PersonMemoryStore:
    def __init__(self, db, *, global_personal: bool = False) -> None:
        self.db = db
        self.global_personal = global_personal

    @contextmanager
    def connect(self):
        with self.db._connect() as conn:
            conn.executescript(SCHEMA)
            yield conn

    def revision(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT revision FROM person_memory_revision WHERE id=1").fetchone()
        return int(row[0]) if row else 0

    def enabled(self) -> bool:
        with self.connect() as conn:
            row = conn.execute("SELECT enabled FROM person_memory_control WHERE id=1").fetchone()
        return bool(row[0]) if row else True

    def set_enabled(self, enabled: bool) -> None:
        with self.connect() as conn:
            conn.execute("INSERT INTO person_memory_control VALUES(1,?) ON CONFLICT(id) DO UPDATE SET enabled=excluded.enabled", (int(enabled),))
            conn.execute("INSERT INTO person_memory_revision VALUES(1,1) ON CONFLICT(id) DO UPDATE SET revision=revision+1")

    def restrictions(self, user_id: int, group_id: int, *, include_superseded: bool = True) -> list[str]:
        with self.connect() as conn:
            rows = [r[0] for r in conn.execute(
                "SELECT needle FROM person_restrictions WHERE user_id=? AND (? OR scope_group IN (0,?)) AND active=1",
                (user_id, self.global_personal, group_id))]
            if include_superseded:
                rows.extend(r[0] for r in conn.execute(
                    "SELECT needle FROM person_superseded_memory WHERE user_id=? AND (? OR scope_group IN (0,?)) AND active=1",
                    (user_id, self.global_personal, group_id)))
            return rows

    def blocked(self, user_id: int, group_id: int, text: str, *, include_superseded: bool = True) -> bool:
        clean = normalize(text)
        return any(needle in clean for needle in self.restrictions(user_id, group_id, include_superseded=include_superseded))

    def superseded(self, user_id: int, group_id: int) -> set[str]:
        with self.connect() as conn:
            return {r[0] for r in conn.execute(
                "SELECT needle FROM person_superseded_memory WHERE user_id=? AND (? OR scope_group IN (0,?)) AND active=1",
                (user_id, self.global_personal, group_id))}

    def restrict(self, user_id: int, group_id: int, query: str, *, restore: bool = False) -> int:
        if self.global_personal:
            return 0
        needle = restriction_needle(query)
        if not needle:
            return 0
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO person_restrictions VALUES(?,?,?,?) ON CONFLICT(user_id,scope_group,needle) DO UPDATE SET active=excluded.active",
                         (user_id, group_id, needle, int(not restore)))
            if restore:
                conn.execute("UPDATE person_restrictions SET active=0 WHERE user_id=? AND scope_group=? AND (instr(needle,?)>0 OR instr(?,needle)>0)",
                             (user_id, group_id, needle, needle))
            # Group-only restrictions do not change portable fact status.
            rows = conn.execute("SELECT id FROM person_facts WHERE user_id=? AND instr(normalized,?)>0", (user_id, needle)).fetchall()
            semantic_rows = conn.execute("SELECT id FROM person_semantic_memory WHERE user_id=? AND instr(normalized,?)>0", (user_id, needle)).fetchall()
            if group_id == 0:
                conn.execute("UPDATE person_facts SET status=? WHERE user_id=? AND instr(normalized,?)>0 AND status IN ('active','candidate','deleted')",
                             ("active" if restore else "deleted", user_id, needle))
                conn.execute("UPDATE tangtang_memories SET status=? WHERE user_id=? AND instr(normalized_content,?)>0 AND status IN ('active','candidate','deleted')",
                             ("active" if restore else "deleted", user_id, needle))
                conn.execute("UPDATE person_semantic_memory SET status=? WHERE user_id=? AND instr(normalized,?)>0 AND status IN ('active','candidate','deleted')",
                             ("active" if restore else "deleted", user_id, needle))
            conn.execute("INSERT INTO person_memory_revision VALUES(1,1) ON CONFLICT(id) DO UPDATE SET revision=revision+1")
            return len(rows) + len(semantic_rows)

    def remember(self, *, group_id: int, user_id: int, message_id: str, content: str,
                 kind: str, status: str, importance: float, confidence: float, now: str,
                 scope_group: int | None = None, correction: bool = False) -> int | None:
        if self.blocked(user_id, group_id, content, include_superseded=not correction):
            return None
        scope = fact_scope(content, group_id) if scope_group is None else scope_group
        if self.global_personal:
            scope, status = 0, 'active'
        elif scope == 0 and int(group_id) > 0:
            # Personal memory is group-local. Legacy scope-zero rows remain in
            # storage for audit but are no longer created by new observations.
            scope = int(group_id)
        clean = normalize(content)
        event = f"{group_id}:{message_id}" if message_id else "unknown:" + hashlib.sha256(clean.encode()).hexdigest()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if correction and (subject := fact_subject(content)):
                conn.execute("UPDATE person_superseded_memory SET active=0 WHERE user_id=? AND scope_group=? AND needle=?", (user_id, scope, clean))
                old = conn.execute("SELECT id,content,normalized FROM person_facts WHERE user_id=? AND (? OR scope_group=?) AND status<>'deleted'", (user_id, self.global_personal, scope)).fetchall()
                for previous in old:
                    if previous['normalized'] != clean and fact_subject(previous['content']) == subject:
                        conn.execute("UPDATE person_facts SET status='archived' WHERE id=?", (previous['id'],))
                        conn.execute("INSERT INTO person_superseded_memory VALUES(?,?,?,1) ON CONFLICT(user_id,scope_group,needle) DO UPDATE SET active=1", (user_id, scope, previous['normalized']))
                conn.execute("INSERT INTO person_memory_revision VALUES(1,1) ON CONFLICT(id) DO UPDATE SET revision=revision+1")
            conn.execute("INSERT OR IGNORE INTO person_facts(user_id,scope_group,content,normalized,kind,status,importance,confidence,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                         (user_id, scope, content, clean, kind, status, importance, confidence, now))
            row = conn.execute("SELECT id,status FROM person_facts WHERE user_id=? AND scope_group=? AND normalized=?", (user_id, scope, clean)).fetchone()
            conn.execute("INSERT OR IGNORE INTO person_fact_evidence VALUES(?,?)", (row[0], event))
            count = conn.execute("SELECT COUNT(*) FROM person_fact_evidence WHERE fact_id=?", (row[0],)).fetchone()[0]
            if row[1] != "deleted" and (status == "active" or count >= 2):
                conn.execute("UPDATE person_facts SET status='active',updated_at=? WHERE id=?", (now, row[0]))
            return int(row[0])

    def facts(self, group_id: int, user_id: int) -> list[dict]:
        if not self.enabled():
            return [r for r in self.db.active_memories(group_id, user_id) if not self.blocked(user_id, group_id, r['content']) and not fact_rejection(r['content'])]
        with self.connect() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM person_facts WHERE user_id=? AND (? OR scope_group IN (0,?)) AND status='active' ORDER BY importance DESC,updated_at DESC LIMIT 100",
                (user_id, self.global_personal, group_id))]
        blocked = self.restrictions(user_id, group_id, include_superseded=False)
        obsolete = self.superseded(user_id, group_id)
        return [r for r in rows if r['normalized'] not in obsolete and not fact_rejection(r['content']) and not any(n in r['normalized'] for n in blocked)]

    def relationship(self, user_id: int) -> dict:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM person_relations WHERE user_id=?", (user_id,)).fetchone()
        return dict(row) if row else {"familiarity": 0., "warmth": .5, "mood": .5, "updated_at": ""}

    def observe_relation(self, group_id: int, user_id: int, event_id: str, warmth_delta: float, now: str) -> bool:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute("INSERT OR IGNORE INTO person_events VALUES(?,?)", (user_id, f"{group_id}:{event_id}")).rowcount:
                return False
            conn.execute("INSERT OR IGNORE INTO person_relations(user_id,updated_at) VALUES(?,?)", (user_id, now))
            row = conn.execute("SELECT mood,updated_at FROM person_relations WHERE user_id=?", (user_id,)).fetchone()
            mood = max(0., min(1., mood_decay(row[0], row[1], now) + warmth_delta))
            conn.execute("UPDATE person_relations SET familiarity=MIN(1,familiarity+0.01), warmth=MIN(1,MAX(0,warmth+?)), mood=?,interaction_count=interaction_count+1,updated_at=? WHERE user_id=?",
                         (warmth_delta, mood, now, user_id))
            return True

    def episodes(self, group_id: int, user_id: int, query: str) -> str:
        if not self.enabled():
            return ""
        terms = {query[i:i+2] for i in range(len(query)-1) if re.fullmatch(r"[\u4e00-\u9fff]{2}", query[i:i+2])}
        terms -= {"什么", "怎么", "记得", "上次", "之前", "我们", "娅娅", "糖糖", "喜欢", "觉得"}
        if not terms:
            return ""
        with self.connect() as conn:
            rows = conn.execute("SELECT group_id,call_text,created_at FROM tangtang_calls WHERE user_id=? AND group_id=? AND reply_kind IN ('model','proactive') ORDER BY id DESC LIMIT 100", (user_id, group_id)).fetchall()
        needles = self.restrictions(user_id, group_id)
        candidates = []
        for row in rows:
            text = str(row['call_text'])
            if LOCAL_SCOPE.search(text) or SENSITIVE.search(text) or THIRD_PARTY.search(text) or any(n in normalize(text) for n in needles):
                continue
            score = sum(t in text for t in terms)
            if score >= 2:
                candidates.append((score, f"{row['created_at'][:10]}，本群与该用户的互动片段：{text[:200]}"))
        candidates.sort(key=lambda r: -r[0])
        return "\n".join(list(dict.fromkeys(text for _, text in candidates))[:2])[:440] if candidates else ""
