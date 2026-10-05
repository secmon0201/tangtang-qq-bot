"""Deterministic business tools. No matcher, model client, or legacy process.

The chat runtime sends ToolResult.messages, while its small data facts are kept
separately from the stable model prefix. Scheduled outputs are acknowledged only
after the transport has confirmed delivery.
"""
from __future__ import annotations

import asyncio
import base64
from dataclasses import asdict
from datetime import date, datetime, time as clock_time, timedelta
import hashlib
import json
from pathlib import Path
import random
import re
import time
from typing import Any
from urllib.parse import unquote, urlencode, urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx

from .types import InboundEvent, ToolCall, ToolResult
from .business_pages import BusinessPages
from .corrections import CATEGORIES, SEVERITIES, SkillCorrectionLedger
from .core_protocol import game_prefix
from .help_content import (admin_help_page_text, build_forward_nodes,
                           super_admin_help_pages, user_help_categories, user_help_text)
from .business import config as business_config
from .business.a_coast_archive import ACoastArchiveService
from .business.a_coast_archive_render import ACoastArchiveImageRenderer
from .business.asoul import ASoulService
from .business.asoul_render import ASoulImageRenderer
from .business.asoul_web_render import ASoulWebRenderer, schedule_payload
from .business.avatars import AvatarService
from .business.community_web import CommunityWebRenderer
from .business.db import Database, utc_now
from .business.denia_gallery import DeniaGallery
from .business.duplicate import DuplicateService
from .business.group_domains import FEATURE_SPECS, GroupDomainService
from .business.hourly_copy import HourlyCopyCatalog
from .business.knowledge_db import KnowledgeDb
from .business.knowledge_search import query_tokens, resolve_related
from .business.knowledge_review import export_pending_doc, export_rejected_doc, process_review_files
from .business.mini_games import MiniGameService, GameEvent, RANDOM_EVENT_TYPES
from .business.mini_game_reports import MiniGameReportRenderer
from .business.passive_settings import PassiveSettingsStore
from .business.qq_platform import QQPlatform
from .business.reports import ReportRenderer
from .business.stats import StatsService
from .business.tangtang_db import TangtangDb
from .business.today_wife import TodayWifeService
from .business.today_wife_game import TodayWifeGameService
from .business.triple_repeat import TripleRepeatTracker
from .business.zhijiang_live_guard import DEFAULT_SCHEDULE_URL, ZhijiangLiveGuard


TOOL_DESCRIPTIONS = {
    "user_help": "本地功能帮助", "robot_status": "机器人运行状态", "ranking": "本群或当前群域日周月总发言榜",
    "archive_records": "本群发言档案分页", "archive_search": "本群发言关键词搜索", "archive_profile": "本地统计画像和雷达图",
    "group_chat_search": "搜索当前群已存档聊天内容，返回时间、昵称和原文节选",
    "mini_game_help": "小游戏菜单和完整玩法图", "mini_game_roulette": "转盘榜", "mini_game_bomb": "炸弹榜",
    "mini_game_dice": "骰子榜", "mini_game_guess": "猜数榜", "roulette_load": "装填转盘", "roulette_fire": "开枪",
    "bomb_load": "装填定时炸弹", "bomb_pass": "向真实@成员传递炸弹", "idiom_bomb_load": "开始专业或娱乐成语炸弹",
    "idiom_bomb_pass": "用四字词向真实@成员传递", "dice_start": "幸运骰局投掷", "guess_start": "开始猜数字",
    "guess_submit": "提交数字", "mini_game_clear": "清理本群小游戏记录，需确认", "wife_draw": "抽取今日缘分",
    "wife_take": "定向今日缘分", "wife_divorce": "解缘", "wife_personal": "个人缘分及永久历史", "wife_group": "群缘分及历史",
    "wife_clear": "清理本群缘分，需确认", "knowledge_search": "本地已审核知识检索", "knowledge_review": "知识词条审核",
    "denia_gallery": "抽取本地达妮娅图片", "duplicate_scan": "跨群成员查重", "whitelist": "查重白名单",
    "group_feature_status": "本群功能状态", "group_settings": "本群开关、过滤和代称", "system_settings": "机器人总控和集群管理",
    "global_announcement": "群目标文字或图片公告", "asoul_help": "A-SOUL功能帮助", "asoul_schedule": "今日明日本周直播",
    "today_live": "今日直播", "tomorrow_live": "明日直播", "week_live": "本周直播", "asoul_highlight": "日程高亮管理",
    "zhijiang_schedule": "枝江日程缓存", "zhijiang_status": "直播防护状态", "zhijiang_refresh": "刷新直播防护",
    "bili_status": "B站订阅状态", "bili_login": "B站二维码人工登录", "bili_logout": "清除B站登录态",
    "bili_test": "B站视频、直播和热门评论诊断", "persona_status": "当前人格状态", "persona_impression": "本群本人印象",
    "qq_platform_health": "QQ平台自检", "qq_transport_status": "新系统QQ传输状态及断线记录", "skill_admin": "本地工具状态和用量", "model_settings": "模型档案管理",
    "proactive_settings": "主动回复策略", "operator_web": "查重和公告面板入口",
    "chat_settings": "聊天模型、开关和主动策略", "memory_manage": "记忆查看、更正、遗忘和恢复",
    "growth_manage": "人格公共成长管理", "profile_generate": "AI发言画像生成",
    "expression_send": "发送本地角色表情", "tool_followup": "查询本会话最近的本地工具结果",
}
# Console-facing names are deliberately kept beside the canonical action names.
# The action ids remain stable for routing, persistence and API compatibility;
# operators should not have to decode those ids in the local console.
TOOL_LABELS = {
    "user_help": "功能帮助", "robot_status": "机器人状态", "ranking": "发言排行",
    "archive_records": "发言记录", "archive_search": "发言搜索", "archive_profile": "发言画像",
    "group_chat_search": "搜索本群聊天内容",
    "mini_game_help": "小游戏帮助", "mini_game_roulette": "转盘排行", "mini_game_bomb": "炸弹排行",
    "mini_game_dice": "骰子排行", "mini_game_guess": "猜数排行", "roulette_load": "装填转盘", "roulette_fire": "转盘开枪",
    "bomb_load": "装填定时炸弹", "bomb_pass": "传递炸弹", "idiom_bomb_load": "装填成语炸弹",
    "idiom_bomb_pass": "传递成语炸弹", "dice_start": "开始幸运骰局", "guess_start": "开始猜数字",
    "guess_submit": "提交猜数", "mini_game_clear": "清理小游戏记录", "wife_draw": "抽取今日缘分",
    "wife_take": "领取指定缘分", "wife_divorce": "解除缘分", "wife_personal": "个人缘分",
    "wife_group": "群缘分", "wife_clear": "清理群缘分", "knowledge_search": "本地知识检索",
    "knowledge_review": "知识词条审核", "denia_gallery": "达妮娅图库", "duplicate_scan": "跨群成员查重",
    "whitelist": "查重白名单", "group_feature_status": "本群功能状态", "group_settings": "本群设置",
    "system_settings": "系统设置与集群管理", "global_announcement": "群公告", "asoul_help": "A-SOUL 帮助",
    "asoul_schedule": "直播日程", "today_live": "今日直播", "tomorrow_live": "明日直播", "week_live": "本周直播",
    "asoul_highlight": "日程高亮", "zhijiang_schedule": "枝江日程", "zhijiang_status": "直播防护状态",
    "zhijiang_refresh": "刷新直播防护", "bili_status": "B站订阅状态", "bili_login": "B站登录",
    "bili_logout": "退出B站登录", "bili_test": "B站诊断", "persona_status": "人格状态",
    "persona_impression": "个人印象", "qq_platform_health": "QQ平台自检", "qq_transport_status": "QQ传输状态",
    "skill_admin": "本地工具管理", "model_settings": "模型设置", "proactive_settings": "主动回复设置",
    "operator_web": "运营面板", "chat_settings": "聊天设置", "memory_manage": "记忆管理",
    "growth_manage": "公共成长管理", "profile_generate": "生成发言画像", "expression_send": "发送角色表情",
    "tool_followup": "查询工具结果",
}
# Console-facing argument metadata.  Action IDs remain stable while this
# schema lets the local UI expose useful controls without guessing JSON.
_TARGET_USER_ARGUMENT = {"label": "目标成员 QQ（可选）", "type": "number", "min": 1,
    "description": "控制台操作者可填写本群目标 QQ；留空查询本人。普通成员查询他人仍遵循真实 @ 或明确数字指令规则。"}
_CLUSTER_ARGUMENT = {"label": "榜单范围", "type": "select", "options": [
    {"value": False, "label": "当前群"}, {"value": True, "label": "当前集群"}], "default": False}
