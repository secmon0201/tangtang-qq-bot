"""Mature public and administrator help templates without NoneBot imports."""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .business.community_web import public_help_categories


def user_help_categories(prefix: str = "#", *, stats_enabled: bool = True) -> list[tuple[str, str, list[tuple[str, str, str]]]]:
    """Group the visual manual by user workflow and merge equivalent commands."""
    return public_help_categories(
        prefix, stats_enabled=stats_enabled
    )


def user_help_text(prefix: str = "#", *, stats_enabled: bool = True) -> str:
    """Return every copyable public command, including compatibility aliases."""
    commands = [
        f"{prefix}帮助", f"{prefix}帮助文字",
        "@娅娅 帮我查一下今天发言排行", "娅娅 帮我看一下今日直播日程",
        "游戏命令明确使用 #nte 或 #ww 前缀，直接转发上游；群内自然语言本地功能需呼叫娅娅或 @机器人。",
        f"{prefix}枝江直播 [状态]", f"{prefix}直播日程 [状态]",
        f"{prefix}今日直播", f"{prefix}明日直播", f"{prefix}本周直播",
        f"{prefix}nte帮助", "nte帮助", f"{prefix}nte登录", f"{prefix}nte查询", f"{prefix}nte刷新面板",
        f"{prefix}nte薄荷排行", f"{prefix}nte薄荷总排行", f"{prefix}nte最强排行", f"{prefix}nte最强总排行",
        f"{prefix}ww帮助", "ww帮助", f"{prefix}ww完整帮助", f"{prefix}ww登录", f"{prefix}ww刷新面板", f"{prefix}ww体力",
        f"{prefix}ww今汐评分排行", f"{prefix}ww今汐评分总排行", f"{prefix}ww今汐声骸排行", f"{prefix}ww今汐声骸总排行",
        f"{prefix}ww练度排行", f"{prefix}ww练度总排行", f"{prefix}ww最强排行", f"{prefix}ww最强总排行",
        f"{prefix}游戏列表", f"{prefix}小游戏列表", f"{prefix}装填", f"{prefix}开枪",
        f"{prefix}装弹", f"{prefix}丢给 @成员", f"{prefix}装弹成语 [专业/娱乐] [60-600]",
        f"四字词 {prefix}丢给 @成员", f"{prefix}骰子", f"{prefix}猜数", f"{prefix}猜 <0-999>",
        f"{prefix}今日老婆", f"{prefix}今日缘分", f"{prefix}强取 @群友", f"{prefix}我的缘分", f"{prefix}我的老婆", f"{prefix}群缘分", f"{prefix}群老婆", f"{prefix}离婚", f"{prefix}解缘",
        f"{prefix}转盘榜", f"{prefix}转盘总榜", f"{prefix}俄罗斯转盘榜单",
        f"{prefix}俄罗斯转盘总榜单", f"{prefix}炸弹榜", f"{prefix}炸弹总榜",
        f"{prefix}定时炸弹榜单", f"{prefix}定时炸弹总榜单", f"{prefix}骰子榜",
        f"{prefix}骰子总榜", f"{prefix}幸运骰局榜单", f"{prefix}幸运骰局总榜单",
        f"{prefix}猜数榜", f"{prefix}猜数总榜", f"{prefix}猜数字榜单", f"{prefix}猜数字总榜单",
        f"{prefix}群设置", f"{prefix}本群设置", f"{prefix}群设置 <功能> 开|关",
        f"{prefix}开关小游戏", f"{prefix}开关今日老婆", f"{prefix}开关准时报点",
        f"{prefix}开关被呼叫会话", f"{prefix}开关B站推送", f"{prefix}开关被动互动", f"{prefix}开关NTE", f"{prefix}开关鸣潮",
        f"{prefix}群设置 代称 <名称>", f"{prefix}群设置 过滤 列表",
        f"{prefix}群设置 过滤 添加 QQ号", f"{prefix}群设置 过滤 移除 QQ号",
    ]
    if stats_enabled:
        commands.extend((
            f"{prefix}发言排行 [日/周/月/总]", f"{prefix}发言榜 [日/周/月/总]", f"{prefix}发言统计 [日/周/月/总]", f"{prefix}统计 [日/周/月/总]",
            f"{prefix}集群发言排行 [日/周/月/总]", f"{prefix}集群发言榜 [日/周/月/总]", f"{prefix}集群发言统计 [日/周/月/总]", f"{prefix}集群统计 [日/周/月/总]",
            f"{prefix}<集群名>发言排行 [日/周/月/总]", f"{prefix}<集群名>发言统计 [日/周/月/总]",
            f"{prefix}发言记录 <QQ号|@成员> [页码]", f"{prefix}发言搜索 <QQ号|@成员> <关键词> [页码]",
            f"{prefix}发言画像 <QQ号|@成员>", f"{prefix}画像 <QQ号|@成员>",
        ))
    return "\n".join(commands)


