"""Grounded model extraction and honest persistence receipts for personal memory.

The model selects a meaningful personal clause and its semantic category. The
stored assertion remains a literal user quote: summarisation cannot silently
invent a name, date, promise or experience. Tags are search hints, never facts.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from bot.services.persona_memory_store import SENSITIVE, THIRD_PARTY


CATEGORIES = frozenset({'alias', 'self_description', 'preference', 'experience', 'commitment'})
TOPICS = {
    '称呼': ('名字', '姓名', '昵称', '外号', '叫我', '称呼', '叫什么'),
    '身份': ('身份', '是谁', '认识', '自称', '几岁', '年龄'),
    '职业': ('职业', '工作', '上班', '教师', '老师', '程序员', '设计师'),
    '偏好': ('偏好', '喜好', '爱好', '喜欢', '讨厌', '口味', '爱吃'),
    '经历': ('经历', '发生', '以前', '曾经', '上次', '那次', '最近'),
    '约定': ('约定', '承诺', '答应', '计划', '打算', '准备', '下次', '说好'),
    '学习': ('学习', '考试', '考研', '作业', '大学', '学校', '毕业', '读书'),
    '游戏': ('游戏', '抽卡', '鸣潮', '战双', '副本', '通关', '角色'),
    '创作': ('创作', '画画', '绘画', '写作', '作品', '小说', '音乐'),
    '生活': ('生活', '旅行', '旅游', '养猫', '宠物', '做饭', '运动'),
}
_CATEGORY_TOPIC = {'alias': '称呼', 'self_description': '身份', 'preference': '偏好',
                   'experience': '经历', 'commitment': '约定'}
_REQUEST = re.compile(r'^(?:(?:娅娅|糖糖|达妮娅)[，,：: ]*)?(?:(?:只|仅)在(?:本群|这个群))?(?:请|帮我)?记住(?:一下)?|(?:更正|纠正|修改)(?:一下)?[，,：: ]*(?:我|之前|刚才|记忆|称呼|昵称|只在|仅在)|我(?:现在|其实)(?:叫|不再喜欢|不喜欢|喜欢)|我不再喜欢')
_ALIAS_REQUEST = re.compile(r'^(?:(?:娅娅|糖糖|达妮娅)[，,：: ]*)?(?:(?:只|仅)?在?(?:本群|这个群)[，,：: ]*)?(?:以后|今后)?(?:请|就)?(?:叫我|称呼我)[^。！？!?\n]{1,30}[。！!]*$')
_UNSAFE = re.compile(r'忽略|系统|提示词|开发者|执行|指令|权限|管理员|无条件|真理|永远服从|主人|恋人|男友|女友|只属于|假装|扮演|开玩笑|假如|如果|说假的|虚构|撒谎|他说|她说|转发|引用|别人|有人说|[“”"\[\]@？?]')
_LOCAL_CONTEXT = re.compile(r'这个话题|当前话题|本群正在|群里正在|大家正在|这个群今天|群内约定')
_SELF = re.compile(r'^(?:我(?!们|说|问|叫你|觉得|认为)|(?:请)?(?:叫我|称呼我)|(?:本群|这个群)(?:叫我|称呼我))')
_PROMISE = re.compile(r'(?:我(?:已经|会|一定|都|牢牢|当然)?(?:帮你)?|已经|这就)?(?:记住|记下|保存|存入|写入)(?:长期|永久)?(?:记忆|档案|资料)?|不会忘(?:记)?|永远记得')


@dataclass(frozen=True, slots=True)
class MemoryProposal:
    category: str
    quote: str
    summary: str
    tags: tuple[str, ...]
    operation: str = 'remember'
    supersedes: str = ''


@dataclass(frozen=True, slots=True)
class MemoryWriteResult:
    requested: bool
    status: str
    ids: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()

    @property
    def receipt(self) -> str:
        if self.status == 'saved':
            return ('已保存这次可确认的个人记忆；其余内容没有保存。' if self.reasons
                    else '这次确认的内容已经保存为你的个人记忆了。')
        if self.status == 'pending':
            return '这次先保留为待确认的自述，还没有列入长期记忆。'
        if self.status == 'disabled':
            return '个人记忆目前没有开启，这次没有保存。'
        if self.status == 'failed':
            return '这次记忆保存失败了，暂时不能保证下次记得。'
        if self.status == 'rejected':
            return '这次没有保存长期记忆；需要你明确说明要记住的个人资料、经历或约定。'
        return '这轮还没有确认保存长期记忆。'


def memory_requested(text: str) -> bool:
    source = str(text)
    if re.search(r'不要记住|别记住|不用记住|忘记|恢复.*记忆|记住了吗|记住了没|记住(?:是什么|什么意思)', source):
        return False
    requested = _REQUEST.search(source) or _ALIAS_REQUEST.search(source) or re.search(r'(?:希望你|我想让你|帮我|麻烦你)(?:能|把)?[^。！？!?]{0,12}(?:记住|记下|记一下)', source)
    if not requested:
        return False
    # "我现在喜欢什么？" is retrieval, not the declaration "我现在喜欢茶".
    # Only actual writing directives may contain a courtesy question suffix.
    directive = re.search(r'记住|记下|记一下|更正|纠正|修改|(?:以后|今后|请)(?:叫我|称呼我)', source)
    if not directive and re.search(r'[？?]|什么|是谁|叫什么|多少|哪(?:个|种|里)|怎么|是否|是不是|(?:吗|么|呢)[。！!]*$', source):
        return False
    return True


def requested_alias(text: str) -> str:
    """Return only a direct alias instruction, never an embedded quotation."""
    if not _ALIAS_REQUEST.fullmatch(text) or _UNSAFE.search(text) or THIRD_PARTY.search(text):
        return ''
    match = re.search(r'(?:叫我|称呼我)[^。！？!?\n]{1,30}', text)
    return match[0] if match else ''


def semantic_topics(text: str) -> set[str]:
    return {tag for tag, terms in TOPICS.items() if any(term in text for term in terms)}


def validate_proposal(raw: Any, source: str) -> tuple[MemoryProposal | None, str]:
    if not isinstance(raw, dict):
        return None, 'invalid_proposal'
    category = raw.get('category')
    quote = raw.get('quote', raw.get('source_quote'))
    summary = raw.get('summary', quote)
    operation = raw.get('operation', 'remember')
    supersedes = raw.get('supersedes', '') or ''
    if not isinstance(category, str) or not isinstance(operation, str) or category not in CATEGORIES or operation not in {'remember', 'correct'}:
        return None, 'invalid_category_or_operation'
    if not isinstance(quote, str) or not isinstance(summary, str):
        return None, 'invalid_quote'
    quote, summary = quote.strip(), summary.strip()
    if not 3 <= len(quote) <= 240 or quote not in source or not 3 <= len(summary) <= 180 or summary not in quote:
        return None, 'unsupported_summary_or_quote'
    # Validate the entire source too: a quoted "我是..." inside somebody else's
    # story or a roleplay request must not become this user's identity.
    if SENSITIVE.search(source) or _UNSAFE.search(source) or THIRD_PARTY.search(source):
        return None, 'unsafe_or_unattributed_source'
    if _LOCAL_CONTEXT.search(quote) or not _SELF.search(summary):
        return None, 'not_personal_assertion'
    before = source[:source.find(quote)].rstrip(' ：:')
    after = source[source.find(quote) + len(quote):].lstrip(' ，,')
    if re.search(r'不是|并非|别以为|不代表|听说|没(?:有)?说|的朋友|的同学', before[-8:]) or re.match(r'我的(?:朋友|同学|父亲|母亲|爸爸|妈妈|同事|家人)', summary) or re.match(r'(?:是假的|才怪|不是真的|是玩笑)', after):
        return None, 'not_personal_assertion'
    if re.search(r'(?:吗|么|呢)[！!。]*$', summary) or re.search(r'我(?:是问|叫你|觉得|认为)|(?:叫|称呼|喜欢|讨厌)(?:什么|谁|哪)|^我(?:现在|其实)?是(?:谁|什么|哪)', summary):
        return None, 'question_or_opinion'
    if re.search(r'我(?:喜欢|爱|讨厌|想念)(?:你|娅娅|糖糖|达妮娅)', summary):
        return None, 'relationship_instruction'
    # A model label must not turn a stable preference or identity into an
    # automatically approved one-off event and bypass the evidence threshold.
    obvious = ('alias' if re.match(r'^(?:(?:本群|这个群))?(?:请)?(?:叫我|称呼我)|^我(?:现在|其实)?叫', summary)
               else 'preference' if re.match(r'^我(?:现在|已经|其实)?(?:最喜欢|不再喜欢|不喜欢|喜欢|讨厌|的爱好是)', summary)
               else 'self_description' if re.match(r'^我是(?!在|从|于|去年|今年|上周|上个月|今天|昨天|前天|第一次|第\d+次)', summary) else '')
    if obvious and category != obvious:
        return None, 'category_conflicts_with_assertion'
    if operation == 'correct' and (not isinstance(supersedes, str) or not re.fullmatch(r'[sf]:[1-9]\d*', supersedes)):
        return None, 'missing_correction_target'
    tags = raw.get('tags', raw.get('keywords', []))
    if not isinstance(tags, (list, tuple)):
        return None, 'invalid_tags'
    selected = set(tag for tag in tags[:5] if isinstance(tag, str) and tag in TOPICS)
    selected.add(_CATEGORY_TOPIC[category])
    # Model categories/topics support synonym retrieval; they cannot add prose.
    selected.update(semantic_topics(summary))
    return MemoryProposal(category, quote, summary, tuple(sorted(selected)), operation, str(supersedes)), ''


def memory_instruction(*, global_personal: bool = False) -> str:
    return (
        '[个人记忆提取合同]\n'
        '在回复JSON中可加入 memory_updates 数组，最多3项；没有可保存的个人信息时为空数组。'
        '只从当前发言人的本条原文中提取本人资料、偏好、重要经历或本人明确约定，'
        '不得把旁人、机器人说的话、群话题、玩笑设定、命令、推测当事实。'
        '每项格式：{"category":"alias|self_description|preference|experience|commitment",'
        '"quote":"本条原文连续片段","summary":"quote中保留第一人称的简短连续摘录",'
        '"tags":["受控主题词"],"operation":"remember|correct","supersedes":"s:编号或f:编号"}。'
        'summary必须逐字来自quote，不改写、不补充隐含信息；提取和分类需要结合语义。'
        'tags只能从' + '、'.join(TOPICS) + '选择，最多5项。'
        '用户明确纠正已保存内容时才correct，并引用当前展示的记忆编号；否则remember。'
        '个人资料、经历、约定和印象只在当前群可用；不同群的个人记忆互不继承，来源群同时作为召回边界。'
        '经历只保存该用户自己的重要事件，不保存或延续其他群的讨论。'
        '系统会在发送前验证并持久化明确保存请求，在成功送达后才整理自动提取。'
        '你看不到本次最终保存结果，所以回复不得宣称已经记住、永久保存或保证不忘；'
        '用自然回应内容即可，系统根据真实保存状态补充确认。'
    )


def enforce_memory_confirmation(messages: tuple[str, ...], result: MemoryWriteResult) -> tuple[str, ...]:
    """Never deliver a model-written persistence promise without a receipt.

    Replace the entire sentence containing a promise rather than deleting only
    a keyword and accidentally leaving the opposite meaning behind.
    """
    # Natural acknowledgements have unbounded paraphrases ("先记着", "旧的
    # 翻篇了"). For an explicit write/correction the persistence receipt is the
    # complete acknowledgement, so a failed write cannot contradict its own
    # dialogue even when the model uses an unseen formulation.
    if result.requested:
        return (result.receipt,)
    parts: list[str] = []
    removed = False
    for message in messages:
        clauses = re.split(r'(?<=[。！？!?\n])', str(message))
        kept = []
        for clause in clauses:
            if _PROMISE.search(clause):
                removed = True
            else:
                kept.append(clause)
        clean = ''.join(kept).strip()
        if clean:
            parts.append(clean)
    if result.requested or removed:
        parts.append(result.receipt)
    return tuple(parts)