_HELP_ARGUMENTS = {
    "audience": {"label": "帮助对象", "type": "select", "default": "user", "options": [
        {"value": "user", "label": "普通用户"}, {"value": "admin", "label": "超级管理员"}]},
    "format": {"label": "普通帮助格式", "type": "select", "default": "image", "options": [
        {"value": "image", "label": "图卡"}, {"value": "text", "label": "可复制文字"}],
        "when": {"audience": ["user"]}},
}
_CLEAR_ARGUMENTS = {
    "action": {"label": "清理操作", "type": "select", "default": "", "options": [
        {"value": "", "label": "发起或确认清理"}, {"value": "cancel", "label": "取消清理"}]},
    "confirm": {"label": "确认已发起的清理", "type": "checkbox", "default": False,
        "when": {"action": ["", None]}, "description": "先不勾选执行以发起清理，再在 60 秒内勾选确认并执行；仅清理所选当前群。"},
}
_GROUP_ARGUMENTS = {
    "action": {"label": "群设置操作", "type": "select", "default": "status", "options": [
        {"value": "status", "label": "查看状态"}, {"value": "alias", "label": "设置排行缩写"},
        {"value": "set", "label": "设置功能开关"}, {"value": "toggle", "label": "反转功能开关"},
        {"value": "filter_status", "label": "查看群内过滤名单"}, {"value": "filter_add", "label": "添加过滤成员"},
        {"value": "filter_remove", "label": "移除过滤成员"}]},
    "value": {"label": "发言榜缩写", "type": "text", "required": True, "when": {"action": ["alias"]},
        "description": "1–20 个字符，只用于发言榜；控制台保留完整群名。"},
    "feature": {"label": "群功能", "type": "select", "required": True, "when": {"action": ["set", "toggle"]},
        "options": [{"value": item.key, "label": item.label} for item in FEATURE_SPECS]},
    "enabled": {"label": "功能开关", "type": "select", "required": True, "when": {"action": ["set"]},
        "options": [{"value": True, "label": "开启"}, {"value": False, "label": "关闭"}]},
    "user_id": {"label": "成员 QQ", "type": "number", "min": 1, "required": True,
        "when": {"action": ["filter_add", "filter_remove"]}},
}
TOOL_ARGUMENTS = {
    "user_help": _HELP_ARGUMENTS,
    "asoul_help": _HELP_ARGUMENTS,
    "ranking": {
        "scope": {"label": "统计范围", "type": "select", "options": [
            {"value": "day", "label": "今日"}, {"value": "week", "label": "本周"},
            {"value": "month", "label": "本月"}, {"value": "total", "label": "累计"},
        ], "default": "day"},
        "cluster": {"label": "统计域", "type": "select", "options": [
            {"value": False, "label": "当前群"}, {"value": True, "label": "当前集群"},
        ], "default": False},
        "cluster_name": {"label": "集群名称（可选）", "type": "text", "placeholder": "例如：A海岸"},
    },
    "archive_records": {
        "target_user_id": _TARGET_USER_ARGUMENT,
        "page": {"label": "页码", "type": "number", "min": 1, "default": 1},
    },
    "archive_search": {
        "target_user_id": _TARGET_USER_ARGUMENT,
        "keyword": {"label": "搜索词", "type": "text", "required": True},
        "page": {"label": "页码", "type": "number", "min": 1, "default": 1},
    },
    "group_chat_search": {
        "keyword": {"label": "群聊关键词", "type": "text", "required": True,
            "description": "按原文搜索当前群已存档消息，每页 10 条，最近优先；最多 100 字。"},
        "page": {"label": "页码", "type": "number", "min": 1, "default": 1},
    },
    "duplicate_scan": {
        "group_ids": {"label": "比较群", "type": "group-multi", "required": False},
        "mode": {"label": "查重范围", "type": "select", "options": [
            {"value": "source", "label": "与来源群比较"}, {"value": "all", "label": "全部群两两比较"},
        ], "default": "source"},
        "ignore_whitelist": {"label": "忽略白名单", "type": "checkbox", "default": False},
    },
    "archive_profile": {"target_user_id": _TARGET_USER_ARGUMENT},
    "mini_game_roulette": {"cluster": _CLUSTER_ARGUMENT},
    "mini_game_bomb": {"cluster": _CLUSTER_ARGUMENT},
    "mini_game_dice": {"cluster": _CLUSTER_ARGUMENT},
    "mini_game_guess": {"cluster": _CLUSTER_ARGUMENT},
    "idiom_bomb_load": {
        "mode": {"label": "成语玩法", "type": "select", "default": "professional", "options": [
            {"value": "professional", "label": "专业模式"}, {"value": "entertainment", "label": "娱乐模式"}]},
        "duration": {"label": "总倒计时（秒）", "type": "number", "min": 60, "max": 600, "default": 120},
    },
    "bomb_pass": {"target_user_id": {"label": "接收成员 QQ", "type": "number", "min": 1, "required": True,
        "description": "必须有本轮消息中真实 @ 的另一位当前群成员；控制台填写 QQ 不会伪造 @。"}},
    "idiom_bomb_pass": {
        "target_user_id": {"label": "接收成员 QQ", "type": "number", "min": 1, "required": True,
            "description": "必须有本轮消息中真实 @ 的另一位当前群成员。"},
        "idiom": {"label": "接龙四字词", "type": "text", "required": True},
    },
    "guess_submit": {"value": {"label": "猜测数字", "type": "number", "min": 0, "max": 999, "required": True}},
    "mini_game_clear": _CLEAR_ARGUMENTS,
    "wife_clear": _CLEAR_ARGUMENTS,
    "wife_take": {"target_user_id": {"label": "指定缘分成员 QQ", "type": "number", "min": 1, "required": True,
        "description": "必须是本轮消息中真实 @ 的另一位当前群成员；填写 QQ 不会绕过缘分规则。"}},
    "wife_personal": {"page": {"label": "记录页码", "type": "number", "min": 1, "default": 1,
        "description": "第 1 页查看当前个人缘分；后续页查看永久历史。"}},
    "wife_group": {
        "history": {"label": "查看群缘分历史", "type": "checkbox", "default": False},
        "date": {"label": "故事日期（可选）", "type": "text", "placeholder": "YYYY-MM-DD", "when": {"history": [False]},
            "description": "留空查看今日；历史汇总模式不使用日期。"},
    },
    "knowledge_search": {
        "query": {"label": "检索内容（可选）", "type": "text", "description": "留空使用上方触发文本；无关键词时按已有词条顺序读取。"},
        "domain": {"label": "知识领域", "type": "select", "default": "", "options": [
            {"value": "", "label": "全部已审核知识"}, {"value": "zhijiang", "label": "枝江"}, {"value": "mingchao", "label": "鸣潮"}]},
        "limit": {"label": "最多返回条数", "type": "number", "min": 1, "default": 3},
    },
    "knowledge_review": {
        "action": {"label": "审核操作", "type": "select", "default": "pending", "options": [
            {"value": "pending", "label": "查看待审核"}, {"value": "approve", "label": "批准词条"},
            {"value": "reject", "label": "拒绝词条"}, {"value": "revive", "label": "恢复为待审"}]},
        "entry_id": {"label": "词条编号", "type": "number", "min": 1, "required": True,
            "when": {"action": ["approve", "reject", "revive"]}},
    },
    "whitelist": {
        "action": {"label": "白名单操作", "type": "select", "default": "list", "options": [
            {"value": "list", "label": "查看白名单"}, {"value": "add", "label": "加入白名单"}, {"value": "remove", "label": "移出白名单"}]},
        "user_id": {"label": "成员 QQ", "type": "number", "min": 1, "required": True, "when": {"action": ["add", "remove"]}},
        "note": {"label": "白名单备注（可选）", "type": "text", "when": {"action": ["add"]}},
    },
    "group_settings": _GROUP_ARGUMENTS,
    "group_feature_status": _GROUP_ARGUMENTS,
    "system_settings": {
        "key": {"label": "设置项目", "type": "group-key", "default": "", "options": [
            {"value": "", "label": "管理帮助"}, {"value": "cluster", "label": "集群管理"},
            {"value": "active_filter", "label": "全局主动过滤名单"}, {"value": "passive_filter", "label": "全局被动过滤名单"},
            {"value": "mini_games_enabled", "label": "小游戏总开关"}, {"value": "game_api_enabled", "label": "游戏接口总开关"},
            {"value": "stats_realtime_enabled", "label": "实时发言统计"}, {"value": "hourly_enabled", "label": "整点报时总开关"},
            {"value": "hourly_schedule", "label": "整点报时时段"}, {"value": "hourly_range", "label": "整点报时群范围"}],
            "group_options": [{"prefix": "passive:", "label": "群被动互动"}, {"prefix": "game_mute:", "label": "群小游戏处罚"}],
            "description": "系统管理使用操作者私聊；群设置选项显示完整群名。"},
        "action": {"label": "操作", "type": "select", "default": "status", "options": [
            {"value": "status", "label": "查看状态"},
            {"value": "set", "label": "保存设置", "when": {"key": ["mini_games_enabled", "game_api_enabled", "stats_realtime_enabled", "hourly_enabled", "hourly_schedule", {"prefix": "game_mute:"}]}},
            {"value": "create", "label": "创建集群", "when": {"key": ["cluster"]}},
            {"value": "invite", "label": "将群加入集群", "when": {"key": ["cluster"]}},
            {"value": "remove", "label": "移除群或名单成员", "when": {"key": ["cluster", "active_filter", "passive_filter"]}},
            {"value": "dissolve", "label": "解散集群", "when": {"key": ["cluster"]}},
            {"value": "add", "label": "添加过滤成员", "when": {"key": ["active_filter", "passive_filter"]}},
            {"value": "configure", "label": "设置被动互动参数", "when": {"key": {"prefix": "passive:"}}},
            {"value": "range_status", "label": "查看报时群范围", "when": {"key": ["hourly_range"]}},
            {"value": "range_add", "label": "添加报时群", "when": {"key": ["hourly_range"]}},
            {"value": "range_remove", "label": "移除报时群", "when": {"key": ["hourly_range"]}}]},
        "name": {"label": "集群名称", "type": "text", "required": True, "when": {"key": ["cluster"], "action": ["create"]}},
        "alias": {"label": "集群缩写（可选）", "type": "text", "when": {"key": ["cluster"], "action": ["create"]}},
        "domain_id": {"label": "集群编号", "type": "number", "min": 1, "required": True,
            "when": {"key": ["cluster"], "action": ["invite", "dissolve"]}, "description": "查看集群状态取得编号，或使用“群与集群”页面直接选择。"},
        "group_id": {"label": "目标群号", "type": "number", "min": 1, "required": True,
            "when": {"action": ["invite", "remove", "range_add", "range_remove"], "key": ["cluster", "hourly_range"]}},
        "user_ids": {"label": "过滤成员 QQ", "type": "user-multi", "required": True,
            "when": {"key": ["active_filter", "passive_filter"], "action": ["add", "remove"]}, "description": "每行一个 QQ，也可使用逗号分隔。"},
        "parameter": {"label": "被动互动参数", "type": "select", "default": "状态",
            "when": {"key": {"prefix": "passive:"}, "action": ["configure"]}, "options": [
                {"value": "状态", "label": "只查看当前配置"}, {"value": "表情", "label": "随机表情开关"},
                {"value": "复读", "label": "随机复读开关"}, {"value": "三连", "label": "三连复读开关"},
                {"value": "表情概率", "label": "随机表情概率"}, {"value": "复读概率", "label": "随机复读概率"},
                {"value": "三连概率", "label": "三连复读概率"}, {"value": "表情冷却", "label": "表情冷却（秒）"},
                {"value": "复读冷却", "label": "复读冷却（分钟）"}, {"value": "复读间隔", "label": "复读间隔（消息条数）"}]},
        "value": {"label": "设定值", "type": "variant", "variants": [
            {"when": {"key": ["hourly_schedule"], "action": ["set"]}, "type": "time-range", "label": "每日报时时段",
                "required": True, "description": "分别填写开始和结束时间，格式 HH:MM。"},
            {"when": {"key": {"prefix": "passive:"}, "action": ["configure"], "parameter": ["表情", "复读", "三连"]},
                "type": "select", "label": "被动互动开关", "options": [
                    {"value": "", "label": "保持现状，只查看"}, {"value": "开", "label": "开启"}, {"value": "关", "label": "关闭"}]},
            {"when": {"key": {"prefix": "passive:"}, "action": ["configure"], "parameter": ["表情概率", "复读概率", "三连概率"]},
                "type": "text", "label": "概率（可选）", "placeholder": "例如：2% 或 0.02", "description": "范围 0–100%；留空只查看当前值。"},
            {"when": {"key": {"prefix": "passive:"}, "action": ["configure"], "parameter": ["表情冷却", "复读冷却", "复读间隔"]},
                "type": "number", "min": 0, "label": "冷却或间隔（可选）", "description": "表情冷却单位秒、复读冷却单位分钟、复读间隔单位条；留空只查看。"},
            {"when": {"key": ["mini_games_enabled", "game_api_enabled", "stats_realtime_enabled", "hourly_enabled", {"prefix": "game_mute:"}], "action": ["set"]},
                "type": "select", "label": "设定开关", "required": True, "options": [
                    {"value": True, "label": "开启"}, {"value": False, "label": "关闭"}]},
        ]},
    },
    "global_announcement": {
        "group_ids": {"label": "公告目标群", "type": "group-multi", "description": "未选择时使用已配置的默认公告集群。"},
        "text": {"label": "公告正文", "type": "textarea", "description": "最多 1000 字；纯文字公告需要正文。"},
        "at_all": {"label": "提醒全体成员", "type": "checkbox", "default": False},
        "image_path": {"label": "已上传图片路径（可选）", "type": "text", "description": "只接受 Harness 目录内的现有图片；图文预览也可在公告页面上传。"},
        "raw_image": {"label": "使用触发消息或引用中的原图", "type": "checkbox", "default": False,
            "description": "需要真实消息附件；控制台无附件时请使用已上传图片路径。"},
        "member": {"label": "配图角色", "type": "select", "default": "__random__", "options": [
            {"value": "__random__", "label": "随机角色"}, {"value": "__none__", "label": "不添加角色表情"},
            *[{"value": value, "label": value} for value in ("贝拉", "嘉然", "乃琳", "思诺", "心宜")]]},
        "sticker": {"label": "指定表情名称（可选）", "type": "text", "description": "填写所选角色素材名称；留空随机选择。"},
        "graphic": {"label": "生成图文海报", "type": "checkbox", "default": False},
        "title": {"label": "海报标题", "type": "text", "required": True, "when": {"graphic": [True]}},
        "extra_text": {"label": "图片后附文（可选）", "type": "textarea"},
    },
    "asoul_highlight": {
        "action": {"label": "高亮操作", "type": "select", "default": "list", "options": [
            {"value": "list", "label": "查看高亮记录"}, {"value": "list_day", "label": "查看指定日期日程"},
            {"value": "set", "label": "设置日程高亮"}, {"value": "remove", "label": "移除日程高亮"},
            {"value": "remove_record", "label": "按记录序号移除高亮"}]},
        "date": {"label": "日程日期", "type": "text", "placeholder": "YYYY-MM-DD", "required": True,
            "when": {"action": ["list_day", "set", "remove"]}},
        "index": {"label": "日程或记录序号", "type": "number", "min": 1, "default": 1,
            "when": {"action": ["set", "remove", "remove_record"]}, "description": "先查看对应列表，再按列表中的序号操作。"},
        "style": {"label": "高亮颜色", "type": "select", "default": "粉色", "when": {"action": ["set"]},
            "options": [{"value": value, "label": value} for value in ("粉色", "红色", "白金色")]},
    },
    "bili_test": {
        "kind": {"label": "诊断项目", "type": "select", "default": "live", "options": [
            {"value": "live", "label": "直播信息"}, {"value": "video", "label": "视频信息"},
            {"value": "comment", "label": "热门评论"}, {"value": "all", "label": "综合诊断"},
            {"value": "dump_live", "label": "导出直播调试数据"}, {"value": "atall", "label": "群全体提醒测试"}],
            "description": "普通诊断使用私聊；全体提醒需选择机器人为管理员的群。"},
        "uid": {"label": "B站用户 UID", "type": "text", "required": True,
            "when": {"kind": ["live", "video", "comment", "all", "dump_live"]},
            "description": "正整数；热门评论只允许已配置的评论目标。"},
    },
    "qq_transport_status": {"limit": {"label": "最近断线记录条数", "type": "number", "min": 1, "default": 20}},
    "skill_admin": {"text": {"label": "工具管理指令", "type": "textarea", "default": "状态",
        "description": "可填：状态；开关 开|关 <工具ID> [群号...]；纠错 列表|类别|解决 <编号>；数据 导出|清理 <QQ号>。工具 ID 可在卡片详情查看。"}},
    "model_settings": {"active_model": {"label": "模型档案名或编号（可选）", "type": "text",
        "description": "填写已登记的档案名或编号后切换模型；留空查看当前模型。"}},
    "chat_settings": {"text": {"label": "聊天设置指令", "type": "textarea", "default": "系统设置",
        "description": "可填：系统设置 被呼叫会话|人格后台整理|糖糖主动聊天|语音 状态|开|关；系统设置 模型 <档案名>；系统设置 主动回复 概率 2%|冷却 15|间隔 20。冷却单位分钟。"}},
    "proactive_settings": {
        "proactive_enabled": {"label": "主动聊天开关（可选）", "type": "select", "options": [
            {"value": "", "label": "保持现状"}, {"value": True, "label": "开启"}, {"value": False, "label": "关闭"}]},
        "strategy": {"label": "全局主动策略（可选）", "type": "select", "options": [
            {"value": "", "label": "保持现状"}, {"value": "legacy", "label": "旧规则"},
            {"value": "active_v1", "label": "活跃群"}, {"value": "low_traffic_v1", "label": "低流量群"}]},
        "text": {"label": "概率、冷却或群策略指令（可选）", "type": "textarea",
            "description": "可填：主动回复 概率 2%；主动回复 冷却 15（分钟）；主动回复 间隔 20（条）；主动回复 策略 活跃群 <群号或全部>。指令参数与直接控件同时填写时，指令先解析。"},
    },
    "operator_web": {"page": {"label": "打开业务页面", "type": "select", "default": "duplicate", "options": [
        {"value": "duplicate", "label": "跨群查重"}, {"value": "whitelist", "label": "白名单"},
        {"value": "announcement", "label": "公告预览"}, {"value": "ranking", "label": "发言排行"},
        {"value": "schedule", "label": "直播日程"}, {"value": "help", "label": "帮助"}]}},
    "memory_manage": {
        "action": {"label": "记忆操作", "type": "select", "default": "list", "options": [
            {"value": "list", "label": "查看本人记忆"}, {"value": "remember", "label": "记住本人原话"},
            {"value": "correct", "label": "更正本人记忆"}, {"value": "forget", "label": "遗忘本人记忆"},
            {"value": "restore", "label": "恢复本人记忆"}]},
        "query": {"label": "内容或目标（可选）", "type": "textarea", "when": {"action": ["remember", "correct", "forget", "restore"]},
            "description": "记住：填写触发文本中存在的本人原话；更正：编号 空格 新自述；遗忘/恢复：内容检索词。遗忘留空会先发起全部确认，不立即停用全部。"},
    },
    "growth_manage": {"text": {"label": "公共成长管理指令", "type": "textarea", "default": "列表",
        "description": "可填：列表；诊断；停用 <编号>；恢复 <编号>；回退 <编号> <版本>。全局范围前加“全局”；本群管理需群管理员，全局及诊断/回退需超级管理员。"}},
    "profile_generate": {
        "action": {"label": "画像操作", "type": "select", "default": "status", "options": [
            {"value": "status", "label": "查看已复核画像"}, {"value": "history", "label": "查看画像历史"},
            {"value": "generate", "label": "进入生成与复核队列"}], "description": "生成会进入独立模型队列，后续执行可能产生费用。"},
        "target_user_id": {**_TARGET_USER_ARGUMENT, "description": "留空处理本人；其他成员必须是本轮消息中唯一真实 @ 的目标。"},
    },
    "expression_send": {"selector": {"label": "表情名称或关键词（可选）", "type": "text", "description": "留空使用触发文本匹配已有本地表情。"}},
    "tool_followup": {"text": {"label": "追问内容（可选）", "type": "text", "placeholder": "例如：看看第一名",
        "description": "读取所选会话最近的本地工具结果；留空使用上方触发文本。"}},
}
for _schedule_tool, _schedule_default in (("asoul_schedule", "today"), ("today_live", "today"),
                                         ("tomorrow_live", "tomorrow"), ("week_live", "week"), ("zhijiang_schedule", "week")):
    TOOL_ARGUMENTS[_schedule_tool] = {"view": {"label": "日程范围", "type": "select", "default": _schedule_default,
        "options": [{"value": "today", "label": "今日"}, {"value": "tomorrow", "label": "明日"}, {"value": "week", "label": "本周"}]}}
