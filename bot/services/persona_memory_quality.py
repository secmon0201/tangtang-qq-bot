"""Conservative personal-fact extraction; conversation is not automatically identity."""
from __future__ import annotations

import re

_REQUEST = re.compile(r"^(?:(?:糖糖|娅娅|达妮娅)[，,：: ]*)?(?:(?:只|仅)在(?:本群|这个群))?(?:请)?记住(?:一下)?[：:，, ]*(.+)$")
_PREFIX = re.compile(r"^(?:(?:糖糖|娅娅|达妮娅)[，,：: ]*)?(?:更正[，,:： ]*|其实)?")
_FACT = re.compile(r"^(?:我(?:现在|已经|其实)?(?:叫|最喜欢|不再喜欢|不喜欢|喜欢|讨厌).+|(?:在)?(?:本群|这个群|这里)?(?:请)?(?:叫我|称呼我).+|我(?:是(?:学生|教师|程序员|设计师|上班族)|的爱好是.+))$")
_BAD = re.compile(r"[？?\n，,；;。]|(?:吗|么|呢)[！!。]*$|是不是|是否|假如|如果|假装|扮演|开玩笑|借我|转账|我是问|我叫你|他说|她说|引用|转发|[“”\"\[\]]")
_RELATION = re.compile(r"(?:喜欢|讨厌|爱|想念)(?:你|糖糖|娅娅|达妮娅)|(?:主人|心魔|猫娘|男友|女友|恋人|中之人|中の人|赛博生命)|(?:我希望|我想要)")
_PERSONAL_QUESTION = re.compile(r'(?:叫|称呼|喜欢|讨厌)(?:什么|谁|哪)|^我(?:现在|其实)?是(?:谁|什么|哪)')


def fact_rejection(content: str) -> str:
    """Also applies to old rows at recall; never deletes the source record."""
    text = content.strip(' ，。！!')
    if len(text) < 3 or len(text) > 160:
        return 'fragment'
    if _BAD.search(text) or _PERSONAL_QUESTION.search(text):
        return 'question_quote_or_roleplay'
    if _RELATION.search(text):
        return 'relationship_or_persona_instruction'
    if not _FACT.fullmatch(text):
        return 'not_stable_personal_fact'
    return ''


def extract_personal_fact(text: str) -> tuple[str, bool] | None:
    clean = text.strip()
    request = _REQUEST.fullmatch(clean)
    explicit = request is not None
    content = request[1] if request else _PREFIX.sub('', clean, count=1)
    content = content.strip(' ，。！!')
    # Do not infer the truth of an embedded clause or a question.
    if fact_rejection(content):
        return None
    return content, explicit