def admin_help_page_text(title: str, sections: list[tuple[str, str, str]]) -> str:
    return title + "\n\n" + "\n\n".join(
        f"【{heading}】\n{commands}\n说明：{note}".strip()
        for heading, commands, note in sections
    )


def super_admin_help_pages(group_count: int, prefix: str = "#") -> list[tuple[str, str, list[tuple[str, str, str]]]]:
    """Build the complete, current super-admin manual as small readable pages."""
    return [
        (
            "超级管理员手册 1/4｜权限与本群设置",
            "群主、群管理员与超级管理员的职责边界",
            [
                (
                    "本群机器人管理员",
                    f"{prefix}群设置\n{prefix}群设置 代称 <名称>\n{prefix}群设置 <功能> 开|关",
                    "群主和 QQ 群管理员自动拥有本群机器人管理权限；超级管理员也可管理当前群。群设置只影响本群，不改变集群统计成员关系。",
                ),
                (
                    "功能开关",
                    f"{prefix}开关小游戏\n{prefix}开关今日老婆\n{prefix}开关准时报点\n{prefix}开关被呼叫会话\n{prefix}开关B站推送\n{prefix}开关被动互动\n{prefix}开关NTE\n{prefix}开关鸣潮",
                    "新独群默认开启被动指令能力，主动推送默认关闭；加入集群时全部功能开启，之后仍由本群管理员分别开关。",
                ),
                (
                    "本群过滤",
                    f"{prefix}群设置 过滤 列表\n{prefix}群设置 过滤 添加 QQ号\n{prefix}群设置 过滤 移除 QQ号",
                    "过滤名单只作用于当前群，不扩散到同一集群的其他群。",
                ),
            ],
        ),
        (
            "超级管理员手册 2/4｜系统与集群",
            "只在私聊开放的全局管理能力",
            [
                (
                    "集群维护",
                    f"{prefix}系统设置 集群 列表\n{prefix}系统设置 集群 创建 <名称>\n{prefix}系统设置 集群 邀请 <集群ID> <群号>\n{prefix}系统设置 集群 移除 <群号>\n{prefix}系统设置 集群 解散 <集群ID>",
                    "集群不对群管理员开放。成员变化会立即轮换排行令牌；只能邀请机器人当前实际加入的群。",
                ),
                (
                    "全局运行条件",
                    f"{prefix}系统设置 游戏接口 状态|开|关\n{prefix}系统设置 小游戏 全局 状态|开|关\n{prefix}系统设置 被呼叫会话 状态|开|关\n{prefix}系统设置 糖糖主动聊天 状态|开|关\n{prefix}糖糖模型 状态|<档案名>\n{prefix}系统设置 准时报点 状态|开|关|时段 HH:MM HH:MM",
                    "全局运行条件不会改写各群已保存的开关意图；重新开启后，各群按原状态恢复。",
                ),
                (
                    "当前规模",
                    f"已登记群：{group_count} 个\n群数量不设上限\nSQLite 为运行权威",
                    "新群在机器人首次观察到群消息或同步群列表时自动登记为独群；新配置与迁入旧快照分离管理。",
                ),
            ],
        ),
        (
            "超级管理员手册 3/4｜统计与游戏接口",
            "当前群、当前集群与机器人总榜的明确边界",
            [
                (
                    "发言榜",
                    f"{prefix}发言排行 日|周|月|总\n{prefix}集群发言排行 日|周|月|总",
                    "普通发言榜只统计当前群；集群发言榜只在集群成员群内可用。23:50 本群日榜推送由各群开关控制。",
                ),
                (
                    "发言档案",
                    f"{prefix}发言记录 QQ号|@成员 [页码]\n{prefix}发言搜索 QQ号|@成员 关键词 [页码]\n{prefix}发言画像 QQ号|@成员",
                    "记录和搜索只读取当前群；画像按当前群域读取。历史说明使用糖糖实际加入该群的日期。",
                ),
                (
                    "NTE 排行",
                    f"{prefix}nte薄荷排行\n{prefix}nte薄荷总排行\n{prefix}nte最强排行\n{prefix}nte最强总排行",
                    "帮助、排行与查询原样交给独立 NTE 上游；Harness 只检查本群开关并转发，不接管榜单或改写上游数据。",
                ),
                (
                    "鸣潮排行",
                    f"{prefix}ww今汐排行\n{prefix}ww今汐总排行\n{prefix}ww练度排行\n{prefix}ww练度总排行",
                    "帮助、排行与查询原样交给独立鸣潮上游；Harness 不生成游戏榜单，也不新增私有集群鸣潮榜。",
                ),
            ],
        ),
        (
            "超级管理员手册 4/4｜公告与运维",
            "多选目标公告、查重与本地运行边界",
            [
                (
                    "公告面板",
                    f"{prefix}公告面板\n兼容：{prefix}公告网页",
                    "仅超级管理员可打开。独群、集群和集群成员可同时多选，实际群号在会话创建时冻结并自动去重；文字、图文和原图使用同一目标规则。",
                ),
                (
                    "查重与白名单",
                    f"{prefix}查重1 / {prefix}查重2 / {prefix}查重3 / {prefix}查重4\n{prefix}白名单 菜单|列表|添加|删除",
                    "查重是独立的内部能力，不随公开群功能列表展示；跨群结果只对超级管理员开放。",
                ),
                (
                    "运行边界",
                    "Windows 本地｜TangtangHarness｜OneBot v11｜SnowLuma｜SQLite\nNTE 上游只读",
                    "QQ 登录与验证始终由用户手工完成。新框架改动只重启 TangtangHarness，不重启 SnowLuma/QQ，也不修改 GsUID.Core。",
                ),
            ],
        ),
    ]


def build_forward_nodes(
    page_messages: Sequence[str],
    page_paths: Sequence[Path | None],
    bot_id: int | str,
    title: str = "查重结果",
) -> list[dict[str, Any]]:
    """Build JSON-native OneBot v11 forward nodes.

    Forward nodes are nested inside an API payload.  Keeping their content as
    plain OneBot dictionaries keeps this payload portable: adapter ``Message``
    objects are fine at the top level, but some OneBot implementations do not unwrap
    ``MessageSegment`` instances nested below ``data.content``.
    """
    if len(page_messages) != len(page_paths):
        raise ValueError("page messages and page paths must have the same length")

    nodes: list[dict[str, Any]] = []
    for index, (fallback, path) in enumerate(zip(page_messages, page_paths), 1):
        if path:
            content = [{"type": "image", "data": {"file": path.resolve().as_uri()}}]
        else:
            content = [{"type": "text", "data": {"text": fallback}}]
        nodes.append(
            {
                "type": "node",
                "data": {
                    "name": f"{title} 第{index}页",
                    "uin": str(bot_id),
                    "content": content,
                },
            }
        )
    return nodes

