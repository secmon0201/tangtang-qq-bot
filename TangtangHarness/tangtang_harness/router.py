"""A token-free router that keeps compound requests complete."""
from __future__ import annotations

from dataclasses import dataclass, field
import re

from .types import InboundEvent, ToolCall
from .core_protocol import game_prefix


@dataclass(slots=True)
class RouteDecision:
    kind: str
    tools: list[ToolCall] = field(default_factory=list)
    chat_text: str = ""
    reason: str = ""


_SPLIT = re.compile(r"\s*(?:然后|接着|最后|顺便|并且|还要|再帮我|再给我|再查|再看)\s*")
_ANALYSIS = re.compile(r"^(?:再|请|帮我|给我|一下|并)?\s*(?:分析|解释|评价|说说|说明|为什么|怎么|聊聊|讲个|说个|谈谈)")
_PERIODS = {"日": "day", "日榜": "day", "今天": "day", "今日": "day", "周": "week", "周榜": "week", "本周": "week", "月": "month", "月榜": "month", "本月": "month", "总": "total", "总榜": "total", "全部": "total", "day": "day", "week": "week", "month": "month", "all": "total", "total": "total"}
_SWITCH = {"开": True, "开启": True, "启用": True, "on": True, "关": False, "关闭": False, "停用": False, "off": False}
_SIMPLE = {
    "帮助": "user_help", "帮助文字": "user_help", "管理员帮助": "user_help", "超级管理员帮助": "user_help",
    "机器人状态": "robot_status", "游戏列表": "mini_game_help", "小游戏列表": "mini_game_help",
    "装填": "roulette_load", "开枪": "roulette_fire", "装弹": "bomb_load", "骰子": "dice_start", "猜数": "guess_start",
    "今日老婆": "wife_draw", "今日缘分": "wife_draw", "离婚": "wife_divorce", "解缘": "wife_divorce",
    "A魂帮助": "asoul_help", "bot帮助": "asoul_help", "今日直播": "today_live", "明日直播": "tomorrow_live", "本周直播": "week_live",
    "刷新枝江直播": "zhijiang_refresh", "QQ平台自检": "qq_platform_health", "QQ传输维护状态": "qq_transport_status",
    "查重网页": "operator_web", "查重面板": "operator_web", "白名单网页": "operator_web", "公告面板": "operator_web", "公告网页": "operator_web",
    "bili_status": "bili_status", "bili_login": "bili_login", "bili_logout": "bili_logout",
    "达妮娅美图": "denia_gallery", "娅娅美图": "denia_gallery",
}


def _mentions(event: InboundEvent) -> list[int]:
    return list(dict.fromkeys(int(segment["data"]["qq"]) for segment in event.segments
        if segment.get("type") == "at" and str(segment.get("data", {}).get("qq", "")).isdigit()
        and int(segment["data"]["qq"]) != event.self_id))


def _target(event: InboundEvent) -> int | None:
    mentions = _mentions(event)
    return mentions[0] if len(mentions) == 1 else None


def _period(text: str) -> str:
    for label in ("本周", "本月", "今日", "今天", "全部", "周", "月", "总"):
        if label in text:
            return _PERIODS[label]
    return "day"