ADMIN_TOOLS = {"robot_status", "duplicate_scan", "whitelist", "knowledge_review", "system_settings", "global_announcement",
               "asoul_highlight", "zhijiang_refresh", "bili_status", "bili_login", "bili_logout", "bili_test",
               "mini_game_clear", "wife_clear", "skill_admin", "model_settings", "proactive_settings", "operator_web", "qq_platform_health", "qq_transport_status"}
GROUP_FEATURES = {"ranking": "speech_ranking", "archive_records": "speech_archive", "archive_search": "speech_archive",
                  "group_chat_search": "speech_archive",
                  "archive_profile": "speech_archive", "asoul_schedule": "zhijiang_calendar", "today_live": "zhijiang_calendar",
                  "tomorrow_live": "zhijiang_calendar", "week_live": "zhijiang_calendar", "zhijiang_schedule": "zhijiang_calendar"}
GAME_ACTIONS = {"roulette_load", "roulette_fire", "bomb_load", "bomb_pass", "idiom_bomb_load", "idiom_bomb_pass",
                "dice_start", "guess_start", "guess_submit"}


def tool_catalog() -> list[dict[str, Any]]:
    return [{"name": name, "description": description,
             "label": TOOL_LABELS.get(name, name),
             "role": "super_admin" if name in ADMIN_TOOLS else "member",
             "token_cost": "background" if name == "profile_generate" else 0,
             "parameters": TOOL_ARGUMENTS.get(name, {})}
            for name, description in TOOL_DESCRIPTIONS.items()]


def _image(path: Path) -> dict[str, Any]:
    return {"type": "image", "data": {"file": path.resolve().as_uri()}}


def _text(text: str) -> dict[str, Any]:
    return {"type": "text", "data": {"text": text}}


