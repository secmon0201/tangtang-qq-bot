"""Local expression selection and voice delivery policy; no model requests."""
from __future__ import annotations

import json
import random
import re
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


EXPRESSION_PROBABILITY = .60
_NO_EXPRESSION = re.compile(r"(?:不要|别|不用|不必|禁止).{0,8}(?:表情包|表情|贴纸)")
_NO_VOICE = re.compile(r"(?:不要|别|不用|不想听|不必).{0,6}(?:语音|声音|说话)|(?:只|就|还是)(?:要|用|发)?(?:文字|打字)|打字就好")
_VOICE_REQUEST = re.compile(r"(?:发|来|听|用|说|读|念|播|讲).{0,8}(?:语音|声音)|(?:语音|声音).{0,8}(?:发|听|回复|回答)|(?:读|念)给我听")
_VOICE_REFUSAL = re.compile(r"(?:不|没|别|休想|才不|懒得).{0,8}(?:发|用|给|开口|念|读|语音|说话|出声|录音|张嘴)|(?:语音|声音).{0,8}(?:不|没|算了)|(?:还是|只|就)(?:打字|文字)")
_VISUAL_TEXT = re.compile(r"https?://|```|\|.+\||\[(?:图片|消息|表情)|<[^>]+>")
_CATEGORY = {"微笑": "gentle_smile", "开心": "happy", "大笑": "happy", "思考": "thinking",
             "难过": "sad", "无语": "speechless", "无奈": "resigned", "尬笑": "awkward_smile",
             "打招呼": "greeting", "早上好": "greeting", "委屈": "hurt"}


def expression_catalog(root: Path, persona: str = "denia") -> list[dict]:
    directory = root / "resources" / "personas" / persona
    path = directory / "expression_catalog.json"
    if not path.is_file():
        return []
    return [row for row in json.loads(path.read_text(encoding="utf-8"))
            if (directory / "expressions" / row["file"]).is_file()]


def expression_candidates(root: Path, text: str, persona: str = "denia", limit: int = 14) -> list[dict]:
    """A compact relevant catalogue in the changing tail, never the persona prefix."""
    rows = [row for row in expression_catalog(root, persona) if not row.get("explicit_only")]
    terms = {text[index:index + 2] for index in range(len(text) - 1)
             if re.fullmatch(r"[\u4e00-\u9fff]{2}", text[index:index + 2])}
    def score(row):
        description = " ".join((row["name"], row["use"], *row.get("emotion", []), *row.get("aliases", [])))
        return sum(term in description for term in terms) + 2 * any(
            term in text and row.get("group") == group for term, group in _CATEGORY.items())
    ordered = sorted(rows, key=lambda row: (-score(row), row["id"]))
    chosen = [row for row in ordered if score(row)][:max(1, limit // 2)]
    groups = set()
    for row in ordered:
        group = row.get("group", row["id"])
        if group not in groups:
            groups.add(group)
            if row not in chosen:
                chosen.append(row)
            if len(chosen) >= limit:
                break
    for row in ordered:
        if len(chosen) >= limit:
            break
        if row not in chosen:
            chosen.append(row)
    return chosen


def expression_prompt(root: Path, text: str, persona: str = "denia") -> str:
    rows = expression_candidates(root, text, persona)
    if not rows:
        return ""
    return ("[本轮本地表情候选，非事实资料]\n最多选一个 expression，另给1至3个适合的 expression_candidates；"
            "没有合适项留空。逐项考虑适用与避免，语音不附表情，不用嘲讽图回应真实痛苦。\n" + "\n".join(
                f"{row['id']}（{row['name']}；适用={row['use']}；避免={row['avoid']}）" for row in rows))


class ExpressionSelector:
    def __init__(self, root: Path, store, persona: str = "denia"):
        self.root, self.store, self.persona = root, store, persona

    def resolve(self, selector: str) -> Path | None:
        if not selector or _NO_EXPRESSION.search(selector):
            return None
        rows = expression_catalog(self.root, self.persona)
        candidates = [row for row in rows if selector == row["id"] or row["name"] in selector
                      or any(alias and alias in selector for alias in row.get("aliases", []))]
        if not candidates:
            category = next((group for name, group in _CATEGORY.items() if name in selector), None)
            candidates = [row for row in rows if row.get("group") == category] if category else []
        if not candidates and "表情" in selector:
            candidates = [row for row in rows if row.get("group") == "gentle_smile" and not row.get("explicit_only")]
        if not candidates:
            return None
        row = random.choice(candidates)
        return self.root / "resources" / "personas" / self.persona / "expressions" / row["file"]

    def choose(self, event, metadata: dict, *, enabled: bool = True, voice: bool = False,
               roll: float | None = None, probability: float = EXPRESSION_PROBABILITY,
               now: float | None = None) -> tuple[str, Path] | None:
        if not enabled or voice or _NO_EXPRESSION.search(event.text):
            return None
        if (random.random() if roll is None else roll) >= probability:
            return None
        rows = {row["id"]: row for row in expression_catalog(self.root, self.persona)
                if not row.get("explicit_only")}
        alternatives = metadata.get("expression_candidates", [])
        alternatives = alternatives[:3] if isinstance(alternatives, list) else []
        preferred = metadata.get("expression", "")
        candidates = list(dict.fromkeys(value for value in [preferred, *alternatives] if isinstance(value, str) and value in rows))
        now = time.time() if now is None else now
        history = [row for row in self.store.get_setting("expression_history:" + event.session_key, [])
                   if row["time"] >= now - 86400]
        counts = Counter(row["id"] for row in history)
        recent = [row["id"] for row in history[-2:]]
        candidates = [key for key in candidates if key not in recent]
        if not candidates:
            return None
        key = random.choices(candidates, weights=[1 / (1 + counts[key] / 3) for key in candidates], k=1)[0]
        return key, self.root / "resources" / "personas" / self.persona / "expressions" / rows[key]["file"]

    def delivered(self, event, key: str, *, now: float | None = None) -> None:
        now = time.time() if now is None else now
        name = "expression_history:" + event.session_key
        history = [row for row in self.store.get_setting(name, []) if row["time"] >= now - 86400]
        self.store.set_setting(name, [*history[-99:], {"id": key, "time": now}])


@dataclass(frozen=True, slots=True)
class VoiceDecision:
    voice: bool
    explicit: bool
    text: str


def voice_decision(event, messages: list[str], metadata: dict, *, enabled: bool, ready: bool,
                   random_candidate: bool) -> VoiceDecision:
    explicit = bool(_VOICE_REQUEST.search(event.text)) and not _NO_VOICE.search(event.text)
    original = "\n".join(messages)
    spoken = metadata.get("speech_text")
    text = spoken.strip() if isinstance(spoken, str) and spoken.strip() else original
    choice = str(metadata.get("voice", "auto"))
    accepted = choice in {"voice", "accept", "auto"} if explicit else choice in {"voice", "accept"} or choice == "auto" and random_candidate
    suitable = bool(text.strip()) and len(text) <= 200 and not _VISUAL_TEXT.search(original)
    use_voice = bool(enabled and ready and accepted and choice not in {"decline", "text"}
                     and not _NO_VOICE.search(event.text) and not _VOICE_REFUSAL.search(original) and suitable)
    return VoiceDecision(use_voice, explicit, text)