def _command(text: str, event: InboundEvent) -> ToolCall | str | None:
    raw = text.lstrip("# ").strip()
    tokens = raw.split()
    if not tokens:
        return None
    head = tokens[0]
    rest = " ".join(tokens[1:])
    if head in _SIMPLE and not (head == "装弹" and rest.startswith("成语")):
        args = {"page": "announcement" if "公告" in head else "whitelist" if "白名单" in head else "duplicate"} if _SIMPLE[head] == "operator_web" else {}
        if head == "帮助文字":
            args = {"format": "text"}
        elif head in {"管理员帮助", "超级管理员帮助"}:
            args = {"audience": "admin"}
        return ToolCall(_SIMPLE[head], args)
    if head in {"我的缘分", "我的老婆"}:
        return ToolCall("wife_personal", {"page": int(rest) if rest.isdigit() else 1})
    if head in {"群缘分", "群老婆"}:
        return ToolCall("wife_group", {"history": rest == "历史", **({"date": rest} if re.fullmatch(r"\d{4}-\d{2}-\d{2}", rest) else {})})
    if head in {"丢给", "强取"} or re.match(r"^.{4}\s*#\s*丢给", text):
        target = _target(event)
        if target is None:
            return "请真实 @ 一位其他群成员。"
        idiom = re.match(r"^([\u4e00-\u9fff]{4})\s*#\s*丢给", text)
        name = "wife_take" if head == "强取" else "idiom_bomb_pass" if idiom else "bomb_pass"
        return ToolCall(name, {"target_user_id": target, **({"idiom": idiom[1]} if idiom else {})})
    if head in {"装弹成语", "装弹"} and (head == "装弹成语" or rest.startswith("成语")):
        params = rest.removeprefix("成语").strip().split()
        return ToolCall("idiom_bomb_load", {"mode": "entertainment" if "娱乐" in params else "professional",
            "duration": next((int(value) for value in params if value.isdigit()), 120)})
    if head == "猜" or re.fullmatch(r"猜\d+", head):
        number = rest if head == "猜" else head[1:]
        return ToolCall("guess_submit", {"value": int(number)}) if number.isdigit() else "请输入 0–999 的数字。"
    rank = re.fullmatch(r"(转盘|俄罗斯转盘|炸弹|定时炸弹|骰子|幸运骰局|猜数|猜数字)(总)?榜(?:单)?", head)
    if rank:
        games = {"转盘": "roulette", "俄罗斯转盘": "roulette", "炸弹": "bomb", "定时炸弹": "bomb", "骰子": "dice", "幸运骰局": "dice", "猜数": "guess", "猜数字": "guess"}
        return ToolCall("mini_game_" + games[rank[1]], {"cluster": bool(rank[2])})
    rank = re.fullmatch(r"(集群)?(?:发言排行|发言榜|发言统计|统计)(日|周|月|总)?", head)
    if rank:
        period = rank[2] or rest or "日"
        if period not in _PERIODS:
            return "发言排行用法：日|周|月|总。"
        return ToolCall("ranking", {"scope": _PERIODS[period], "cluster": bool(rank[1])})
    named_rank = re.fullmatch(r"(.+?)(?:发言排行|发言榜|发言统计|统计)(日|周|月|总)?", head)
    if named_rank:
        period = named_rank[2] or rest or "日"
        if period not in _PERIODS:
            return "集群发言排行用法：日|周|月|总。"
        return ToolCall("ranking", {"scope": _PERIODS[period], "cluster": True, "cluster_name": named_rank[1]})
    if head == "QQ传输离线记录":
        return ToolCall("qq_transport_status", {"limit": min(20, max(1, int(rest))) if rest.isdigit() else 5})
    if head in {"群聊搜索", "搜索群聊", "搜群聊"}:
        params = tokens[1:]
        if not params:
            return "请提供要搜索的群聊关键词，例如 #群聊搜索 关键词。"
        page = int(params.pop()) if len(params) > 1 and params[-1].isdigit() else 1
        return ToolCall("group_chat_search", {"keyword": " ".join(params), "page": page})
    if head in {"发言记录", "发言搜索", "发言画像", "画像"}:
        params = tokens[1:]
        target = _target(event) or (int(params.pop(0)) if params and params[0].isdigit() else event.user_id)
        args = {"target_user_id": target}
        if head == "发言搜索":
            if not params:
                return "请提供搜索关键词。"
            if len(params) > 1 and params[-1].isdigit():
                args["page"] = int(params.pop())
            args["keyword"] = " ".join(params)
        elif params and params[-1].isdigit():
            args["page"] = int(params[-1])
        return ToolCall({"发言记录": "archive_records", "发言搜索": "archive_search"}.get(head, "archive_profile"), args)
    if head in {"AI发言画像", "画像生成"}:
        action = {"查看": "list", "状态": "list", "历史": "history"}.get(rest, "generate")
        return ToolCall("profile_generate", {"target_user_id": _target(event) or event.user_id, "action": action})
    if head == "排行后续":
        return ToolCall("tool_followup", {"text": rest}) if rest else "请补充要查看的名次，例如 #排行后续 第二名是谁。"
    if head in {"表情", "表情包"}:
        return ToolCall("expression_send", {"selector": rest or "表情"})
    if head in {"清游", "确认", "取消", "清缘", "确认清缘", "取消清缘"}:
        return ToolCall("wife_clear" if "缘" in head else "mini_game_clear", {"confirm": head.startswith("确认"), "action": "cancel" if head.startswith("取消") else "clear"})
    if head in {"群设置", "本群设置"}:
        if len(tokens) < 2 or tokens[1] in {"状态", "列表"}:
            return ToolCall("group_feature_status", {})
        if tokens[1] == "代称":
            return ToolCall("group_settings", {"action": "alias", "value": " ".join(tokens[2:])})
        if tokens[1] == "过滤":
            action = {"列表": "filter_status", "添加": "filter_add", "移除": "filter_remove"}.get(tokens[2] if len(tokens) > 2 else "", "filter_status")
            if action != "filter_status" and (len(tokens) != 4 or not tokens[3].isdigit()):
                return "请提供过滤名单用户编号。"
            return ToolCall("group_settings", {"action": action, **({"user_id": int(tokens[3])} if len(tokens) == 4 else {})})
        params = tokens[1:]
        if params[0] == "功能":
            params = params[1:]
        if not params:
            return ToolCall("group_feature_status", {})
        if len(params) > 1 and params[-1] in _SWITCH:
            return ToolCall("group_settings", {"action": "set", "feature": " ".join(params[:-1]), "enabled": _SWITCH[params[-1]]})
        if len(params) > 1 and params[0] in _SWITCH:
            return ToolCall("group_settings", {"action": "set", "feature": " ".join(params[1:]), "enabled": _SWITCH[params[0]]})
        return "群设置用法：#群设置 <功能> 开|关。"
    if head.startswith("开关"):
        return ToolCall("group_settings", {"action": "toggle", "feature": head.removeprefix("开关")})
    if head in {"白名单", "白名单列表", "白名单菜单"}:
        operation = tokens[1].casefold() if len(tokens) > 1 else "列表"
        action = {"添加": "add", "add": "add", "删除": "remove", "移除": "remove", "delete": "remove", "remove": "remove",
                  "列表": "list", "list": "list", "菜单": "list", "menu": "list"}.get(operation)
        if action is None:
            return "白名单用法：添加|删除|列表 QQ号 [备注]。"
        if action != "list" and (len(tokens) < 3 or not tokens[2].isdigit()):
            return "请提供白名单用户编号。"
        return ToolCall("whitelist", {"action": action, **({"user_id": int(tokens[2]), "note": " ".join(tokens[3:])} if action != "list" else {})})
    if head in {"查重", "查重1", "查重2", "查重3", "查重4"}:
        return ToolCall("duplicate_scan", {"mode": "source" if head in {"查重", "查重2", "查重4"} else "all", "ignore_whitelist": head in {"查重3", "查重4"}, **({"group_ids": [int(value) for value in tokens[1:] if value.isdigit()]} if rest else {})})
    if head.startswith("收录"):
        action = {"收录确认": "approve", "收录拒绝": "reject", "收录恢复待审": "revive"}.get(head, "pending")
        if action != "pending" and not rest.isdigit():
            return "请提供待审词条编号。"
        return ToolCall("knowledge_review", {"action": action, **({"entry_id": int(rest)} if rest.isdigit() else {})})
    if head in {"枝江直播", "直播日程"}:
        return ToolCall("zhijiang_status" if rest == "状态" else "zhijiang_schedule", {})
    if head in {"人格"}:
        if rest in {"印象", "我的印象"}:
            return ToolCall("persona_impression", {})
        if rest.startswith("成长"):
            return ToolCall("growth_manage", {"text": rest})
        if rest.startswith(("语音", "后台整理")):
            return ToolCall("chat_settings", {"text": "系统设置 " + rest.replace("后台整理", "人格后台整理", 1)})
        return ToolCall("persona_status", {})
    if head == "技能" or head.startswith("技能"):
        prefix = {"技能列表": "列表", "技能开关": "开关", "技能纠错": "纠错"}.get(head, "")
        return ToolCall("skill_admin", {"text": (prefix + " " + rest).strip()})
    if head in {"糖糖模型", "糖糖主动回复", "糖糖主动", "主动回复"}:
        return ToolCall("chat_settings", {"text": raw})
    if head in {"日程高亮", "取消日程高亮", "日程高亮列表", "取消日程高亮记录"}:
        if head == "取消日程高亮记录":
            return ToolCall("asoul_highlight", {"action": "remove_record", "index": int(rest) if rest.isdigit() else 0})
        action = "list" if head == "日程高亮列表" else "remove" if head.startswith("取消") else "set"
        if action != "list" and (len(tokens) < 2 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", tokens[1])):
            return "请提供 YYYY-MM-DD 日期。"
        if head == "日程高亮" and len(tokens) == 2:
            action = "list_day"
        if head == "取消日程高亮" and (len(tokens) != 3 or not tokens[2].isdigit()):
            return "用法：#取消日程高亮 YYYY-MM-DD 序号。"
        return ToolCall("asoul_highlight", {"action": action, **({"date": tokens[1]} if len(tokens) > 1 else {}), "index": int(tokens[2]) if len(tokens) > 2 and tokens[2].isdigit() else 1, "style": tokens[3] if len(tokens) > 3 else "粉色"})
    if head == "bili_test_atall":
        return ToolCall("bili_test", {"kind": "atall"})
    if head.startswith("bili_"):
        if not rest:
            return "请提供 B站目标 UID。"
        return ToolCall("bili_test", {"kind": head.removeprefix("bili_test_").removeprefix("bili_"), "uid": rest})
    if head == "整点报时":
        return _system(["准时报点", *tokens[1:]])
    if head == "全局通告":
        at_all = bool(re.search(r"(?:^|\s)@(?:全体|all)(?:\s|$)", rest, re.I)) or any(part.get("type") == "at" and part.get("data", {}).get("qq") == "all" for part in event.segments)
        body = re.sub(r"@(?:全体|all)\s*", "", rest, flags=re.I).strip()
        parts = body.split(maxsplit=1)
        member = parts[0] if parts and parts[0] in {"嘉然", "乃琳", "贝拉", "心宜", "思诺"} else None
        if member:
            body = parts[1] if len(parts) > 1 else ""
        return ToolCall("global_announcement", {"text": body, "at_all": at_all, "member": member})
    if head in {"全局图片公告", "图片公告"}:
        return ToolCall("global_announcement", {"raw_image": True, "at_all": bool(re.search(r"@(?:全体|all)", rest, re.I))})
    if head in {"总游戏开", "游戏总开", "总游戏关", "游戏总关", "总游戏状态", "游戏总状态"}:
        return ToolCall("system_settings", {"key": "mini_games_enabled", "action": "status" if "状态" in head else "set", "value": head.endswith("开")})
    if head in {"游戏开", "游戏关", "游戏状态"}:
        return ToolCall("group_feature_status", {}) if head.endswith("状态") else ToolCall("group_settings", {"action": "set", "feature": "mini_games", "enabled": head.endswith("开")})
    if head in {"游戏禁言开", "游戏禁言关", "游戏禁言状态"}:
        return ToolCall("system_settings", {"key": f"game_mute:{event.group_id}", "action": "status" if "状态" in head else "set", "value": head.endswith("开")})
    if head == "系统设置":
        return _system(tokens[1:])
    if head in {"主动过滤", "被动过滤", "移除主动过滤", "移除被动过滤", "主动过滤列表", "被动过滤列表"}:
        return ToolCall("system_settings", {"key": "active_filter" if "主动" in head else "passive_filter", "action": "remove" if head.startswith("移除") else "status" if "列表" in head or rest == "列表" else "add", "user_ids": [int(value) for value in re.split(r"[,，\s]+", rest) if value.isdigit()]})
    if game_prefix(text):
        return ToolCall("external_game", {"text": text})
    if head in {"记忆", "遗忘", "恢复记忆"}:
        action = {"遗忘": "forget", "恢复记忆": "restore"}.get(head, "list")
        query = rest
        if head == "记忆":
            operation = tokens[1] if len(tokens) > 1 else "列表"
            action = {"查看": "list", "列表": "list", "记住": "remember", "添加": "remember", "更正": "correct", "遗忘": "forget", "恢复": "restore"}.get(operation, "list")
            query = " ".join(tokens[2:])
        return ToolCall("memory_manage", {"action": action, "query": query})
    return None


def _system(tokens: list[str]) -> ToolCall | str:
    if not tokens:
        return ToolCall("system_settings", {})
    if tokens[0] == "集群":
        action = {"列表": "status", "创建": "create", "邀请": "invite", "移除": "remove", "解散": "dissolve"}.get(tokens[1] if len(tokens) > 1 else "列表", "status")
        args = {"key": "cluster", "action": action}
        if action == "create" and len(tokens) > 2:
            args["name"] = " ".join(tokens[2:])
        elif action == "invite" and len(tokens) == 4 and all(value.isdigit() for value in tokens[2:]):
            args.update(domain_id=int(tokens[2]), group_id=int(tokens[3]))
        elif action in {"remove", "dissolve"} and len(tokens) == 3 and tokens[2].isdigit():
            args["group_id" if action == "remove" else "domain_id"] = int(tokens[2])
        elif action != "status":
            return "请提供完整的集群管理参数。"
        return ToolCall("system_settings", args)
    if tokens[0] in {"被呼叫会话", "糖糖主动聊天", "人格后台整理", "语音"}:
        return ToolCall("chat_settings", {"text": "系统设置 " + " ".join(tokens)})
    if tokens[0] == "被动互动":
        if len(tokens) < 3 or not tokens[1].isdigit() or int(tokens[1]) <= 0:
            return "请提供完整的被动互动参数：被动互动 <群号> 状态，或 <群号> <参数> <值>。"
        if tokens[2] == "状态" and len(tokens) == 3:
            return ToolCall("system_settings", {"key": "passive:" + tokens[1], "action": "configure", "parameter": "状态", "value": None})
        if len(tokens) != 4:
            return "请提供完整的被动互动参数：被动互动 <群号> <参数> <值>。"
        return ToolCall("system_settings", {"key": "passive:" + tokens[1], "action": "configure", "parameter": tokens[2], "value": tokens[3]})
    keys = {"小游戏": "mini_games_enabled", "游戏接口": "game_api_enabled", "nte": "game_api_enabled", "鸣潮": "game_api_enabled", "ww": "game_api_enabled", "准时报点": "hourly_enabled", "整点报时": "hourly_enabled"}
    key = keys.get(tokens[0].casefold())
    if key is None:
        return "系统设置可用：集群、游戏接口、小游戏、被呼叫会话、糖糖主动聊天、人格后台整理、语音、被动互动、准时报点。"
    action = tokens[-1]
    if tokens[:2] == ["小游戏", "禁言"] and len(tokens) >= 3:
        key = "game_mute:" + tokens[2]
    if action in _SWITCH:
        return ToolCall("system_settings", {"key": key, "action": "set", "value": _SWITCH[action]})
    if len(tokens) == 4 and tokens[1] == "时段":
        return ToolCall("system_settings", {"key": "hourly_schedule", "action": "set", "value": tokens[2:]})
    if tokens[0] in {"准时报点", "整点报时"} and len(tokens) >= 3 and tokens[1] == "范围":
        action = {"列表": "range_status", "添加": "range_add", "移除": "range_remove"}.get(tokens[2], "range_status")
        return ToolCall("system_settings", {"key": "hourly_range", "action": action, **({"group_id": int(tokens[3])} if len(tokens) > 3 and tokens[3].isdigit() else {})})
    return ToolCall("system_settings", {"key": key, "action": "status"})


def _natural(text: str, event: InboundEvent) -> ToolCall | str | None:
    clean = re.sub(r"^(?:先|请|帮我|给我|看看|看下|查下|查一下|查查|我想|我要|来个|来张|来一张|发一张|发个|发一下|看一下)\s*", "", text.strip())
    if re.search(r"(?:不要|不用|别|禁止|不需要|先别|暂时不|不)(?:再)?(?:查|看|发|抽|玩|调用|执行)", text):
        return None
    search_text = re.sub(r"^(?:(?:先|请|帮我|给我)\s*)+", "", text.strip())
    # A separated preposition needs its own following boundary; otherwise
    # the keyword's first character belongs to the keyword.
    group_search = re.fullmatch(
        r"(?:搜索|搜一下|搜搜|搜|查找|找一下)\s*(?:(?:当前|本)?群聊(?:内容|记录)?|(?:当前|本)?群(?:聊天|发言|历史)记录)(?:[中里内的]|\s+[中里内的](?=\s|[:：]))?\s*[:：]?\s*(.*?)", search_text
    ) or re.fullmatch(
        r"(?:找一下|找找|查找|搜索|搜一下|搜搜|搜)\s*(?:群里|群内|本群|当前群)(?:的|\s+的(?=\s|[:：]))?\s*[:：]?\s*(.*?)", search_text
    )
    if group_search:
        keyword = group_search[1].strip(" ,，。?？!！ ")
        return ToolCall("group_chat_search", {"keyword": keyword}) if keyword else "请提供要搜索的群聊关键词。"
    if re.match(r"^(?:请)?记住(?:我|我的)", text):
        query = re.sub(r"^(?:请)?记住", "", text)
        return ToolCall("memory_manage", {"action": "remember", "query": query})
    ordinal = re.fullmatch(r"第([一二三四五六七八九十]|\d+)名(?:是|叫)?谁[?？。]*", clean)
    if ordinal:
        rank = int(ordinal[1]) if ordinal[1].isdigit() else "一二三四五六七八九十".index(ordinal[1]) + 1
        return ToolCall("tool_followup", {"text": clean, "rank": rank})
    if re.fullmatch(r"(?:来|发|给我)(?:个|张)?(?:[^，。；;]{0,16})?表情(?:包)?(?:\s*[^，。；;]{0,16})?", text.strip()):
        return ToolCall("expression_send", {"selector": text.strip()})
    if re.search(r"(?:发言|说话)(?:排行|榜|统计)|(?:今天|本周|本月)(?:的)?(?:排行|榜)", clean):
        return ToolCall("ranking", {"scope": _period(clean), "cluster": "集群" in clean})
    if re.search(r"(?:今日|今天|明日|明天|本周).*(?:直播|日程)|直播日程", clean):
        view = "tomorrow" if re.search("明日|明天", clean) else "week" if "本周" in clean else "today"
        return ToolCall("asoul_schedule", {"view": view})
    if re.search(r"(?:本群|群里|群).*(?:缘分|老婆)|群缘分", clean):
        return ToolCall("wife_group", {})
    if re.search(r"(?:我|我的).*(?:缘分|老婆)|今日缘分", clean):
        return ToolCall("wife_draw" if re.search(r"抽|领取|领个", text) else "wife_personal", {})
    if re.search(r"(?:你的|达妮娅|娅娅)?.*(?:美图|自拍|照片)|好看的图", clean):
        return ToolCall("denia_gallery", {})
    if re.search(r"(?:本地知识|知识库|百科|梗库)(?:查询|搜索|查找)?", clean):
        query = re.sub(r".*?(?:本地知识|知识库|百科|梗库)(?:查询|搜索|查找)?[:：\s]*", "", clean)
        return ToolCall("knowledge_search", {"query": query or text})
    if re.fullmatch(r"(?:来|玩|开始|开一局|开个|投个|扔个|掷个)?\s*(?:幸运)?骰子(?:游戏|吧|一下)?", clean):
        return ToolCall("dice_start", {})
    if re.fullmatch(r"(?:玩|开始|开一局|开个)?\s*(?:俄罗斯)?转盘(?:游戏|吧|一下)?", clean):
        return ToolCall("roulette_load", {})
    if re.fullmatch(r"(?:玩|开始|开一局|开个)?\s*猜数字(?:游戏|吧|一下)?", clean):
        return ToolCall("guess_start", {})
    if re.fullmatch(r"(?:我)?猜\s*(\d{1,3})", clean):
        return ToolCall("guess_submit", {"value": int(re.search(r"\d+", clean)[0])})
    if re.search(r"发言(?:记录|档案|画像)|搜索.*发言", clean):
        return ToolCall("archive_profile" if "画像" in clean else "archive_records", {"target_user_id": _target(event) or event.user_id})
    if re.search(r"本群.*(?:功能|开关|设置)|群设置", clean):
        return ToolCall("group_feature_status", {})
    return None


def route(text: str, event: InboundEvent) -> RouteDecision:
    original = text.strip()
    # Upstream owns the entire command, including words also used by local
    # combination syntax. Do not split game arguments or normalise their text.
    if game_prefix(original):
        return RouteDecision("tool", [ToolCall("external_game", {"text": original})])
    cleaned = re.sub(r"^(?:糖糖|达妮娅|娅娅|娅宝|小娅)[,，、：:\s]*", "", original).strip()
    chunks = [chunk.strip(" ,，。；; ") for chunk in _SPLIT.split(cleaned) if chunk.strip(" ,，。；; ")]
    tools, analysis = [], []
    unresolved = []
    for chunk in chunks or [cleaned]:
        # Four-character idiom throws retain their command identity before '#'.
        command = chunk.startswith("#") or bool(re.match(r"^[\u4e00-\u9fff]{4}\s*#\s*丢给", chunk))
        result = _command(chunk, event) if command else _natural(chunk, event)
        if isinstance(result, ToolCall):
            tools.append(result)
        elif isinstance(result, str):
            return RouteDecision("clarification", reason=result, chat_text=original)
        elif tools and _ANALYSIS.match(chunk):
            analysis.append(chunk)
        else:
            unresolved.append(chunk)
    if unresolved and tools:
        return RouteDecision("clarification", reason="组合请求中还有无法完整识别的步骤，请说明要执行的具体本地功能。", chat_text=original)
    if not tools:
        return RouteDecision("chat", chat_text=original)
    return RouteDecision("mixed" if analysis else "tool", tools, "；".join(analysis))


class Router:
    def route(self, event: InboundEvent) -> RouteDecision:
        return route(event.text, event)