def _plain(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


class ToolExecutor:
    def __init__(self, gateway: Any, root: Path, store: Any) -> None:
        self.gateway, self.root, self.store = gateway, Path(root), store
        runtime = self.root / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        self.resources = business_config.RESOURCE_DIR
        self.db = Database(runtime / "business.db")
        self.corrections = SkillCorrectionLedger(self.db)
        self.domains = GroupDomainService(self.db)
        self.history = TangtangDb(runtime / "business-history.db")
        self.archive = ACoastArchiveService(self.db, self.history)
        self.stats = StatsService(self.db, group_provider=self.domains.all_group_ids)
        self.games = MiniGameService(self.db)
        self.wife = TodayWifeService(self.db)
        self.wife_game = TodayWifeGameService(self.db)
        self.knowledge = KnowledgeDb(runtime / "knowledge.db")
        self.knowledge_review_dir = runtime / "knowledge"
        review = process_review_files(self.knowledge,
            pending_path=self.knowledge_review_dir / "收录待审.md",
            approve_path=self.knowledge_review_dir / "收录确认.md",
            reject_path=self.knowledge_review_dir / "收录拒绝.md")
        self._refresh_knowledge_docs()
        self.store.set_setting("knowledge_review_files", review)
        self.gallery = DeniaGallery(self.resources / "personas/denia/gallery", history_path=runtime / "gallery.db")
        self.platform = QQPlatform(gateway)
        self.duplicate = DuplicateService(self.db)
        self.asoul = ASoulService(self.db, debug_dir=runtime / "asoul_debug")
        self.font = self.resources / "asoul_stickers/font.ttf"
        self.report_dir = runtime / "reports"
        self.renderer = ReportRenderer(self.report_dir, self.font, command_prefix="#")
        self.community_renderer = CommunityWebRenderer(self.report_dir)
        self.game_renderer = MiniGameReportRenderer(self.report_dir, self.font, command_prefix="#")
        self.archive_renderer = ACoastArchiveImageRenderer(self.report_dir, self.font)
        self.asoul_renderer = ASoulImageRenderer(self.report_dir, self.font)
        self.asoul_web = ASoulWebRenderer(self.report_dir, self.asoul_renderer._remote_image,
                                         self.asoul_renderer.select_schedule_stickers)
        self.avatars = AvatarService(runtime / "avatars", "https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=100", timeout=5)
        self.hourly = HourlyCopyCatalog.load(self.resources / "zhijiang_hourly_copy.json",
                                             self.resources / "zhijiang_character_aliases.json")
        self.guard = ZhijiangLiveGuard(self.db, PassiveSettingsStore(store),
            enabled=bool(store.get_setting("live_guard_enabled", True)),
            source_url=str(store.get_setting("schedule_url", DEFAULT_SCHEDULE_URL)),
            timezone="Asia/Shanghai", refresh_minutes=int(store.get_setting("schedule_refresh_minutes", 5)),
            pause_minutes=int(store.get_setting("live_pause_minutes", 60)),
            lookahead_days=int(store.get_setting("schedule_lookahead_days", 7)),
            timeout_seconds=float(store.get_setting("schedule_timeout", 15)))
        self.repeat = TripleRepeatTracker()
        self._clear_requests: dict[tuple[str, int, int], float] = {}
        self._last_external: dict[str, float] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._qr_tasks: set[asyncio.Task] = set()
        self.web = BusinessPages(self)
        with self.db.connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS harness_tool_usage(id INTEGER PRIMARY KEY, event_id TEXT, user_id INTEGER, group_id INTEGER, name TEXT, status TEXT, elapsed_ms REAL, created_at TEXT)")
            conn.execute("CREATE TABLE IF NOT EXISTS harness_deliveries(delivery_key TEXT PRIMARY KEY, delivered INTEGER NOT NULL DEFAULT 0, created_at TEXT)")
            conn.execute("CREATE TABLE IF NOT EXISTS harness_pending_outputs(delivery_key TEXT PRIMARY KEY, group_id INTEGER NOT NULL, result_json TEXT NOT NULL, next_attempt REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0)")
            conn.execute("CREATE TABLE IF NOT EXISTS harness_bili_updates(update_id TEXT PRIMARY KEY, text TEXT NOT NULL, kind TEXT NOT NULL, data_json TEXT, targets_json TEXT NOT NULL)")

    @staticmethod
    def catalog() -> list[dict[str, Any]]:
        return tool_catalog()

    async def page(self, name: str, *, group_id: int = 0, user_id: int = 0, token: str = "") -> str:
        return await self.web.page(name, group_id=group_id, user_id=user_id, token=token)

    def _now(self, timestamp: float = 0) -> datetime:
        return datetime.fromtimestamp(timestamp or time.time(), ZoneInfo("Asia/Shanghai"))

    def _business_url(self, page: str, **query: Any) -> str:
        origin = str(self.store.get_setting("web_base_url", "http://127.0.0.1:8090")).rstrip("/")
        return origin + "/business/" + page + ("?" + urlencode(query) if query else "")

    def _refresh_knowledge_docs(self) -> None:
        export_pending_doc(self.knowledge, self.knowledge_review_dir / "收录待审.md")
        export_rejected_doc(self.knowledge, self.knowledge_review_dir / "收录拒绝归档.md")

    def _operator(self, event: InboundEvent) -> bool:
        return int(event.user_id) in {int(value) for value in self.store.get_setting("operator_ids", [])}

    async def _group_admin(self, event: InboundEvent) -> bool:
        if self._operator(event):
            return True
        if event.group_id is None:
            return False
        role = event.sender.get("role")
        if role is None:
            role = (await self.platform.member_info(event.group_id, event.user_id)).get("role")
        return role in {"owner", "admin"}

    def _mentions(self, event: InboundEvent) -> list[int]:
        return list(dict.fromkeys(int(segment["data"]["qq"]) for segment in event.segments
            if segment.get("type") == "at" and str(segment.get("data", {}).get("qq", "")).isdigit()
            and int(segment["data"]["qq"]) != event.self_id))

    def blocked(self, event: InboundEvent) -> bool:
        return self.db.passive_filter_contains(event.user_id) or self.db.active_filter_contains(event.user_id) or (
            event.group_id is not None and self.db.group_filter_contains(event.group_id, event.user_id))

    async def execute(self, event: InboundEvent, call: ToolCall) -> ToolResult:
        started = time.perf_counter()
        if not self.store.group_present(event.group_id):
            return ToolResult("disabled", "机器人已离开该群；本地功能已暂停。")
        if call.name not in TOOL_DESCRIPTIONS:
            return ToolResult("error", "没有这个本地功能。")
        if call.name != "skill_admin" and not self.store.get_setting("tool_enabled:" + call.name, True):
            return ToolResult("disabled", "这个工具已停用。")
        released_groups = self.store.get_setting("tool_groups:" + call.name, [])
        if released_groups and event.group_id not in released_groups:
            return ToolResult("disabled", "这个工具尚未在当前群启用。")
        if self.blocked(event):
            return ToolResult("blocked", "")
        announcement_allowed = (call.name == "global_announcement" or call.name == "operator_web" and call.arguments.get("page") == "announcement") and int(event.user_id) in self.store.get_setting("announcement_ids", [])
        if call.name in ADMIN_TOOLS and not self._operator(event) and not announcement_allowed:
            return ToolResult("denied", "这个操作仅限超级管理员。")
        feature = GROUP_FEATURES.get(call.name)
        if call.name in GAME_ACTIONS or call.name.startswith("mini_game_") and call.name != "mini_game_clear":
            feature = "mini_games"
        if call.name.startswith("wife_") and call.name != "wife_clear":
            feature = "today_wife"
        if feature:
            if event.group_id is None:
                return ToolResult("denied", "这个功能需要在群里使用。")
            if not self.domains.feature_enabled(event.group_id, feature):
                return ToolResult("denied", "本群尚未开启这个功能。")
        if call.name in GAME_ACTIONS:
            if not self.store.get_setting("mini_games_enabled", True):
                return ToolResult("denied", "小游戏全局开关已关闭。")
            if call.name in {"roulette_load", "bomb_load", "idiom_bomb_load", "dice_start", "guess_start"} and (
                self.domains.effective_feature_enabled(event.group_id, "live_guard") and self.guard.active_entries()):
                return ToolResult("denied", "直播期间暂停小游戏开局。")
        try:
            result = await self._execute(event, call)
        except (ValueError, KeyError, TypeError) as exc:
            result = ToolResult("error", str(exc))
        if result.status == "error" and call.name != "skill_admin":
            self.corrections.record_failure(skill_id=call.name, error=result.text,
                group_id=event.group_id or 0, user_id=event.user_id)
        result.data["facts"] = self._facts(call.name, result)
        with self.db.connect() as conn:
            conn.execute("INSERT INTO harness_tool_usage(event_id,user_id,group_id,name,status,elapsed_ms,created_at) VALUES(?,?,?,?,?,?,?)",
                (event.event_id, event.user_id, event.group_id, call.name, result.status,
                 (time.perf_counter() - started) * 1000, self._now().isoformat()))
        return result

    @staticmethod
    def _facts(name: str, result: ToolResult) -> dict[str, Any]:
        data = result.data
        facts = {"status": result.status, "summary": result.text[:300]}
        for key in ("scope", "count", "kind", "target_user_id", "group_count", "removed", "persona"):
            if key in data:
                facts[key] = data[key]
        if name == "ranking":
            rows = data.get("rows", [])
            facts.update(member_count=len(rows), message_total=sum(int(row.get("message_count", 0)) for row in rows),
                top=[{key: row.get(key) for key in ("rank", "nickname", "message_count")} for row in rows[:5]])
        elif name == "knowledge_search":
            facts["entries"] = [{"title": row["title"], "summary": row["summary"][:350]} for row in data.get("entries", [])[:3]]
        elif name.startswith("wife_") and data.get("record"):
            facts["relation"] = {key: data["record"].get(key) for key in ("day", "actor_nickname", "target_nickname")}
        elif name == "bili_status":
            facts.update(credential_available=data.get("credential_available"), authenticated=data.get("authenticated"))
        elif name == "bili_test" and "errors" in data:
            facts["successful_items"] = [key for key in data["result"] if key not in data["errors"]]
        return facts

    async def _execute(self, event: InboundEvent, call: ToolCall) -> ToolResult:
        name, args, group = call.name, dict(call.arguments), event.group_id
        now = self._now()
        if name in {"user_help", "asoul_help"}:
            return await self._help(event, args)
        if name == "robot_status":
            rows = [dict(row) for row in self.db.managed_groups()]
            for row in rows:
                gid = int(row["group_id"])
                domain = self.domains.domain_for_group(gid)
                enabled = self.domains.feature_rows(gid, include_internal=True)
                row.update(detail=(self.domains.domain_display_name(domain) + " / " if domain and domain.mode == "cluster" else "独群 / ")
                           + "、".join(item["label"] for item in enabled if item["effective_enabled"]), tag="管理群")
            lines = ["框架：TangtangHarness", "QQ连接：" + ("已连接" if getattr(self.gateway, "connected", False) else "未连接"),
                     "实时消息统计：" + ("开启" if self.store.get_setting("stats_realtime_enabled", True) else "关闭"),
                     "游戏接口：" + ("NTE / 鸣潮上游透传" if self.store.get_setting("game_api_enabled", True) else "关闭"),
                     f"本地工具：{len(TOOL_DESCRIPTIONS)} 项"]
            path = await asyncio.to_thread(self.renderer.render_status, rows, lines, {})
            return ToolResult("ok", "\n".join(lines), {"groups": len(rows), "tools": len(TOOL_DESCRIPTIONS), "group_rows": rows}, [_image(path)])
        if name in {"group_settings", "group_feature_status"}:
            if group is None:
                return ToolResult("clarification", "群设置只能在当前群使用。")
            action = args.get("action", "status")
            if action != "status" and not await self._group_admin(event):
                return ToolResult("denied", "只有本群群主、管理员或超级管理员可以修改。")
            if action == "alias":
                self.domains.set_alias(group, str(args["value"]))
            elif action in {"set", "toggle"}:
                feature = self.domains.normalize_feature(str(args["feature"]))
                if feature is None:
                    return ToolResult("clarification", "没有这个群功能。")
                enabled = not self.domains.feature_enabled(group, feature) if action == "toggle" else bool(args["enabled"])
                self.domains.set_feature(group, feature, enabled)
                if feature == "mini_games" and not enabled:
                    self.games.cancel_group_session(group)
            elif action == "filter_add":
                self.db.add_group_filter(group, int(args["user_id"]), event.user_id)
            elif action == "filter_remove":
                self.db.remove_group_filter(group, int(args["user_id"]))
            if action == "filter_status":
                members = self.db.group_filter_members(group)
                return ToolResult("ok", "本群过滤名单：" + ("、".join(map(str, members)) if members else "无"), {"filters": members})
            rows = self.domains.feature_rows(group, include_internal=self._operator(event))
            if not self._operator(event) and event.sender.get("role") not in {"owner", "admin"}:
                rows = [row for row in rows if row["configured_enabled"]]
            text = "本群：" + self.domains.display_name(group) + "\n" + "\n".join(f"{row['label']}：{'开' if row['configured_enabled'] else '关'}" for row in rows)
            return ToolResult("ok", text, {"features": rows})
        if name == "system_settings":
            return await self._system_settings(event, args)
        if name == "ranking":
            domain = self.domains.domain_for_group(group)
            if args.get("cluster_name"):
                requested_domain = self.domains.cluster_by_name_or_alias(args["cluster_name"])
                if requested_domain is None or domain is None or requested_domain.domain_id != domain.domain_id:
                    return ToolResult("clarification", "只能查看当前群所属集群的发言榜。")
            scope = str(args.get("scope", "day"))
            groups = self.domains.domain_groups(domain.domain_id) if args.get("cluster") and domain and domain.mode == "cluster" else (group,)
            if args.get("cluster") and (domain is None or domain.mode != "cluster"):
                return ToolResult("empty", "当前群不属于集群。")
            rows = self.stats.ranking_rows_for_groups(scope, groups)[:100]
            payload = await self.web.ranking_data(group, scope, cluster=bool(args.get("cluster")), rows=rows)
            path = await self.community_renderer.render_ranking(payload)
            url = self._business_url("ranking", group_id=group, group="domain" if args.get("cluster") else "current", scope=scope)
            return ToolResult("ok", f"{scope}榜共 {len(rows)} 位成员。", {"scope": scope, "rows": rows, "group_ids": groups, "page_url": url}, [_image(path), _text("排行在线：" + url)])
        if name == "group_chat_search":
            return await self._group_chat_search(event, args)
        if name.startswith("archive_"):
            return await self._archive(event, name, args)
        if name == "mini_game_help":
            path = await asyncio.to_thread(self.game_renderer.render_menu)
            pages = await asyncio.to_thread(self.game_renderer.render_game_details)
            return ToolResult("ok", "小游戏菜单及完整玩法。", {"additional_messages": [[_image(p)] for p in pages], "random_event_count": sum(map(len, RANDOM_EVENT_TYPES.values()))}, [_image(path)])
        if name.startswith("mini_game_") and name != "mini_game_clear":
            game = name.removeprefix("mini_game_")
            domain = self.domains.domain_for_group(group)
            visible = self.domains.domain_groups(domain.domain_id) if args.get("cluster") and domain else (group,)
            payload = self.games.ranking(game, group_id=None if args.get("cluster") else group, visible_group_ids=visible)
            rows = [row for section in payload["sections"] for row in section["rows"]]
            path = await asyncio.to_thread(self.game_renderer.render_ranking, payload, await self._avatar_paths(rows), {})
            return ToolResult("ok", str(payload.get("title", "小游戏榜单")), payload, [_image(path)])
        if name in GAME_ACTIONS:
            return await self._game(event, name, args)
        if name in {"mini_game_clear", "wife_clear"}:
            if group is None:
                return ToolResult("clarification", "清理需要在当前群内操作。")
            key = (name, group, event.user_id)
            if args.get("action") == "cancel":
                self._clear_requests.pop(key, None)
                return ToolResult("ok", "已取消清理。")
            if not args.get("confirm"):
                self._clear_requests[key] = time.monotonic() + 60
                return ToolResult("clarification", "60 秒内发送 #确认清缘。" if name == "wife_clear" else "60 秒内发送 #确认。")
            if self._clear_requests.pop(key, 0) < time.monotonic():
                return ToolResult("clarification", "没有有效的清理请求，请先发起清理。")
            removed = self.games.clear_group_records(group) if name == "mini_game_clear" else self.wife.clear_group_records(group)
            return ToolResult("ok", "已清理当前群对应记录。", {"removed": removed})
        if name.startswith("wife_"):
            return await self._wife(event, name, args)
        if name == "knowledge_search":
            tokens = query_tokens(str(args.get("query", event.text)))
            domains = (args["domain"],) if args.get("domain") else ("zhijiang", "mingchao")
            entries = [row for domain in domains for row in self.knowledge.approved_entries(domain) if not row["blocked"]]
            scored = sorted(((sum(5 if token in row["title"].lower() else 3 if token in row["tags"].lower()
                                  else 1 if token in row["summary"].lower() else 0 for token in tokens), row)
                             for row in entries), key=lambda pair: (-pair[0], pair[1]["entry_id"]))
            results = [row for score, row in scored if score or not tokens][:int(args.get("limit", 3))]
            blocks = []
            for row in results:
                row = dict(row)
                row["related"] = resolve_related(row.get("related_ids", "[]"), self.knowledge)
                blocks.append(f"《{row['title']}》\n{row['summary']}\n来源：{row['source_name']} {row['source_url']}"
                              + ("\n来源说明：" + row["source_note"] if row["source_note"] else "")
                              + ("\n相关词条：" + "、".join(title for _, title in row["related"]) if row["related"] else ""))
            return ToolResult("ok", "\n\n".join(blocks) or "本地知识库没有找到相关条目。", {"entries": results})
        if name == "knowledge_review":
            action = args.get("action", "pending")
            if action == "approve":
                data = self.knowledge.approve_entry(int(args["entry_id"]))
            elif action == "reject":
                self.knowledge.reject_entry(int(args["entry_id"]))
                data = {"entry_id": args["entry_id"]}
            elif action == "revive":
                data = self.knowledge.revive_entry(int(args["entry_id"]))
            else:
                data = {"entries": self.knowledge.pending_entries()}
            self._refresh_knowledge_docs()
            if action == "pending":
                rows = data["entries"]
                text = f"待审核 {len(rows)} 条，完整文档在 Harness runtime/knowledge/收录待审.md。"
                text += "".join(f"\n#{row['id']} [{row['domain']}] {row['title']}\n{row['summary'][:80]}\n来源：{row['source_name']} {row['source_url']}" for row in rows)
            else:
                text = "审核操作已完成，待审与拒绝归档文档已同步。"
            return ToolResult("ok", text, data)
        if name == "denia_gallery":
            image = self.gallery.draw(group_id=group or 0, user_id=event.user_id)
            return ToolResult("ok", "一张达妮娅美图。", {"image_id": image.image_id}, [_image(image.path)])
        if name in {"duplicate_scan", "whitelist"}:
            return await self._duplicate(event, name, args)
        if name in {"asoul_schedule", "today_live", "tomorrow_live", "week_live"}:
            view = str(args.get("view", {"today_live": "today", "tomorrow_live": "tomorrow", "week_live": "week"}.get(name, "today")))
            first = now.date() + timedelta(days=view == "tomorrow")
            last = first + timedelta(days=6 - first.weekday()) if view == "week" else first
            days = await self.asoul.schedule_for_days(first, last)
            day_items = [(first + timedelta(days=offset), days.get(first + timedelta(days=offset), [])) for offset in range((last - first).days + 1)]
            payload = schedule_payload(view, day_items, generated_at=now.isoformat())
            payload = self.asoul_web.localize_schedule_stickers(payload)
            try:
                path = await self.asoul_web.render_payload(payload, "schedule")
            except Exception:
                path = await self.asoul_renderer.render_week_schedule(day_items) if view == "week" else await self.asoul_renderer.render_schedule(first, f"{view}直播日程", days.get(first, []))
            url = self._business_url("schedule", view=view)
            return ToolResult("ok", f"{view}直播日程", {**payload, "page_url": url}, [_image(path), _text("互动日程：" + url)])
        if name == "asoul_highlight":
            action = args.get("action", "list")
            if action == "list":
                return ToolResult("ok", json.dumps(self.asoul.highlight_records(), ensure_ascii=False), {"highlights": self.asoul.highlight_records()})
            if action == "remove_record":
                records = list(self.asoul.highlight_records())
                index = int(args["index"]) - 1
                if not 0 <= index < len(records):
                    return ToolResult("clarification", "高亮记录序号不存在。")
                self.asoul.remove_highlight_key(records[index])
                return ToolResult("ok", "特别关注记录已删除。")
            items = await self.asoul.schedule_for_day(date.fromisoformat(args["date"]))
            if action == "list_day":
                text = "\n".join(f"{index}. {item.starts_at:%H:%M} {' / '.join(item.hosts)}《{item.content}》" for index, item in enumerate(items, 1))
                return ToolResult("ok", text or "当天暂无日程。", {"date": args["date"], "entries": _plain([asdict(item) for item in items])})
            index = int(args.get("index", 1)) - 1
            if not 0 <= index < len(items):
                return ToolResult("clarification", "日程序号不存在。")
            if action == "remove":
                self.asoul.remove_highlight(items[index])
            else:
                self.asoul.set_highlight(items[index], args.get("style", "粉色"))
            return ToolResult("ok", "日程高亮已更新。")
        if name == "zhijiang_refresh":
            await self.guard.refresh_and_apply()
        if name == "zhijiang_schedule":
            return await self._execute(event, ToolCall("week_live", args))
        if name in {"zhijiang_status", "zhijiang_refresh"}:
            status = asdict(self.guard.status())
            entries = [item.as_cache() for item in self.guard.active_entries()]
            data = {"status": _plain(status), "entries": entries}
            if name == "zhijiang_refresh" and status["last_error"]:
                return ToolResult("error", status["last_error"], data)
            paused = status["paused_until"]
            sections = [("直播防护", "开启" if status["enabled"] else "关闭", "只暂停受保护群的小游戏开局"),
                        ("自动暂停", str(paused) if paused else "无", f"当前直播 {len(entries)} 条"),
                        ("日程刷新", status["last_refresh"] or "尚未成功", f"已缓存未来日程 {len(status['upcoming'])} 条")]
            path = await asyncio.to_thread(self.renderer.render_admin_panel, "枝江直播防护", "本地日程与小游戏状态", sections)
            return ToolResult("ok", "\n".join(title + "：" + content for title, content, _ in sections), data, [_image(path)])
        if name.startswith("bili_"):
            return await self._bili(event, name, args)
        if name == "global_announcement":
            return await self._announcement(event, args)
        if name in {"persona_status", "persona_impression"}:
            persona = self.store.get_setting("persona", "denia")
            impressions = self.store.get_setting("impressions", {})
            impression = impressions.get(f"{group}:{event.user_id}", "还没有本群交流印象。")
            return ToolResult("ok", impression if name == "persona_impression" else f"当前人格：{persona}", {"persona": persona})
        if name == "qq_platform_health":
            login = await self.platform.login_info()
            if group is not None:
                info = await self.platform.group_info(group)
                members = await self.platform.member_list(group)
                return ToolResult("ok", "QQ平台自检通过\n机器人：" + str(login.get("nickname") or login.get("user_id"))
                    + "\n群：" + str(info.get("group_name") or group) + f"\n成员列表：{len(members)} 人",
                    {"nickname": login.get("nickname"), "group_id": group, "member_count": len(members)})
            groups = await self.platform.group_list()
            return ToolResult("ok", f"QQ平台连接正常，共 {len(groups)} 个群。", {"nickname": login.get("nickname"), "group_count": len(groups)})
        if name == "skill_admin":
            tokens = str(args.get("text", "状态")).split()
            if tokens[:1] == ["纠错"]:
                return self._correction_action(event, tokens[1:])
            if tokens[:1] == ["开关"] and len(tokens) >= 3 and tokens[1] in {"开", "关", "状态"}:
                target = tokens[2]
                if target not in TOOL_DESCRIPTIONS or target == "skill_admin":
                    return ToolResult("clarification", "请选择一个已注册业务工具。")
                if tokens[1] != "状态":
                    self.store.set_setting("tool_enabled:" + target, tokens[1] == "开")
                    self.store.set_setting("tool_groups:" + target, [int(group) for group in tokens[3:] if group.isdigit()])
                return ToolResult("ok", f"{target}：{'开' if self.store.get_setting('tool_enabled:' + target, True) else '关'}", {"enabled": self.store.get_setting("tool_enabled:" + target, True), "group_ids": self.store.get_setting("tool_groups:" + target, [])})
            if tokens[:1] == ["开关"] and len(tokens) == 3:
                if tokens[1] not in TOOL_DESCRIPTIONS or tokens[1] == "skill_admin":
                    return ToolResult("clarification", "请选择一个已注册业务工具。")
                if tokens[2] not in {"开", "关", "on", "off"}:
                    return ToolResult("clarification", "工具开关使用 开|关。")
                self.store.set_setting("tool_enabled:" + tokens[1], tokens[2] in {"开", "on"})
            if tokens[:2] == ["数据", "清理"] and len(tokens) == 3:
                corrections = self.corrections.purge_user(int(tokens[2]))
                with self.db.connect() as conn:
                    count = conn.execute("DELETE FROM harness_tool_usage WHERE user_id=?", (int(tokens[2]),)).rowcount
                return ToolResult("ok", f"已清理 {count} 条本地工具用量及 {corrections} 条纠错记录。",
                                  {"removed": count, "corrections_removed": corrections})
            if tokens[:2] == ["数据", "导出"]:
                with self.db.connect() as conn:
                    rows = [dict(row) for row in conn.execute("SELECT * FROM harness_tool_usage ORDER BY id")]
                path = self.root / "runtime" / "tool-usage-export.json"
                path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
                corrections_path = self.root / "runtime" / "tool-corrections-export.json"
                corrections_count = self.corrections.export(corrections_path)
                return ToolResult("ok", "工具用量及纠错账本已导出到新系统运行目录。",
                    {"path": str(path), "count": len(rows), "corrections_path": str(corrections_path), "corrections_count": corrections_count})
            with self.db.connect() as conn:
                rows = [dict(row) for row in conn.execute("SELECT name,status,COUNT(*) AS calls,AVG(elapsed_ms) AS average_ms FROM harness_tool_usage GROUP BY name,status")]
            catalog = [{**tool, "enabled": self.store.get_setting("tool_enabled:" + tool["name"], True)} for tool in self.catalog()]
            summary = self.corrections.summary()
            return ToolResult("ok", f"本地工具调用状态；未解决纠错 {summary['by_status'].get('open', 0)} 条。",
                              {"tools": catalog, "usage": rows, "corrections": summary})
        if name in {"model_settings", "proactive_settings"}:
            key = "active_model" if name == "model_settings" else "proactive"
            if "value" in args:
                self.store.set_setting(key, args["value"])
            return ToolResult("ok", str(self.store.get_setting(key, "未配置")), {key: self.store.get_setting(key, None)})
        if name == "operator_web":
            if event.group_id is not None:
                return ToolResult("clarification", "请私聊机器人获取管理网页链接。")
            page = str(args.get("page", "duplicate"))
            token = self.web.create_session(page, user_id=event.user_id)
            url = self._business_url(page, token=token)
            return ToolResult("ok", f"管理网页：{url}", {"panel": url})
        raise ValueError(f"尚未实现此工具：{name}")

    async def _help(self, event: InboundEvent, args: dict[str, Any]) -> ToolResult:
        if args.get("audience") == "admin":
            if not self._operator(event):
                return ToolResult("denied", "只有超级管理员可以查看超级管理员帮助。")
            pages = super_admin_help_pages(len(self.domains.all_group_ids()))
            pages[-1][2].append(("本地工具与纠错", "#技能 状态 / #技能 列表\n#技能 开关 开|关 <工具ID> [群号...]\n#技能 纠错 列表 / 解决 <编号> / 导出\n#技能 数据 导出 / 清理 <QQ号>", "工具开关与纠错账本只操作新系统；纠错记录不参与工具调用门禁。"))
            nodes = []
            for title, subtitle, sections in pages:
                path = await asyncio.to_thread(self.renderer.render_admin_panel, title, subtitle, sections)
                text = admin_help_page_text(title, sections)
                nodes.extend(build_forward_nodes([title + " 图片", text], [path, None], event.self_id, title="超级管理员帮助"))
            return ToolResult("ok", "超级管理员分类手册。", {"audience": "admin", "forward_nodes": nodes, "page_count": len(pages)})
        if args.get("format") == "text":
            text = user_help_text(stats_enabled=bool(self.store.get_setting("stats_realtime_enabled", True)))
            nodes = build_forward_nodes([text], [None], event.self_id, title="普通用户帮助文字")
            return ToolResult("ok", "普通用户可复制指令。", {"format": "text", "forward_nodes": nodes})
        categories = user_help_categories(stats_enabled=bool(self.store.get_setting("stats_realtime_enabled", True)))
        path = await asyncio.to_thread(self.renderer.render_user_help, "机器人功能手册", "普通用户分类帮助 · 指令可通过 #帮助文字 复制", categories)
        url = self._business_url("help")
        return ToolResult("ok", "普通用户分类帮助图卡。", {"format": "image", "image_path": str(path), "category_count": len(categories), "page_url": url}, [_image(path), _text("帮助在线：" + url)])

    def _correction_action(self, event: InboundEvent, tokens: list[str]) -> ToolResult:
        action = tokens[0] if tokens else "列表"
        if action == "类别":
            return ToolResult("ok", "可用类别：" + "、".join(sorted(CATEGORIES)), {"categories": sorted(CATEGORIES)})
        if action == "列表":
            status = tokens[1] if len(tokens) > 1 else "open"
            status = {"全部": "", "all": "", "未解决": "open", "已解决": "resolved"}.get(status, status)
            if status not in {"", "open", "resolved"}:
                return ToolResult("clarification", "纠错状态使用 open / resolved / all。")
            entries = self.corrections.list_entries(status=status, limit=10)
            lines = [f"[{entry['id']}] {entry['skill_id']} [{entry['category']}/{entry['severity']}]：{entry['detail'][:80]}" for entry in entries]
            return ToolResult("ok", "技能纠错记录：\n" + "\n".join(lines) if lines else "没有匹配的纠错记录。", {"entries": entries})
        if action == "记录" and len(tokens) >= 4:
            skill_id, category = tokens[1:3]
            if skill_id not in TOOL_DESCRIPTIONS:
                return ToolResult("clarification", "请选择一个已注册业务工具。")
            if category not in CATEGORIES:
                return ToolResult("clarification", "纠错类别不正确，请使用 #技能 纠错 类别 查看。")
            severity = tokens[3] if tokens[3] in SEVERITIES else "warning"
            detail = " ".join(tokens[4:] if tokens[3] in SEVERITIES else tokens[3:])
            if not detail:
                return ToolResult("clarification", "请填写纠错说明。")
            entry = self.corrections.record(skill_id=skill_id, category=category, severity=severity,
                group_id=event.group_id or 0, user_id=event.user_id, source="operator", detail=detail)
            return ToolResult("ok", f"已记录纠错 [{entry['id']}]。", {"entry": entry})
        if action in {"查看", "解决"} and len(tokens) >= 2:
            entry = self.corrections.get(tokens[1])
            if entry is None:
                return ToolResult("empty", "没有找到这条纠错记录。")
            if action == "解决":
                changed = self.corrections.resolve(tokens[1], " ".join(tokens[2:]) or "由超级管理员标记解决")
                return ToolResult("ok", "已标记解决。" if changed else "该记录已经解决。", {"entry": self.corrections.get(tokens[1])})
            return ToolResult("ok", json.dumps(entry, ensure_ascii=False), {"entry": entry})
        if action == "导出":
            path = self.root / "runtime" / "tool-corrections-export.json"
            count = self.corrections.export(path)
            return ToolResult("ok", f"已导出最近 {count} 条纠错记录到新系统运行目录。", {"path": str(path), "count": count})
        return ToolResult("clarification", "用法：#技能 纠错 列表 [open|resolved|all] / 类别 / 记录 <工具ID> <类别> [info|warning|error] <说明> / 查看 <编号> / 解决 <编号> [说明] / 导出")

    async def _avatar_paths(self, rows: list[dict]) -> dict[int, Path]:
        # Tests and offline deployments can use existing/default local portraits.
        if not self.store.get_setting("avatar_fetch_enabled", True):
            return self.avatars.cached_paths(rows)
        return await self.avatars.prefetch(rows)

    async def _group_chat_search(self, event: InboundEvent, args: dict) -> ToolResult:
        group = event.group_id
        if group is None:
            return ToolResult("clarification", "请在要搜索的群聊中使用，本功能只查询当前群。")
        keyword = str(args.get("keyword", "")).strip()
        if not keyword:
            return ToolResult("clarification", "请提供要搜索的群聊关键词。")
        if len(keyword) > 100:
            return ToolResult("clarification", "群聊搜索关键词最多 100 字。")
        page = int(args.get("page", 1))
        if page < 1:
            return ToolResult("clarification", "页码必须为正整数。")
        excluded_user_ids = await asyncio.to_thread(self.store.blocked_users, group)
        rows, total = await asyncio.to_thread(
            self.archive.group_search, group, keyword, page,
            exclude_message_id=event.event_id, excluded_user_ids=excluded_user_ids,
        )
        total_pages = max(1, (total + 9) // 10)
        data = {"scope": "current_group", "keyword": keyword, "page": page, "page_size": 10,
                "count": len(rows), "total_count": total, "total_pages": total_pages, "records": []}
        if not total:
            return ToolResult("empty", "当前群已存档聊天内容中没有找到这个关键词。", data)
        if not rows:
            return ToolResult("clarification", f"共找到 {total} 条记录，请选择第 1–{total_pages} 页。", data)
        lines = [f"本群已存档聊天内容：共 {total} 条，第 {page}/{total_pages} 页（最近优先）。"]
        for index, row in enumerate(rows, start=(page - 1) * 10 + 1):
            content = str(row["content"])
            start = max(0, content.find(keyword) - 50)
            excerpt = ("…" if start else "") + content[start:start + 180] + ("…" if start + 180 < len(content) else "")
            occurred_at = datetime.fromisoformat(row["occurred_at"]).astimezone(self.archive.zone).strftime("%Y-%m-%d %H:%M:%S")
            nickname = str(row.get("nickname") or "群友")
            lines.append(f"{index}. {occurred_at} · {nickname}\n{excerpt}")
            data["records"].append({"message_id": row["message_id"], "user_id": row["user_id"],
                                    "nickname": nickname, "occurred_at": row["occurred_at"], "excerpt": excerpt})
        if page < total_pages:
            lines.append(f"下一页：#群聊搜索 {keyword} {page + 1}")
        return ToolResult("ok", "\n\n".join(lines), data)

    async def _archive(self, event: InboundEvent, name: str, args: dict) -> ToolResult:
        group = event.group_id
        target = int(args.get("target_user_id", event.user_id))
        numeric_command = re.match(r"^#\s*(?:发言记录|发言搜索|发言画像|画像)\s+(\d+)(?:\s|$)", event.text.strip())
        explicit_target = numeric_command is not None and int(numeric_command[1]) == target
        if target != event.user_id and target not in self._mentions(event) and not self._operator(event) and not explicit_target:
            return ToolResult("clarification", "档案目标请在本轮消息中真实 @。")
        page = int(args.get("page", 1))
        if page < 1:
            return ToolResult("clarification", "页码必须为正整数。")
        keyword = str(args.get("keyword", "")) if name == "archive_search" else ""
        if name != "archive_profile":
            rows = self.archive.records(target, (group,), keyword, page)
            path = await asyncio.to_thread(self.archive_renderer.render, target, rows, page, keyword,
                                           await self._avatar_paths(rows), self.archive.total_pages(target, (group,), keyword))
            return ToolResult("ok", f"已找到 {len(rows)} 条本群发言。", {"target_user_id": target, "page": page, "count": len(rows), "records": rows}, [_image(path)])
        domain = self.domains.domain_for_group(group)
        groups = self.domains.domain_groups(domain.domain_id) if domain else (group,)
        rows = self.archive.all_records(target, groups)
        if len(rows) <= 100:
            return ToolResult("empty", "发言画像需要超过 100 条已存档纯文本发言。", {"count": len(rows)})
        summary = self.archive.summary(target, groups)
        scope_key = f"group:{group}" if not domain or domain.mode == "solo" else domain.domain_key
        existing = self.db.a_coast_profile_state(target, scope_key)
        include_ai = bool(self.store.get_setting("speech_profile_ai_enabled", False)) and bool(existing)
        path = await asyncio.to_thread(self.archive_renderer.render_profile, target, str(rows[-1].get("nickname", "群友")),
            str(rows[0].get("occurred_at", "")), existing, rows, include_ai_profile=include_ai)
        return ToolResult("ok", "本地发言统计画像已生成。", {"target_user_id": target, "count": len(rows), "summary": summary,
                          "existing_ai_profile": include_ai}, [_image(path)])

    async def _game(self, event: InboundEvent, name: str, args: dict) -> ToolResult:
        group, user, nick = event.group_id, event.user_id, event.nickname
        if name == "roulette_load":
            outcome = self.games.start_roulette(group, user, nick)
        elif name == "roulette_fire":
            outcome = self.games.fire(group, user, nick)
        elif name in {"bomb_load", "idiom_bomb_load"}:
            outcome = self.games.start_bomb(group, user, nick, idiom_mode=name == "idiom_bomb_load",
                idiom_ruleset=args.get("mode", "professional"), duration_seconds=args.get("duration"))
        elif name in {"bomb_pass", "idiom_bomb_pass"}:
            target = int(args.get("target_user_id", 0))
            if target not in self._mentions(event) or target == user:
                return ToolResult("clarification", "请真实 @ 另一位当前群成员。")
            member = await self.platform.member_info(group, target)
            outcome = self.games.throw_bomb(group, user, nick, target, str(member.get("card") or member.get("nickname") or target), idiom=args.get("idiom"))
        elif name == "dice_start":
            outcome = self.games.roll_dice(group, user, nick)
        elif name == "guess_start":
            outcome = self.games.start_guess(group, user, nick)
        else:
            value = int(args["value"])
            if not 0 <= value <= 999:
                return ToolResult("clarification", "猜数字范围为 0–999。")
            outcome = self.games.guess_number(group, user, nick, value)
        result = await self._game_result(outcome)
        if outcome.kind == "guess_cursed":
            result.data.setdefault("actions", []).append({"action": "delete_msg", "params": {"message_id": int(event.event_id)}})
        return result

    def _game_mute_enabled(self, group_id: int) -> bool:
        selected = self.store.get_setting(f"game_mute:{group_id}", None)
        if selected is not None:
            return bool(selected)
        disabled = self.store.get_setting("game_mute_disabled_group_ids", None)
        if disabled is None:
            raw = self.db.passive_settings().get("game_mute_disabled_group_ids", "")
            disabled = [int(value.strip()) for value in raw.split(",") if value.strip().isdigit()]
        return self.domains.feature_enabled(group_id, "mini_games") and group_id not in disabled

    async def _game_result(self, outcome: GameEvent) -> ToolResult:
        data = asdict(outcome)
        if outcome.mute_user_ids and self._game_mute_enabled(outcome.group_id):
            member = await self.platform.member_info(outcome.group_id, int(self.gateway.self_id))
            if member.get("role") in {"owner", "admin"}:
                duration = random.randint(30, 60 if outcome.game_type == "guess" else 120)
                data["actions"] = [{"action": "set_group_ban", "params": {"group_id": outcome.group_id, "user_id": target,
                    "duration": max(1, duration // 2) if target in outcome.half_mute_user_ids else duration}}
                    for target in dict.fromkeys(outcome.mute_user_ids) if target != int(self.gateway.self_id)]
        remaining = outcome.text
        messages, replaced = [], set()
        replacements = [(str(name), int(target)) for name, target in outcome.mention_replacements
                        if str(name).strip() and int(target) > 0]
        while remaining and replacements:
            matches = [(remaining.find(name), index, name, target) for index, (name, target) in enumerate(replacements)
                       if remaining.find(name) >= 0]
            if not matches:
                break
            offset, index, name, target = min(matches, key=lambda item: (item[0], -len(item[2])))
            if offset:
                messages.append(_text(remaining[:offset]))
            messages.append({"type": "at", "data": {"qq": str(target)}})
            replaced.add(target)
            remaining = remaining[offset + len(name):]
            replacements.pop(index)
        if remaining:
            messages.append(_text(remaining))
        if not messages:
            messages.append(_text(outcome.text))
        fallback = [part for target in dict.fromkeys(outcome.mention_user_ids) if target > 0 and target not in replaced
                    for part in ({"type": "at", "data": {"qq": str(target)}}, _text(" "))]
        messages = [*fallback, *messages]
        return ToolResult("ok", outcome.text, data, messages)

    async def _wife(self, event: InboundEvent, name: str, args: dict) -> ToolResult:
        group, user = event.group_id, event.user_id
        if name in {"wife_draw", "wife_take", "wife_divorce"} and self.wife_game.is_locked(group):
            return ToolResult("empty", "今天的缘分故事已收官。", {"kind": "locked"})
        if name in {"wife_draw", "wife_take"}:
            members = await self.platform.member_list(group)
            if name == "wife_take":
                target = int(args.get("target_user_id", 0))
                if target not in self._mentions(event) or target == user:
                    return ToolResult("clarification", "请真实 @ 一位其他群友。")
                outcome = self.wife.force_draw(group, user, event.nickname, members, target_id=target)
            else:
                outcome = self.wife.draw(group, user, event.nickname, members)
            record = outcome.record
            self.wife_game.passive_interaction_after_command(group, user, event.event_id)
            if name == "wife_take" and outcome.kind in {"force_existing", "existing", "existing_divorced"}:
                text = ("强取失败：你今天的重抽机会已经用过，不能再抽新的缘分。"
                        if str((record or {}).get("status", "")) == "divorced" else
                        f"强取失败：你今天已经有老婆（{str((record or {}).get('target_nickname') or '当前老婆')}）了；想换人请先 #离婚。")
                return ToolResult("empty", text, {"kind": outcome.kind, "record": record})
            if record:
                self.wife_game.ensure_relation_for_draw(record)
                record = dict(record)
                record["intro_line"] = self.wife.intro_line(record)
                archive = self.wife_game.personal_archive(group, user)
                relation = next((row.get("relation") or {} for row in archive["own"]
                                 if int(row.get("draw_index") or 0) == int(record.get("draw_index") or 0)), {})
                avatars = await self._avatar_paths([{"user_id": record["actor_id"]}, {"user_id": record["target_id"]}])
                messages = [{"type": "reply", "data": {"id": str(event.event_id)}}]
                data = {"kind": outcome.kind, "record": record, "relation": relation}
                if outcome.kind == "existing_divorced":
                    path = await asyncio.to_thread(self.game_renderer.render_divorce, record, self.wife.divorce_lines(record), avatars, relation)
                    messages.append(_image(path))
                    return ToolResult("ok", "今天的缘分已结束，关系已留档。", data, messages)
                state = self.wife_game.day_state(group)
                reveal = self.wife_game.draw_reveal(record, relation)
                path = await asyncio.to_thread(self.game_renderer.render_today_wife_game_draw, record, self.wife.story_lines(record),
                    self.wife.context_lines(record), avatars, state, relation, draw_reveal=reveal)
                data.update(day_state=state, draw_reveal=reveal)
                messages.append(_image(path))
                if outcome.kind == "drawn" and int(record.get("draw_index") or 0) == 1:
                    lead = str(reveal.get("mention_lead") or "今天的缘分悄悄落在了").strip()
                    ending = " 身上。" if lead == "今天的缘分悄悄落在了" else "，这一幕从这里开始。"
                    messages.extend([_text(f"\n{lead} "), {"type": "at", "data": {"qq": str(record["target_id"])}}, _text(ending)])
                return ToolResult("ok", "今日缘分已领取。", data, messages)
            if outcome.kind == "force_invalid_target":
                return ToolResult("clarification", "请真实 @ 一位当前群成员，重抽时不能选择刚结束缘分的对象。", {"kind": outcome.kind})
            if outcome.kind == "no_candidates":
                return ToolResult("empty", self.wife.state_message(outcome.kind, group, user), {"kind": outcome.kind})
            return ToolResult("error", f"今日缘分：{outcome.kind}", {"kind": outcome.kind})
        if name == "wife_divorce":
            outcome = self.wife.divorce(group, user)
            data = {"kind": outcome.kind, "record": outcome.record}
            if outcome.kind == "divorced":
                record = outcome.record
                archive = self.wife_game.personal_archive(group, user)
                relation = next((row.get("relation") or {} for row in archive["own"]
                                 if int(row.get("draw_index") or 0) == int(record.get("draw_index") or 0)), {})
                data["relation"] = relation
                path = await asyncio.to_thread(self.game_renderer.render_divorce, record, self.wife.divorce_lines(record),
                    await self._avatar_paths([{"user_id": record["target_id"]}]), relation)
                return ToolResult("ok", "已结束今天的缘分。", data,
                    [{"type": "reply", "data": {"id": str(event.event_id)}}, _image(path)])
            if outcome.kind in {"not_found", "already_divorced"}:
                return ToolResult("empty", self.wife.state_message(outcome.kind, group, user), data)
            return ToolResult("error", f"解缘：{outcome.kind}", data)
        if name == "wife_personal":
            page = int(args.get("page", 1))
            payload = self.wife_game.personal_archive(group, user) if page == 1 else self.wife_game.personal_history(group, user, page - 1)
            method = self.game_renderer.render_today_wife_archive if page == 1 else self.game_renderer.render_today_wife_history_archive
        else:
            requested = date.fromisoformat(args["date"]) if args.get("date") else None
            payload = self.wife_game.group_archive(group) if args.get("history") else self.wife_game.group_story(group, requested_day=requested)
            if not args.get("history"):
                rows = payload["records"]
                avatars = await self._avatar_paths([{"user_id": member} for row in rows for member in (row["actor_id"], row["target_id"])])
                path = await asyncio.to_thread(self.game_renderer.render_group_today_wife, rows, avatars, payload["day"],
                    {"title": payload["day_state"]["script_title"]}, payload["spotlight"])
            else:
                path = await asyncio.to_thread(self.game_renderer.render_today_wife_group_archive, payload)
        if name == "wife_personal":
            path = await asyncio.to_thread(method, payload, {})
        self.wife_game.passive_interaction_after_command(group, user, event.event_id)
        return ToolResult("ok", "缘分记录。", payload, [_image(path)])

    async def _duplicate(self, event: InboundEvent, name: str, args: dict) -> ToolResult:
        if name == "whitelist":
            action = args.get("action", "list")
            note = ""
            if action == "add":
                self.db.add_whitelist(int(args["user_id"]), event.user_id, str(args.get("note", "")))
                note = "已加入查重白名单。"
            elif action == "remove":
                removed = self.db.remove_whitelist(int(args["user_id"]))
                note = "已移除查重白名单。" if removed else "该用户不在白名单中。"
            rows = self.db.whitelist_profiles()
            if action != "list":
                return ToolResult("ok", note, {"members": rows})
            path = await asyncio.to_thread(self.renderer.render_whitelist, rows, await self._avatar_paths(rows))
            text = f"查重白名单共 {len(rows)} 人。" + "".join(f"\n{row['user_id']} {row.get('nickname', '')} {row.get('note', '')}" for row in rows)
            nodes = build_forward_nodes(["查重白名单图片", text], [path, None], event.self_id, title="查重白名单")
            return ToolResult("ok", text, {"members": rows, "forward_nodes": nodes}, [_image(path)])
        groups = tuple(int(group) for group in args.get("group_ids", self.domains.all_group_ids()))
        if len(groups) < 2:
            return ToolResult("clarification", "查重至少需要两个群。")
        if any(group not in self.domains.all_group_ids() for group in groups):
            return ToolResult("clarification", "查重目标必须是受管群。")
        method = self.duplicate.scan if args.get("mode") == "source" else self.duplicate.scan_all
        rows, failed = await method(self.platform, groups, bool(args.get("ignore_whitelist", False)))
        if failed:
            return ToolResult("error", "部分群成员列表同步失败，未执行残缺查重。", {"failed_groups": failed})
        labels = [(group, self.domains.display_name(group)) for group in groups]
        path = await asyncio.to_thread(self.renderer.render_duplicate, rows, len(rows), 1, 1,
            await self._avatar_paths(rows), labels, "source" if args.get("mode") == "source" else "all")
        return ToolResult("ok", f"发现 {len(rows)} 名重复成员。", {"members": rows, "groups": groups}, [_image(path)])

    async def _system_settings(self, event: InboundEvent, args: dict) -> ToolResult:
        action, key = args.get("action", "status"), str(args.get("key", ""))
        tokens = event.text.lstrip("# ").split()
        head = tokens[0] if tokens else ""
        group_alias = head in {"主动过滤", "被动过滤", "移除主动过滤", "移除被动过滤", "主动过滤列表", "被动过滤列表",
                              "整点报时", "总游戏开", "游戏总开", "总游戏关", "游戏总关", "总游戏状态", "游戏总状态"}
        if event.group_id is not None and (head == "系统设置" or not group_alias and key != f"game_mute:{event.group_id}"):
            return ToolResult("denied", "系统设置仅限超级管理员私聊。")
        if not key:
            return ToolResult("ok", "系统设置：\n#系统设置 集群 列表|创建|邀请|移除|解散\n#系统设置 游戏接口 状态|开|关\n#系统设置 小游戏 全局 状态|开|关\n#系统设置 被呼叫会话 状态|开|关\n#系统设置 糖糖主动聊天 状态|开|关\n#系统设置 人格后台整理 状态|开|关\n#系统设置 语音 状态|开|关\n#系统设置 被动互动 <群号> 状态|<参数> <值>\n#系统设置 准时报点 状态|开|关|时段 HH:MM HH:MM")
        if key == "cluster":
            if action == "create":
                domain = self.domains.create_cluster(args["name"], args.get("alias", ""))
                return ToolResult("ok", "集群已创建。", asdict(domain))
            if action == "invite":
                self.domains.add_group_to_cluster(int(args["group_id"]), int(args["domain_id"]))
            elif action == "remove":
                self.domains.remove_group_from_cluster(int(args["group_id"]))
            elif action == "dissolve":
                self.domains.dissolve_cluster(int(args["domain_id"]))
            with self.db.connect() as conn:
                rows = [dict(row) for row in conn.execute("SELECT domain_id,name,alias FROM group_domains WHERE mode='cluster' AND enabled=1")]
            return ToolResult("ok", json.dumps(rows, ensure_ascii=False), {"clusters": rows})
        if key in {"active_filter", "passive_filter"}:
            kind = key.removesuffix("_filter")
            ids = [int(user) for user in args.get("user_ids", [])]
            if action == "add":
                self.db.add_filter_members(kind, ids, event.user_id)
            elif action == "remove":
                self.db.remove_filter_members(kind, ids)
            members = [dict(row) for row in self.db.filter_members(kind)]
            label = "主动" if kind == "active" else "被动"
            return ToolResult("ok", f"{label}过滤名单（{len(members)} 人）：" + ("、".join(str(row["user_id"]) for row in members) if members else "无"), {"members": members})
        if key == "hourly_schedule" and action == "set":
            start, end = args["value"]
            for value in (start, end):
                clock_time.fromisoformat(value)
            self.store.set_setting("hourly_start", start)
            self.store.set_setting("hourly_end", end)
        if key.startswith("passive:") and action == "configure":
            config = self.store.get_setting(key, {})
            parameter, raw = str(args["parameter"]), args.get("value")
            original_parameter = parameter
            aliases = {"表情": "reaction_enabled", "表情开关": "reaction_enabled", "复读": "repeat_enabled", "复读开关": "repeat_enabled", "三连": "triple_enabled", "三连开关": "triple_enabled",
                "三连复读": "triple_enabled", "表情概率": "reaction_probability", "表情命中率": "reaction_probability",
                "复读概率": "repeat_probability", "复读命中率": "repeat_probability", "三连概率": "triple_probability", "三连命中率": "triple_probability",
                "表情冷却": "reaction_cooldown", "复读冷却": "repeat_cooldown", "复读间隔": "repeat_interval"}
            parameter = aliases.get(parameter, parameter)
            if raw is not None:
                if parameter.endswith("enabled"):
                    if str(raw) not in {"开", "开启", "on", "关", "关闭", "off"}:
                        return ToolResult("clarification", "开关使用 开|关。")
                    value = str(raw) in {"开", "开启", "on"}
                elif parameter.endswith("probability"):
                    value = float(str(raw).rstrip("%"))
                    if str(raw).endswith("%") or value > 1:
                        value /= 100
                    if not 0 <= value <= 1:
                        return ToolResult("clarification", "概率范围为 0–100%。")
                elif parameter.endswith(("cooldown", "interval")):
                    value = int(raw)
                    if value < 0:
                        return ToolResult("clarification", "冷却和间隔不能为负数。")
                    if original_parameter == "复读冷却":
                        value *= 60
                else:
                    return ToolResult("clarification", "没有这个被动互动参数。")
                config[parameter] = value
                self.store.set_setting(key, config)
            return ToolResult("ok", json.dumps(config, ensure_ascii=False), {"config": config})
        if key == "hourly_range":
            if action in {"range_add", "range_remove"}:
                group = int(args["group_id"])
                if group not in self.domains.all_group_ids():
                    return ToolResult("clarification", "请选择当前受管群。")
                self.domains.set_feature(group, "hourly", action == "range_add")
            groups = self.domains.enabled_groups("hourly")
            return ToolResult("ok", "整点报时群范围。", {"group_ids": groups})
        if action == "set":
            self.store.set_setting(key, args["value"])
            if key == "mini_games_enabled" and not args["value"]:
                for group in self.domains.all_group_ids():
                    self.games.cancel_group_session(group)
        if key.startswith("game_mute:"):
            enabled = self._game_mute_enabled(int(key.split(":", 1)[1]))
            return ToolResult("ok", f"小游戏处罚禁言：{'开' if enabled else '关'}", {"key": key, "value": enabled})
        if key in {"hourly_enabled", "hourly_schedule"}:
            enabled = bool(self.store.get_setting("hourly_enabled", False))
            start, end = self.store.get_setting("hourly_start", "08:00"), self.store.get_setting("hourly_end", "23:00")
            groups = sorted(self.domains.enabled_groups("hourly"))
            return ToolResult("ok", f"准时报点：{'开' if enabled else '关'}\n时段：{start}–{end}\n群范围：" + ("、".join(map(str, groups)) if groups else "无"),
                              {"enabled": enabled, "start": start, "end": end, "group_ids": groups})
        return ToolResult("ok", f"{key}：{self.store.get_setting(key, None)}", {"key": key, "value": self.store.get_setting(key, None)})

    async def _announcement(self, event: InboundEvent, args: dict) -> ToolResult:
        groups = list(dict.fromkeys(int(group) for group in args.get("group_ids", [])))
        if not groups:
            default = self.store.get_setting("announcement_cluster", "")
            domain = self.domains.cluster_by_name_or_alias(default) if default else None
            groups = list(self.domains.domain_groups(domain.domain_id)) if domain else []
        if not groups or any(group not in self.domains.all_group_ids() for group in groups):
            return ToolResult("clarification", "请选择当前受管群或设置默认公告集群。")
        text = str(args.get("text", ""))
        if len(text) > 1000:
            return ToolResult("clarification", "公告正文最多 1000 字。")
        segments = []
        if args.get("at_all"):
            segments.append({"type": "at", "data": {"qq": "all"}})
        if args.get("raw_image"):
            image_segments = list(event.segments)
            quoted = event.quoted
            if quoted is None:
                reply = next((part for part in event.segments if part.get("type") == "reply"), None)
                if reply:
                    quoted = await self.platform.call("get_msg", message_id=reply["data"]["id"])
            if quoted:
                image_segments += list(quoted.get("message", quoted.get("segments", [])))
            part = next((part for part in image_segments if part.get("type") == "image"), None)
            if part is None:
                return ToolResult("clarification", "请在指令中附图或回复一张图片。")
            source = str(part["data"].get("url") or part["data"].get("file") or "")
            if source.startswith(("https://", "http://")):
                async with httpx.AsyncClient(timeout=15) as client:
                    response = await client.get(source)
                    response.raise_for_status()
                    content = response.content
            elif source.startswith("base64://"):
                content = base64.b64decode(source.removeprefix("base64://"))
            else:
                value = unquote(urlparse(source).path) if source.startswith("file:") else source
                if value.startswith("/") and len(value) > 2 and value[2] == ":":
                    value = value[1:]
                content = Path(value).read_bytes()
            args["image_path"] = str(self.web.upload(content, "announcement.png"))
        if args.get("image_path"):
            path = Path(args["image_path"]).resolve()
            path.relative_to(self.root.resolve())
            if not path.is_file():
                return ToolResult("clarification", "公告图片不存在。")
        else:
            if not text.strip():
                return ToolResult("clarification", "公告正文不能为空。")
            member = args.get("member")
            candidates = list((self.resources / "asoul_stickers" / str(member)).glob("*.png")) if member and member != "__random__" else list((self.resources / "asoul_stickers").glob("*/*.png"))
            if args.get("sticker") and args["sticker"] != "__random__":
                candidates = [candidate for candidate in candidates if candidate.stem == args["sticker"]]
            if member == "__none__":
                candidates = []
            sticker = random.choice(candidates) if candidates else None
            path = await asyncio.to_thread(self.renderer.render_global_announcement, text, sticker)
        if args.get("graphic"):
            member = args.get("member")
            candidates = list((self.resources / "asoul_stickers" / str(member)).glob("*.png")) if member and member not in {"__random__", "__none__"} else []
            sticker = next((candidate for candidate in candidates if candidate.stem == args.get("sticker")), None)
            path = await asyncio.to_thread(self.renderer.render_global_graphic_announcement, str(args["title"]), text, path, sticker)
        segments.append(_image(path))
        if args.get("extra_text"):
            segments.append(_text("\n" + str(args["extra_text"])))
        return ToolResult("ok", f"公告已生成，等待向 {len(groups)} 个群发送。", {"deliveries": [{"group_id": group, "message": segments} for group in groups], "image_path": str(path), "group_count": len(groups)})

    def _bili_config(self) -> None:
        for key in ("enabled", "target_uids", "comment_target_uids", "push_dynamic", "push_video", "push_live", "push_comment", "render_cards", "poll_interval_seconds"):
            value = self.store.get_setting("bili_" + key, getattr(business_config.settings, "asoul_bili_" + key))
            setattr(business_config.settings, "asoul_bili_" + key, tuple(map(str, value)) if key.endswith("uids") else value)

    async def _bili(self, event: InboundEvent, name: str, args: dict) -> ToolResult:
        self._bili_config()
        if name == "bili_status":
            credential = self.asoul._credential()
            authenticated, error = None, ''
            if credential is not None:
                try:
                    authenticated = await asyncio.wait_for(credential.check_valid(), timeout=10)
                except Exception as exc:
                    error = type(exc).__name__
            text = self.asoul.monitor_status(authenticated=authenticated)
            return ToolResult("ok", text, {"credential_available": credential is not None,
                "authenticated": authenticated, "authentication_error": error})
        if name == "bili_logout":
            self.asoul.clear_credential()
            return ToolResult("ok", "已清除本机 B站登录态。")
        if name == "bili_login":
            if event.group_id is not None:
                return ToolResult("denied", "请在私聊中进行 B站扫码登录。")
            login = await self.asoul.create_qr_login()
            path = self.root / "runtime" / "bili-login.png"
            login.get_qrcode_picture().to_file(str(path))
            task = asyncio.create_task(self._finish_bili_login(login, event))
            self._qr_tasks.add(task)
            task.add_done_callback(self._qr_tasks.discard)
            return ToolResult("ok", "请使用 B站 App 人工扫码，登录态仅保存到新系统数据库。", {}, [_image(path)])
        kind = args.get("kind", "live")
        if kind == "atall":
            if event.group_id is None:
                return ToolResult("denied", "请在需要验证的群聊中使用。")
            member = await self.platform.member_info(event.group_id, int(self.gateway.self_id))
            if member.get("role") not in {"owner", "admin"}:
                return ToolResult("denied", "机器人需要本群管理员权限。")
            return ToolResult("ok", "B站开播全体提醒测试", {}, [{"type": "at", "data": {"qq": "all"}}, _text(" B站开播全体提醒测试")])
        if event.group_id is not None:
            return ToolResult("denied", "B站测试与调试请在私聊进行。")
        uid = str(args["uid"])
        if not uid.isdigit() or int(uid) <= 0:
            return ToolResult("clarification", "B站 UID 必须是正整数。")
        if kind in {"dynamic", "dump_dynamic"}:
            return ToolResult("disabled", "动态手工诊断已停用。")
        if kind == "all":
            payload = {"dynamic": "已停用"}
            errors = {}
            for item_kind, fetcher in (("video", self.asoul.fetch_video), ("live", self.asoul.fetch_live)):
                try:
                    payload[item_kind] = await fetcher(uid)
                except Exception as exc:
                    payload[item_kind] = {"error": type(exc).__name__}
                    errors[item_kind] = type(exc).__name__
            if uid in business_config.settings.asoul_bili_comment_target_uids:
                try:
                    dynamics = await self.asoul.fetch_dynamics(uid)
                    resource = self.asoul.latest_comment_resource(dynamics)
                    payload["comment"] = await self.asoul.fetch_hot_comments(resource) if resource else []
                except Exception as exc:
                    payload["comment"] = {"error": type(exc).__name__}
                    errors["comment"] = type(exc).__name__
            else:
                payload["comment"] = "该 UID 未配置为评论目标。"
            visible = {key: value for key, value in payload.items() if key not in errors}
            return ToolResult("ok", json.dumps(visible, ensure_ascii=False, default=str), {"result": payload, "errors": errors})
        elif kind == "video":
            payload = await self.asoul.fetch_video(uid)
        elif kind == "comment":
            if uid not in business_config.settings.asoul_bili_comment_target_uids:
                return ToolResult("denied", "该 UID 不在已配置评论目标中。")
            dynamics = await self.asoul.fetch_dynamics(uid)
            resource = self.asoul.latest_comment_resource(dynamics)
            payload = await self.asoul.fetch_hot_comments(resource) if resource else []
        elif kind == "dump_live":
            payload = {"path": str(await self.asoul.dump_live_payload(uid))}
        elif kind == "live":
            payload = await self.asoul.fetch_live(uid)
        else:
            return ToolResult("clarification", "没有这个B站诊断项目。")
        return ToolResult("ok", json.dumps(payload, ensure_ascii=False, default=str), {"result": payload})

    def _collect_message(self, event_id: str, group_id: int, user_id: int, nickname: str, now: datetime) -> bool:
        if self.store.get_setting("stats_realtime_enabled", True):
            return self.db.record_message(event_id, group_id, user_id, nickname, now)
        # Keep the existing event identity even while counting is stopped;
        # archives, activity weighting and passive interaction still consume it.
        with self.db.connect() as conn:
            return bool(conn.execute("INSERT OR IGNORE INTO event_dedup(event_id,received_at) VALUES (?,?)",
                                     (event_id, utc_now())).rowcount)

    async def collect(self, event: InboundEvent, *, passive: bool = False) -> ToolResult | None:
        if event.group_id is None:
            return None
        group = event.group_id
        now = self._now(event.timestamp)
        # A delayed event must not resurrect a group that the bot has left.
        # Re-activation is performed only by the authoritative group-list
        # reconciliation in Runtime._external_worker.
        existing = self.db.managed_group(group, include_disabled=True)
        if existing is not None and not bool(existing["enabled"]):
            return None
        self.domains.ensure_group(group, joined_at=now.isoformat(), reactivate=False)
        inserted = self._collect_message(f"{group}:{event.event_id}", group, event.user_id, event.nickname, now)
        if not inserted:
            return None
        self.wife.record_activity(f"{group}:{event.event_id}", group, event.user_id, now)
        if not self.blocked(event):
            self.history.insert_group_message(group_id=group, user_id=event.user_id, nickname=event.nickname,
                text=event.text, message_id=event.event_id, created_at=now.isoformat())
        if self.blocked(event) or event.user_id == event.self_id or event.text.lstrip().startswith("#") or game_prefix(event.text):
            return None
        if any(part.get("type") == "at" and str(part.get("data", {}).get("qq")) == str(event.self_id) for part in event.segments):
            return None
        if not passive or not self.domains.feature_enabled(group, "passive_interaction"):
            return None
        if (self._now() - now).total_seconds() > 120:
            return None
        text = event.text.strip()
        repeatable = 1 <= len(text) <= 80 and all(part.get("type") == "text" for part in event.segments)
        config = self.store.get_setting(f"passive:{group}", {})
        last = self.store.get_setting(f"passive_state:{group}", {"messages": 0, "last_repeat": 0, "last_reaction": 0})
        last["messages"] += 1
        current = time.time()
        actions = []
        if config.get("reaction_enabled") and current - last["last_reaction"] >= config.get("reaction_cooldown", 300) and random.random() < config.get("reaction_probability", 0.02):
            actions.append({"action": "set_msg_emoji_like", "params": {"message_id": int(event.event_id), "emoji_id": str(random.choice(config.get("emoji_ids", ["128077"]))), "set": True}})
            last["last_reaction"] = current
        triple = repeatable and self.repeat.observe(group, text)
        repeat = repeatable and config.get("repeat_enabled") and current - last["last_repeat"] >= config.get("repeat_cooldown", 300) and last["messages"] >= config.get("repeat_interval", 20) and random.random() < config.get("repeat_probability", 0.01)
        send_triple = triple and config.get("triple_enabled") and random.random() < config.get("triple_probability", 1)
        if repeat and not send_triple:
            last["last_repeat"], last["messages"] = current, 0
        self.store.set_setting(f"passive_state:{group}", last)
        if send_triple or repeat:
            return ToolResult("ok", text, {"passive": True, "actions": actions}, [_text(text)])
        return ToolResult("ok", "", {"passive": True, "actions": actions}) if actions else None

    async def collect_outgoing(self, event: InboundEvent, text: str, message_id: str | int) -> None:
        if event.group_id is None or not message_id:
            return
        now = self._now()
        existing = self.db.managed_group(event.group_id, include_disabled=True)
        if existing is not None and not bool(existing["enabled"]):
            return
        self.domains.ensure_group(event.group_id, joined_at=now.isoformat(), reactivate=False)
        self._collect_message(f"{event.group_id}:{message_id}", event.group_id, event.self_id,
                               str(self.store.get_setting("persona_name", "达妮娅")), now)
        self.history.insert_group_message(group_id=event.group_id, user_id=event.self_id,
            nickname=str(self.store.get_setting("persona_name", "达妮娅")), text=text,
            message_id=message_id, created_at=now.isoformat())

    async def poll_external(self, group_ids: tuple[int, ...] | list[int] | None = None) -> None:
        """Run in a separate bounded background owner, never on the event path."""
        current = time.monotonic()
        if self.guard.enabled and current - self._last_external.get("guard", -1000) >= self.guard.refresh_minutes * 60:
            self._last_external["guard"] = current
            await self.guard.refresh_and_apply()
        self._bili_config()
        targets = self._groups("bilibili", group_ids)
        if targets and business_config.settings.asoul_bili_enabled and current - self._last_external.get("bili", -1000) >= business_config.settings.asoul_bili_poll_interval_seconds:
            self._last_external["bili"] = current
            for text in await self.asoul.poll_updates():
                item = {"text": text, "kind": "text", "data": None}
                for kind in ("live", "dynamic", "video", "comment"):
                    details = getattr(self.asoul, f"{kind}_notification_details")(text)
                    if details:
                        item = {"text": text, "kind": kind, "data": details}
                        break
                update_id = uuid4().hex
                with self.db.connect() as conn:
                    conn.execute("INSERT INTO harness_bili_updates VALUES(?,?,?,?,?)",
                        (update_id, text, item["kind"], json.dumps(item["data"], ensure_ascii=False), json.dumps(targets)))

    def _groups(self, feature: str, group_ids: tuple[int, ...] | list[int] | None) -> tuple[int, ...]:
        scope = set(self.domains.all_group_ids() if group_ids is None else group_ids)
        return tuple(group for group in self.domains.enabled_groups(feature) if group in scope)

    def _queued(self, key: str) -> bool:
        with self.db.connect() as conn:
            return conn.execute("SELECT 1 FROM harness_pending_outputs WHERE delivery_key=?", (key,)).fetchone() is not None

    def _queue_output(self, group: int, key: str, result: ToolResult, now: datetime, *, immediate: bool = False) -> dict[str, Any]:
        result.data.setdefault("delivery", {})["key"] = key
        with self.db.connect() as conn:
            conn.execute("INSERT INTO harness_pending_outputs VALUES(?,?,?,?,0)",
                (key, group, json.dumps(asdict(result), ensure_ascii=False, default=str), now.timestamp() + (0 if immediate else 30)))
        return {"group_id": group, "result": result}

    async def _finish_bili_login(self, login: Any, event: InboundEvent) -> None:
        try:
            success = await self.asoul.wait_for_qr_login(login)
            status = "ok" if success else "clarification"
            text = "B站扫码登录成功，登录态已保存到新系统。" if success else "B站二维码已过期，请重新发起扫码登录。"
        except Exception as exc:
            success, text = False, f"B站扫码登录失败：{type(exc).__name__}。请重新发起扫码登录。"
            status = "error"
            self.corrections.record_failure(skill_id="bili_login", error=str(exc),
                                            user_id=event.user_id, source="bili_qr_login")
        key = "bili_login:" + event.key
        result = ToolResult(status, text,
            {"delivery": {"type": "bili_login", "user_id": event.user_id, "self_id": event.self_id}}, [_text(text)])
        self._queue_output(0, key, result, self._now(), immediate=True)

    async def _notification_segments(self, text: str, kind: str, details: dict | None) -> list[dict]:
        caption = str(details.get("url") or text) if kind in {"dynamic", "video"} and details else text
        if not details or not self.store.get_setting("bili_render_cards", True):
            return [_text(caption)]
        try:
            path = await self.asoul_web.render_notification(text, **{kind: details})
        except Exception:
            kwargs = {kind: details} if kind in {"live", "dynamic", "video"} else {}
            path = await self.asoul_renderer.render_bilibili_notification(text, **kwargs)
        return [_image(path), _text("\n" + caption)]

    async def tick(self, now: datetime | None = None, group_ids: tuple[int, ...] | list[int] | None = None,
                   user_ids: tuple[int, ...] | list[int] | None = None) -> list[dict[str, Any]]:
        now = now or self._now()
        active_groups = set(self.domains.all_group_ids())
        scope = active_groups if group_ids is None else active_groups.intersection(group_ids)
        cutoff = float(self.store.get_setting("import_cutoff", 0))
        pending = []
        with self.db.connect() as conn:
            for row in conn.execute("SELECT * FROM harness_pending_outputs WHERE next_attempt<=?", (now.timestamp(),)).fetchall():
                result = ToolResult(**json.loads(row["result_json"]))
                delivery = result.data.get("delivery", {})
                if delivery.get("type") == "bili_login" and (not user_ids or delivery["user_id"] in user_ids):
                    pending.append({"group_id": None, "user_id": delivery["user_id"], "self_id": delivery["self_id"], "result": result})
                    conn.execute("UPDATE harness_pending_outputs SET next_attempt=? WHERE delivery_key=?", (now.timestamp() + 30, row["delivery_key"]))
                elif row["group_id"] in scope:
                    feature = {"wife": "today_wife", "hourly": "hourly", "ranking": "speech_ranking_push", "bili": "bilibili"}.get(row["delivery_key"].split(":", 1)[0])
                    if feature and not self.domains.feature_enabled(row["group_id"], feature):
                        continue
                    pending.append({"group_id": row["group_id"], "result": result})
                    conn.execute("UPDATE harness_pending_outputs SET next_attempt=? WHERE delivery_key=?", (now.timestamp() + 30, row["delivery_key"]))
        self.guard.apply_due(now)
        for group in self._groups("mini_games", group_ids):
            for outcome in self.games.expire_due(now, group_id=group) + self.games.trigger_due_random_events(now, group_id=group):
                if outcome.announce:
                    pending.append({"group_id": outcome.group_id, "result": await self._game_result(outcome)})
        for group in self._groups("today_wife", group_ids):
            for number, start, end in ((1, clock_time(11, 30), clock_time(12)), (2, clock_time(17, 30), clock_time(18)), (3, clock_time(23), clock_time(23, 30))):
                seconds = int((datetime.combine(now.date(), end) - datetime.combine(now.date(), start)).total_seconds())
                offset = int.from_bytes(hashlib.sha256(f"{now.date()}:{group}:{number}".encode()).digest()[:4], "big") % max(1, seconds - 60)
                due = datetime.combine(now.date(), start, now.tzinfo) + timedelta(seconds=offset)
                key = f"wife:{group}:{now.date()}:{number}"
                if due.timestamp() >= cutoff and due <= now < datetime.combine(now.date(), end, now.tzinfo) and not self._delivered(key) and not self._queued(key) and not self.wife_game.collective_round_delivered(group, number, now):
                    payload = self.wife_game.prepare_collective_round(group, number, now)
                    if payload:
                        path = await asyncio.to_thread(self.game_renderer.render_today_wife_collective_round, payload)
                        pending.append(self._queue_output(group, key, ToolResult("ok", "今日缘分集体互动。", {"delivery": {"type": "wife_round", "group_id": group, "round": number, "at": now.isoformat()}}, [_image(path)]), now))
        if self.store.get_setting("hourly_enabled", False) and now.minute < 5:
            start, end = self.store.get_setting("hourly_start", "08:00"), self.store.get_setting("hourly_end", "23:00")
            clock = now.strftime("%H:%M")
            in_window = start <= clock <= end if start <= end else clock >= start or clock <= end
            period = "morning" if 6 <= now.hour < 10 else "daytime" if 10 <= now.hour < 17 else "evening" if 17 <= now.hour < 22 else "night"
            send_hour = period != "night" or now.hour in {0, 2, 4, 22, 23}
            if in_window and send_hour and now.replace(minute=0, second=0, microsecond=0).timestamp() >= cutoff:
                for group in self._groups("hourly", group_ids):
                    key = f"hourly:{group}:{now:%Y-%m-%d-%H}"
                    if not self._delivered(key) and not self._queued(key):
                        recent = [*self.store.get_setting(f"hourly_recent:{group}", []), *self.db.recent_hourly_text_indices(group, 24)][:24]
                        generated = self.hourly.compose(period, recent)
                        pending.append(self._queue_output(group, key, ToolResult("ok", generated.text,
                            {"delivery": {"hourly_group": group, "fingerprint": generated.fingerprint}}, [_text(generated.text)]), now))
        if now.time() >= clock_time(23, 50) or now.time() <= clock_time(0, 30, 59):
            day = now.date() if now.hour >= 23 else now.date() - timedelta(days=1)
            # Render one aggregate payload per private cluster and deliver the
            # same image to every enabled member group.  Previously each
            # member was queried independently, so a cluster's 23:50 notice
            # accidentally contained five separate single-group rankings.
            cluster_payloads: dict[int, tuple[Path, str]] = {}
            for group in self._groups("speech_ranking_push", group_ids):
                key = f"ranking:{group}:{day}"
                due = datetime.combine(day, clock_time(23, 50), now.tzinfo)
                if due.timestamp() >= cutoff and not self._delivered(key) and not self._queued(key):
                    domain = self.domains.domain_for_group(group)
                    is_cluster = bool(domain and domain.mode == "cluster")
                    if is_cluster:
                        cache_key = domain.domain_id
                        cached = cluster_payloads.get(cache_key)
                        if cached is None:
                            groups = self.domains.ranking_group_ids(group, cluster=True)
                            rows = self.stats.ranking_rows_for_groups("day", groups, today=day)
                            payload = await self.web.ranking_data(
                                group, "day", cluster=True, rows=rows, day=day
                            )
                            path = await self.community_renderer.render_ranking(payload)
                            cached = (path, payload.get("group_label", "当前集群"))
                            cluster_payloads[cache_key] = cached
                        path, label = cached
                        result = ToolResult("ok", f"{label}集群每日发言榜。", {}, [_image(path)])
                    else:
                        rows = self.stats.ranking_rows_for_groups("day", (group,), today=day)
                        payload = await self.web.ranking_data(group, "day", rows=rows, day=day)
                        path = await self.community_renderer.render_ranking(payload)
                        result = ToolResult("ok", "每日发言榜。", {}, [_image(path)])
                    pending.append(self._queue_output(group, key, result, now))
        with self.db.connect() as conn:
            updates = [dict(row) for row in conn.execute("SELECT * FROM harness_bili_updates")]
        enabled = set(self._groups("bilibili", group_ids))
        for item in updates:
            targets = json.loads(item["targets_json"])
            groups = [group for group in targets if group in enabled and not self._delivered(f"bili:{group}:{item['update_id']}") and not self._queued(f"bili:{group}:{item['update_id']}")]
            if not groups:
                continue
            segments = await self._notification_segments(item["text"], item["kind"], json.loads(item["data_json"]))
            for group in groups:
                message = list(segments)
                role_error = ""
                if item["text"].startswith("【开播】"):
                    try:
                        member = await self.platform.member_info(group, int(self.gateway.self_id))
                        if member.get("role") in {"owner", "admin"}:
                            message = [{"type": "at", "data": {"qq": "all"}}, _text(" "), *message]
                    except Exception as exc:
                        role_error = type(exc).__name__
                key = f"bili:{group}:{item['update_id']}"
                data = {"delivery": {"bili_update": item["update_id"]}}
                if role_error:
                    data["atall_validation_error"] = role_error
                pending.append(self._queue_output(group, key, ToolResult("ok", item["text"], data, message), now))
        return pending

    def _delivered(self, key: str) -> bool:
        with self.db.connect() as conn:
            row = conn.execute("SELECT delivered FROM harness_deliveries WHERE delivery_key=?", (key,)).fetchone()
        return bool(row and row[0])

    def mark_delivered(self, result: ToolResult, success: bool) -> None:
        delivery = result.data.get("delivery", {})
        if delivery.get("type") == "wife_round":
            self.wife_game.mark_collective_round_delivery(delivery["group_id"], delivery["round"],
                datetime.fromisoformat(delivery["at"]), error="send_failed" if not success else "")
        if delivery.get("key") and success:
            with self.db.connect() as conn:
                conn.execute("INSERT INTO harness_deliveries VALUES(?,1,?) ON CONFLICT(delivery_key) DO UPDATE SET delivered=1",
                             (delivery["key"], self._now().isoformat()))
                conn.execute("DELETE FROM harness_pending_outputs WHERE delivery_key=?", (delivery["key"],))
            if "hourly_group" in delivery:
                group = delivery["hourly_group"]
                recent = [delivery["fingerprint"], *self.store.get_setting(f"hourly_recent:{group}", [])][:24]
                self.store.set_setting(f"hourly_recent:{group}", recent)
            update_id = delivery.get("bili_update")
            if update_id:
                with self.db.connect() as conn:
                    row = conn.execute("SELECT targets_json FROM harness_bili_updates WHERE update_id=?", (update_id,)).fetchone()
                if row and all(self._delivered(f"bili:{group}:{update_id}") for group in json.loads(row[0])):
                    with self.db.connect() as conn:
                        conn.execute("DELETE FROM harness_bili_updates WHERE update_id=?", (update_id,))
        elif delivery.get("key"):
            with self.db.connect() as conn:
                row = conn.execute("SELECT attempts FROM harness_pending_outputs WHERE delivery_key=?", (delivery["key"],)).fetchone()
                if row:
                    attempts = row[0] + 1
                    conn.execute("UPDATE harness_pending_outputs SET attempts=?,next_attempt=? WHERE delivery_key=?",
                        (attempts, time.time() + min(900, 30 * 2 ** min(attempts, 5)), delivery["key"]))

    async def close(self) -> None:
        for task in self._qr_tasks:
            task.cancel()
        if self._qr_tasks:
            await asyncio.gather(*self._qr_tasks, return_exceptions=True)
        await self.asoul_web.close()
        await self.community_renderer.close()
