"""Offline visual metadata, bounded semantic selection and acknowledged image usage."""
from __future__ import annotations

import json
import random
import re
import time
from collections import Counter
from pathlib import Path

from bot.services.persona_expressions import EXPRESSION_PROBABILITY, expression_request, requested_name
from bot.services.persona_store import PersonaStore


CATEGORIES = {
    "微笑": "gentle_smile", "开心": "happy", "思考": "thinking", "难过": "sad",
    "无语": "speechless", "无奈": "resigned", "尬笑": "awkward_smile",
    "打招呼": "greeting", "早上好": "greeting", "委屈": "hurt",
}
DEFAULT_NAMES = {"smile": "微笑", "laugh": "大笑", "think": "思考", "peek": "探头"}


class ExpressionSelection:
    def __init__(self, store: PersonaStore) -> None:
        self.store = store
        self._catalogs: dict[tuple, list[dict]] = {}

    def catalog(self, profile) -> list[dict]:
        cache_key = (profile.key, profile.version, profile.resource_dir)
        if cache_key in self._catalogs:
            return self._catalogs[cache_key]
        path = profile.resource_dir / "expression_catalog.json"
        if profile.key == "tangtang" or not path.is_file():
            rows = [dict(id=k, name=v, file="", use=v, avoid="不合语境时不用",
                         group={"smile": "gentle_smile", "think": "thinking"}.get(k, k))
                    for k, v in DEFAULT_NAMES.items()]
        else:
            try:
                rows = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                rows = []
            if not isinstance(rows, list):
                rows = []
        valid = {}
        for row in rows:
            if not isinstance(row, dict) or not all(isinstance(row.get(k), str) for k in ("id", "name", "file", "use", "avoid")):
                continue
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", row["id"]):
                continue
            filename = row["file"]
            if filename and (Path(filename).name != filename or Path(filename).suffix.lower() not in {".gif", ".webp", ".png", ".jpg", ".jpeg"}):
                continue
            valid.setdefault(row["id"], row)
        rows = list(valid.values())
        self.store.sync_expression_catalog(profile.key, rows)
        with self.store.connect() as conn:
            conn.executemany("INSERT INTO expression_metadata VALUES(?,?,?,?) ON CONFLICT(persona,expression_id) "
                             "DO UPDATE SET version=excluded.version,metadata=excluded.metadata",
                             [(profile.key, r["id"], profile.version, json.dumps(r, ensure_ascii=False)) for r in rows])
        self._catalogs[cache_key] = rows
        return rows

    def names(self, profile) -> tuple[str, ...]:
        names = list(CATEGORIES) + list(DEFAULT_NAMES.values())
        for row in self.catalog(profile):
            names.extend([row["id"], row["name"]])
            names.extend(v for v in row.get("aliases", []) if isinstance(v, str))
        return tuple(names)

    def requested(self, profile, text: str) -> tuple[list[str], bool]:
        """Exact IDs/names bypass recent-use exclusion; category requests do not."""
        rows = self.catalog(profile)
        name = requested_name(text, self.names(profile))
        if name in CATEGORIES:
            return [r["id"] for r in rows if r.get("group") == CATEGORIES[name]], False
        if name:
            ids = [r["id"] for r in rows if name in (r["id"], r["name"], *r.get("aliases", []))
                   or DEFAULT_NAMES.get(r["id"]) == name]
            return ids, len(ids) == 1
        return [r["id"] for r in rows if r.get("group") == "gentle_smile"], False

    def history(self, context, now: float) -> tuple[Counter, list[str]]:
        with self.store.connect() as conn:
            rows = conn.execute("SELECT selected_id FROM expression_events WHERE persona=? AND group_id=? "
                                "AND status='delivered' AND completed_at>=? ORDER BY completed_at DESC,rowid DESC",
                                (context.persona.key, context.group_id, now - 86400)).fetchall()
        ids = [r[0] for r in rows]
        return Counter(ids), ids[:2]

    def choose(self, context, text: str, preferred: str, alternatives: tuple[str, ...], *,
               available: tuple[str, ...], roll: float, structured: bool = True,
               blocked: str = "", now: float | None = None) -> str:
        now = time.time() if now is None else now
        rows = {r["id"]: r for r in self.catalog(context.persona)}
        intent = expression_request(text, self.names(context.persona))
        exact = False
        if intent == "explicit":
            candidates, exact = self.requested(context.persona, text)
        else:
            candidates = list(dict.fromkeys([preferred, *alternatives[:3]])) if preferred else []
        rejected = {}
        eligible = []
        for key in candidates:
            reason = ""
            if key not in rows:
                reason = "invalid_id"
            elif key not in available:
                reason = "unavailable"
            elif rows[key].get("explicit_only") and not exact:
                reason = "explicit_only"
            elif intent != "explicit" and key != preferred and (
                preferred not in rows or rows[key].get("group", key) != rows[preferred].get("group", preferred)
            ):
                reason = "incompatible_group"
            if reason:
                rejected[key] = reason
            else:
                eligible.append(key)
        counts, recent = self.history(context, now)
        reason = blocked or ("user_opt_out" if intent == "none" else "")
        if not reason and intent != "explicit" and not structured:
            reason = "invalid_structure"
        if not reason and intent != "explicit" and roll >= EXPRESSION_PROBABILITY:
            reason = "probability_miss"
        if not exact:
            for key in eligible[:]:
                if key in recent:
                    rejected[key] = "recent_two"
                    eligible.remove(key)
        if not reason and not eligible:
            reason = "recent_exhausted" if "recent_two" in rejected.values() else "no_candidate"
        weights = [1 / (1 + counts[key] / 3) for key in eligible]
        selected = random.choices(eligible, weights=weights, k=1)[0] if eligible and not reason else ""
        detail = dict(preferred=preferred, candidates=candidates, eligible=eligible, rejected=rejected,
                      weights=dict(zip(eligible, weights)), counts=dict(counts), recent=recent,
                      intent=intent, exact=exact, roll=roll, probability=EXPRESSION_PROBABILITY,
                      version=context.persona.version)
        with self.store.connect() as conn:
            inserted = conn.execute("INSERT OR IGNORE INTO expression_events(persona,request_id,group_id,created_at,"
                                    "selected_id,status,reason,decision_json) VALUES(?,?,?,?,?,?,?,?)",
                                    (context.persona.key, context.request_id, context.group_id, now, selected,
                                     "selected" if selected else "skipped", reason or "weighted_choice",
                                     json.dumps(detail, ensure_ascii=False))).rowcount
        return selected if inserted else ""

    def result(self, context, status: str, *, message_id: str = "", reason: str = "",
               now: float | None = None) -> bool:
        if status not in {"sending", "delivered", "failed", "uncertain", "cancelled"}:
            raise ValueError("invalid expression status")
        if status == "delivered" and not message_id:
            status, reason = "uncertain", "missing_acknowledgement"
        with self.store.connect() as conn:
            return bool(conn.execute("UPDATE expression_events SET status=?,completed_at=?,platform_message_id=?,"
                                     "reason=CASE WHEN ?='' THEN reason ELSE ? END "
                                     "WHERE persona=? AND request_id=? AND status IN ('selected','sending')",
                                     (status, time.time() if now is None else now, message_id, reason, reason,
                                      context.persona.key, context.request_id)).rowcount)
