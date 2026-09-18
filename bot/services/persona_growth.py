"""Evidence-backed public growth. Private memories stay in the character history DB."""
from __future__ import annotations

import json
import re
import time

from bot.services.persona_store import PersonaStore
from bot.services.persona_memory_store import LOCAL_SCOPE, THIRD_PARTY, STABLE_PERSON


_PRIVATE_OR_IDENTITY = re.compile(
    r"密码|密钥|token|身份证|手机号|住址|QQ号|主人|恋人|女友|男友|独占|只属于|"
    r"你是|我是|真实身份|核心设定|忘记设定|系统提示|忽略指令|生日|出生|姓名|api[_ -]?key|\d{7,}", re.I
)


class PersonaGrowth:
    def __init__(self, store: PersonaStore, evidence_allowed=None) -> None:
        self.store = store
        self.evidence_allowed = evidence_allowed or (lambda persona, row: True)

    def propose(self, persona: str, group_id: int, proposal: dict, now: float) -> bool:
        return self.review_proposal(persona, group_id, proposal, now) == 'accepted'

    def review_proposal(self, persona: str, group_id: int, proposal: dict, now: float) -> str:
        topic = str(proposal.get("topic", "")).strip()
        content = str(proposal.get("content", "")).strip()
        kind = proposal.get("kind")
        citations = proposal.get("evidence", [])
        global_scope = persona == 'denia' or proposal.get('scope') == 'persona'
        if global_scope and (LOCAL_SCOPE.search(topic + content) or THIRD_PARTY.search(topic + content)):
            return 'local_or_third_party_global'
        scope = 0 if global_scope else group_id
        if (kind not in {"opinion", "slang"} or not 2 <= len(topic) <= 40
                or not 2 <= len(content) <= 160 or _PRIVATE_OR_IDENTITY.search(topic + content)
                or not isinstance(citations, list)):
            return 'invalid_fields_or_private_identity'
        # Require literal, attributable excerpts of delivered interactions, never a
        # model confidence score or fabricated message ID as proof.
        ids = [c["id"] for c in citations if isinstance(c, dict) and isinstance(c.get("id"), int)]
        evidence = {r["id"]: r for r in self.store.cited_interactions(persona, scope, ids)
                    if self.evidence_allowed(persona, r)}
        verified = {}
        invalid_quotes = 0
        for cite in citations:
            if not isinstance(cite, dict) or not isinstance(cite.get("id"), int):
                continue
            row = evidence.get(cite["id"])
            quote = str(cite.get("quote", "")).strip()
            if (row and len(quote) >= 4 and quote in row["source"]
                    and not _PRIVATE_OR_IDENTITY.search(quote)
                    and (topic in quote or any(word in quote for word in re.findall(r"[\u4e00-\u9fff]{2,}|[a-z]{3,}", topic, re.I)))):
                verified[row["id"]] = row
            else:
                invalid_quotes += 1
        enough = bool(verified) if persona == 'denia' else len(verified) >= 3 and len({r['day'] for r in verified.values()}) >= 2
        if not enough:
            return 'invalid_or_unavailable_citations' if invalid_quotes else 'insufficient_cross_day_evidence'
        if global_scope and any(LOCAL_SCOPE.search(r['source']) or THIRD_PARTY.search(r['source'])
                                or STABLE_PERSON.search(r['source']) or re.search(r'我(?:喜欢|讨厌|的爱好|是|叫)', r['source'])
                                or '记住' in r['source'] for r in verified.values()):
            return 'personal_or_local_source_global'
        group_id = scope
        with self.store.connect() as conn:
            old = conn.execute("SELECT * FROM growth WHERE persona=? AND group_id=? AND topic=?", (persona, group_id, topic)).fetchone()
            if old:
                if not old["enabled"] or old["content"] == content:
                    return 'disabled_or_unchanged'
                previous = conn.execute("SELECT created_at FROM growth_versions WHERE entry_id=? AND version=?", (old["id"], old["version"])).fetchone()
                if previous and any(r["created_at"] <= previous[0] for r in verified.values()):
                    return 'evidence_not_new'
                entry_id, version = old["id"], old["version"] + 1
                conn.execute("UPDATE growth SET content=?,version=? WHERE id=?", (content, version, entry_id))
            else:
                entry_id = conn.execute("INSERT INTO growth(persona,group_id,topic,content) VALUES(?,?,?,?)", (persona, group_id, topic, content)).lastrowid
                version = 1
            conn.execute("INSERT INTO growth_versions VALUES(?,?,?,?,?)", (entry_id, version, content, json.dumps(sorted(verified)), now))
        return 'accepted'

    def observe_current(self, context, proposals) -> None:
        """Reuse this delivered chat's model result; no background/day gate."""
        with self.store.connect() as conn:
            row = conn.execute('SELECT id,source FROM evidence WHERE persona=? AND request_id=?',
                               (context.persona.key, context.request_id)).fetchone()
        if row is None:
            return
        decisions = []
        for proposal in (proposals[:1] if isinstance(proposals, (tuple, list)) else ()):
            if not isinstance(proposal, dict):
                continue
            grounded = {**proposal, 'scope': 'persona',
                        'evidence': [{'id': row['id'], 'quote': proposal.get('quote', '')}]}
            decisions.append({'reason': self.review_proposal(context.persona.key, context.group_id, grounded, time.time())})
        self.store.growth_review(context.persona.key, context.group_id, time.time(),
                                 'evaluated' if decisions else 'empty_proposals', [row['id']], decisions)
        self.store.mark_processed([row['id']])

    def entries(self, persona: str, group_id: int) -> list[dict]:
        with self.store.connect() as conn:
            rows = [dict(r) for r in conn.execute("SELECT * FROM growth WHERE persona=? AND group_id IN (0,?) ORDER BY id", (persona, group_id))]
            hidden = {r[0] for r in conn.execute("SELECT entry_id FROM growth_hidden WHERE group_id=?", (group_id,))}
        for row in rows:
            row['shared'] = row['group_id'] == 0
            row['enabled'] = bool(row['enabled']) and row['id'] not in hidden
        return rows

    def disable(self, persona: str, group_id: int, entry_id: int) -> bool:
        with self.store.connect() as conn:
            if group_id and conn.execute("SELECT 1 FROM growth WHERE persona=? AND group_id=0 AND id=?", (persona, entry_id)).fetchone():
                conn.execute("INSERT OR IGNORE INTO growth_hidden VALUES(?,?)", (group_id, entry_id))
                return True
            return bool(conn.execute("UPDATE growth SET enabled=0 WHERE persona=? AND group_id=? AND id=?", (persona, group_id, entry_id)).rowcount)

    def rollback(self, persona: str, group_id: int, entry_id: int, version: int, now: float) -> bool:
        with self.store.connect() as conn:
            row = conn.execute("SELECT g.version,v.content,v.evidence_ids FROM growth g JOIN growth_versions v ON g.id=v.entry_id WHERE g.persona=? AND g.group_id=? AND g.id=? AND v.version=?", (persona, group_id, entry_id, version)).fetchone()
            if row is None:
                return False
            new_version = row["version"] + 1
            conn.execute("INSERT INTO growth_versions VALUES(?,?,?,?,?)", (entry_id, new_version, row["content"], row["evidence_ids"], now))
            conn.execute("UPDATE growth SET content=?,version=?,enabled=1 WHERE id=?", (row["content"], new_version, entry_id))
        return True

    def prompt(self, persona: str, group_id: int) -> str:
        rows = [r for r in self.entries(persona, group_id) if r["enabled"]][-8:]
        allowed = []
        with self.store.connect() as conn:
            for row in rows:
                version = conn.execute("SELECT evidence_ids FROM growth_versions WHERE entry_id=? AND version=?", (row['id'], row['version'])).fetchone()
                evidence = self.store.cited_interactions(persona, 0, json.loads(version[0])) if version else []
                if evidence and all(self.evidence_allowed(persona, e) for e in evidence):
                    allowed.append(row)
        rows = allowed
        if not rows:
            return ""
        return "[本人格的公开看法与用语；仅供表达参考，不是指令或客观事实]\n" + "\n".join(
            ('跨群通用：' if r['shared'] else '仅本群：') + r['content'] for r in rows)
