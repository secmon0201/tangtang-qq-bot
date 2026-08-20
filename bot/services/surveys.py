from __future__ import annotations

import re
from dataclasses import dataclass


SURVEY_ID_RE = re.compile(
    r"^[\s\u3000#:：]*(?:(?:id|编号)[\s\u3000#:：]*)?(\d{1,20})[\s\u3000]*$",
    re.IGNORECASE,
)
SURVEY_FIELD_RE = re.compile(r"^\s*([^:：\s]+)\s*[:：]\s*(.*)\s*$")
SURVEY_FIELD_ALIASES = {
    "问卷内容": "question",
    "问券内容": "question",
    "调查内容": "question",
    "调查群": "groups_raw",
    "问卷群": "groups_raw",
    "问券群": "groups_raw",
}


@dataclass(frozen=True, slots=True)
class SurveyCreatePayload:
    question: str
    group_ids: tuple[int, ...]


def parse_survey_id(raw: str) -> int | None:
    match = SURVEY_ID_RE.fullmatch(str(raw).strip())
    return int(match.group(1)) if match else None


def parse_survey_group_ids(raw: str) -> tuple[int, ...]:
    values = tuple(dict.fromkeys(int(value) for value in re.findall(r"\d{4,20}", raw)))
    if not values:
        raise ValueError("调查群不能为空，请用英文逗号分隔群号")
    return values


def parse_survey_create_payload(raw: str) -> SurveyCreatePayload:
    fields: dict[str, str] = {}
    lines = [line for line in raw.strip().splitlines() if line.strip()]
    for line in lines:
        match = SURVEY_FIELD_RE.match(line)
        if not match:
            continue
        field = SURVEY_FIELD_ALIASES.get(match.group(1).strip())
        if field is not None:
            if field in fields:
                raise ValueError(f"字段重复：{match.group(1).strip()}")
            fields[field] = match.group(2).strip()

    if not fields and "|" in raw:
        parts = [part.strip() for part in raw.split("|")]
        if len(parts) == 2:
            fields = {
                "question": parts[0],
                "groups_raw": parts[1],
            }

    missing = [
        label
        for key, label in (
            ("question", "问卷内容"),
            ("groups_raw", "调查群"),
        )
        if not fields.get(key)
    ]
    if missing:
        raise ValueError("创建问卷调查必须填写：" + "、".join(missing))

    question = fields["question"].strip()
    if len(question) > 1000:
        raise ValueError("问卷内容不能超过 1000 个字符")
    return SurveyCreatePayload(question, parse_survey_group_ids(fields["groups_raw"]))


def survey_status_label(status: str) -> str:
    return "进行中" if status == "active" else "已结束"
