"""Voice intent and textual consistency; synthesis never makes these choices."""
from __future__ import annotations

import re
from dataclasses import dataclass

from bot.services.tangtang_reply import ReplyPlan


_NO_VOICE = re.compile(r"(?:不要|别|不用|不想听|不必).{0,6}(?:语音|声音|说话)|(?:只|就|还是)(?:要|用|发)?(?:文字|打字)|打字就好")
_REQUEST = re.compile(r"(?:发|来|听|用|说|读|念|播|讲).{0,8}(?:语音|声音)|(?:语音|声音).{0,8}(?:发|听|回复|回答)|(?:读|念)给我听")
_REFUSAL = re.compile(r"(?:不|没|别|休想|才不|懒得).{0,8}(?:发|用|给|开口|念|读|语音|说话|出声|录音|张嘴)|(?:语音|声音).{0,8}(?:不|没|算了)|(?:还是|只|就)(?:打字|文字)")
_PROMISE = re.compile(r"(?:语音|声音|录音).{0,8}(?:发好了|发出去|发给你了|收到|听一下|已经)|(?:已|正在|马上|这就|给你|等我).{0,8}(?:语音|录音)|听我说|听好了|这就发|那我发|给你发一条")
_VISUAL = re.compile(r"https?://|```|\|.+\||\[(?:图片|消息|表情)|<[^>]+>")


@dataclass(frozen=True, slots=True)
class DeliveryDecision:
    voice: bool
    explicit: bool
    text: str
    fallback: tuple[str, ...]
    reason: str = ""


def voice_request(text: str) -> str:
    if _NO_VOICE.search(text):
        return "text"
    return "voice" if _REQUEST.search(text) else "ordinary"


def choose_delivery(plan: ReplyPlan, user_text: str, *, available: bool,
                    random_candidate: bool, unavailable_reason: str = "") -> DeliveryDecision:
    request = voice_request(user_text)
    explicit = request == "voice"
    text = plan.text
    fallback = plan.text_fallback or plan.messages
    if any(_PROMISE.search(part) for part in fallback):
        fallback = ("这次先用文字回复吧。",)
    if not plan.decided:
        return DeliveryDecision(False, explicit, "", ())
    refusal = plan.voice in {"decline", "text"} or bool(_REFUSAL.search(text))
    suitable = bool(text.strip()) and len(text) <= 200 and not _VISUAL.search(text)
    accepted = plan.voice == "accept" if explicit else random_candidate and plan.voice == "auto"
    use_voice = (available and request != "text" and plan.structured and accepted
                 and not refusal and suitable and not _PROMISE.search(text))
    if use_voice:
        return DeliveryDecision(True, explicit, text, fallback)
    if explicit and not available:
        return DeliveryDecision(False, True, "", (f"现在暂时发不了语音（{unavailable_reason or '语音不可用'}），先打字吧。", *fallback))
    return DeliveryDecision(False, explicit, "", fallback if plan.voice == "accept" or _PROMISE.search(text) else plan.messages)


def delivery_instruction(status: str, candidate: bool, expressions: tuple[str, ...]) -> str:
    return (
        "\n[最终输出契约：覆盖前面的消息标记格式]\n"
        '只输出 JSON：{"decision":"reply或silent","messages":["实际对白"],'
        '"voice":"auto或accept或decline或text","text_fallback":["语音未发送时也成立的文字回答"],'
        '"expression":"最合适的表情ID或空字符串","expression_candidates":["同样适合的可替代ID"]}。不输出思考、分析、L1、代码围栏。'
        f"语音状态：{status}；本轮普通语音候选：{'是' if candidate else '否'}。"
        "用户要求文字时 voice=text；主动要求语音时，你决定是否愿意，答应用 accept，拒绝用 decline。"
        "普通聊天用 auto，觉得不适合语音则用 text。拒绝必须保持文字；不能嘴上拒绝却选择语音。"
        "语音不可用时如实说明能力限制，不伪装成自己不愿意。silent 时 messages 为空，不能发任何东西。"
        "messages 是直接说出口的内容，不写发语音的过程、承诺、旁白或内心独白。"
        "text_fallback 回答同一个问题，但不能声称发了、将发语音，不要包含格式或控制标记。"
        "需要代码、链接或长篇说明时选择文字，不为语音删掉关键信息。"
        "表情可选且最终最多发送一个；expression 是首选，expression_candidates 尽量提供1至3个同组且同样贴合本轮语境的替代ID，"
        "程序会在你认可的候选里按近期使用量抽选。没有合适替代就留空，不为凑数跨情绪选图。"
        "不要因为名称熟悉反复只选旧ID；检查图片说明与回答的态度一致。不能提交文件路径；语音或沉默不附表情。可用ID：" + "、".join(expressions)
    )
