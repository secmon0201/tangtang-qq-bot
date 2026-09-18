"""Evidence-linked, event-driven personal impressions; never group-local facts."""
from __future__ import annotations

import re

from bot.services.persona_memory_store import SENSITIVE, THIRD_PARTY


TRAITS = {
    'curious': '愿意追问和了解细节',
    'playful': '喜欢轻松打趣',
    'direct': '表达直接、重视明确回答',
    'considerate': '交流时会照顾对方感受',
    'creative': '乐于创作或提出新点子',
    'persistent': '会继续推进和完善事情',
}
_CONTROL = re.compile(r'系统提示|忽略|权限|管理员|永远|无条件|主人|恋人|假装|扮演|给我.*(?:印象|评价)|把我.*(?:当成|记成)')
_REMOVAL = re.compile(r'^(?:(?:娅娅|达妮娅)[，,：: ]*)?(?:请|帮我|你)?(?:忘记|遗忘|删除|清空|抹除|清除)(?!了吗|了没|了什么|了谁).+|(?:把|将).{0,20}(?:我的|关于我).{0,20}(?:忘记|删除|清空|抹除|清除)|(?:只在|仅在)(?:本群|这个群|这群)(?:别提|不要提|不再提)|恢复.{0,10}记忆')
_VIEW = re.compile(r'^(?:(?:娅娅|达妮娅)[，,：: ]*)?(?:查看(?:我的|对我的)印象|你对我(?:有什么|的|有啥|什么)?印象|你觉得我是(?:什么样的人|怎样的人)|我的印象)[？?。！!]*$')
SCHEMA = """
CREATE TABLE IF NOT EXISTS person_impression_events(
 id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, event_key TEXT NOT NULL,
 trait TEXT NOT NULL, direction INTEGER NOT NULL CHECK(direction IN (-1,1)),
 quote TEXT NOT NULL, source_group INTEGER NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(user_id,event_key,trait));
CREATE INDEX IF NOT EXISTS idx_impression_person ON person_impression_events(user_id,id DESC);
"""


def removal_requested(text: str) -> bool:
    return bool(_REMOVAL.search(text))


def impression_requested(text: str) -> bool:
    return bool(_VIEW.fullmatch(text.strip()))


def impression_instruction() -> str:
    return (
        '[即时个人印象]\n在同一回复JSON中可加 impression_updates 数组，最多2项：'
        '{"trait":"受控维度","direction":1,"quote":"当前用户本条原话连续片段"}。'
        '维度为' + '；'.join(f'{k}={v}' for k, v in TRAITS.items()) + '。'
        'direction仅可为1或-1，表示本次互动支持或反对该交流倾向。'
        '只依据当前用户实际表达方式，不依据他人评价、用户要求你给他的标签或你自己的回答。'
        '没有明确证据就留空；一次表现只产生暂时印象，不是已核实的人格结论。'
        '不要推断疾病、身份、年龄、性别或私人关系。quote必须是当前原话中4至160字的片段。'
        '只在回复成功送达后更新，同一人格对同一用户跨群共享，不需要重复数天。'
        '不提供聊天遗忘、删除、清空或恢复资料操作；可查看自己的印象和明确纠正自述资料。'
    )


class PersonalImpressions:
    def __init__(self, people) -> None:
        self.people = people

    def record(self, user_id: int, group_id: int, event_id: str, source: str,
               updates, now: str) -> int:
        if (not self.people.enabled() or not event_id or not isinstance(updates, (list, tuple))
                or SENSITIVE.search(source) or THIRD_PARTY.search(source)
                or _CONTROL.search(source) or removal_requested(source)):
            return 0
        count = 0
        with self.people.connect() as conn:
            conn.executescript(SCHEMA)
            for update in updates[:2]:
                if not isinstance(update, dict):
                    continue
                trait, direction, quote = (update.get(k) for k in ('trait', 'direction', 'quote'))
                if (not isinstance(trait, str) or trait not in TRAITS or type(direction) is not int
                        or direction not in (-1, 1) or not isinstance(quote, str)
                        or not 4 <= len(quote) <= 160 or quote not in source
                        or self.people.blocked(user_id, group_id, quote)):
                    continue
                count += conn.execute(
                    'INSERT OR IGNORE INTO person_impression_events(user_id,event_key,trait,direction,quote,source_group,created_at) VALUES(?,?,?,?,?,?,?)',
                    (user_id, f'{group_id}:{event_id}', trait, direction, quote, group_id, now)).rowcount
        return count

    def recall(self, user_id: int) -> list[dict]:
        if not self.people.enabled():
            return []
        with self.people.connect() as conn:
            conn.executescript(SCHEMA)
            rows = conn.execute('SELECT * FROM person_impression_events WHERE user_id=? ORDER BY id DESC LIMIT 96', (user_id,)).fetchall()
        grouped = {}
        restrictions = self.people.restrictions(user_id, 0)
        for row in rows:
            if row['trait'] not in TRAITS or any(n in re.sub(r'\s+', '', row['quote']).lower() for n in restrictions):
                continue
            values = grouped.setdefault(row['trait'], [])
            if len(values) < 8:
                values.append(dict(row))
        result = []
        for trait, evidence in grouped.items():
            # Event recency, not elapsed days: one new observation can change
            # direction, repeated supporting evidence stabilises the estimate.
            weights = [0.5 ** i for i in range(len(evidence))]
            score = sum(r['direction'] * w for r, w in zip(evidence, weights)) / sum(weights)
            if score > 0.2:
                result.append(dict(trait=trait, text=TRAITS[trait], observations=len(evidence),
                                   quote=evidence[0]['quote'], updated_at=evidence[0]['created_at']))
        return result[:6]

    def prompt(self, user_id: int) -> str:
        rows = self.recall(user_id)
        if not rows:
            return ''
        return '[对当前用户的暂时交流印象，跨群共享；是推断而非客观身份，可被后续互动修正]\n' + '\n'.join(
            f"· {r['text']}；参考原话：{r['quote']}" for r in rows)

    def view(self, user_id: int) -> str:
        rows = self.recall(user_id)
        if not rows:
            return '目前还没有形成可以展示的具体交流印象。之后的实际互动会逐步补充。'
        return '目前对你的交流印象：\n' + '\n'.join(
            f"· {r['text']}（{'初步观察' if r['observations'] == 1 else '近期多次观察'}）" for r in rows
        ) + '\n这些是交流中的暂时印象，会随之后的互动更新。'
