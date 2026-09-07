import asyncio
from types import SimpleNamespace

import nonebot
import pytest

nonebot.init()

from bot.plugins import commands


def test_user_help_text_contains_only_copyable_public_commands():
    text = commands.user_help_text()

    assert text.splitlines()[0] == "#帮助"
    assert "#帮助文字" in text
    assert "@糖糖 帮我看看今天能玩什么" in text
    assert "#nte帮助" in text
    assert "#nte薄荷排行" in text
    assert "#nte薄荷总排行" in text
    assert "#ww帮助" in text
    assert "#ww完整帮助" in text
    assert "#ww今汐评分总排行" in text
    assert "#群设置" in text
    assert "#开关NTE" in text
    assert "#开关鸣潮" in text
    assert "#活动大厅" not in text
    assert "#查看获奖名单" not in text
    assert "#俄罗斯转盘榜单" in text
    assert "#装弹成语 [专业/娱乐] [60-600]" in text
    assert "#群老婆" in text
    assert "#强取 @群友" in text
    assert "#我的老婆" in text
    assert "#解缘" in text
    assert "#个人发言统计" not in text
    assert "#个人发言榜" not in text
    assert "#我的发言榜" not in text
    assert "枝江百科" not in text
    assert "A魂百科" not in text
    assert "[-a]" not in text
    assert "#创建活动" not in text
    assert "#查重" not in text
    assert "#机器人状态" not in text
    assert "#系统设置" not in text
    assert "#公告面板" not in text
    assert "#管理员帮助" not in text
    assert "说明" not in text


def test_user_help_sections_exclude_privileged_operations():
    sections = commands.user_help_sections()
    contents = "\n".join(command for _, command, _ in sections)

    assert "创建活动" not in contents
    assert "查重" not in contents
    assert "机器人状态" not in contents
    assert [heading for heading, _, _ in commands.user_help_categories()] == [
        "使用说明",
        "聊天互动",
        "NTE 查询与排行",
        "鸣潮查询与排行",
        "直播与日程",
        "今日老婆",
        "小游戏",
        "小游戏榜单",
        "发言统计",
        "本群设置",
        "群内自动功能",
    ]
    assert all("活动" not in title for title, _, _ in sections)
    assert "#系统设置" not in contents
    assert "#公告面板" not in contents
    assert "#管理员帮助" not in contents


def test_robot_status_is_super_admin_only(monkeypatch):
    responses: list[str] = []

    class Finished(Exception):
        pass

    class Matcher:
        async def finish(self, message):
            responses.append(str(message))
            raise Finished

    monkeypatch.setattr(commands, "is_super_admin", lambda user_id: False)

    with pytest.raises(Finished):
        asyncio.run(
            commands.send_robot_status(
                Matcher(),
                SimpleNamespace(user_id=42),
            )
        )

    assert responses == ["只有超级管理员可以查看机器人状态。"]


def test_user_help_merges_equivalent_commands_and_game_rankings():
    categories = {heading: entries for heading, _, entries in commands.user_help_categories()}
    live_entries = dict((title, command) for title, command, _ in categories["直播与日程"])
    ranking_entries = dict((title, command) for title, command, _ in categories["小游戏榜单"])
    game_entries = dict((title, command) for title, command, _ in categories["小游戏"])
    today_wife_entries = dict((title, command) for title, command, _ in categories["今日老婆"])
    nte_entries = dict((title, command) for title, command, _ in categories["NTE 查询与排行"])
    wuwa_entries = dict((title, command) for title, command, _ in categories["鸣潮查询与排行"])
    stats_entries = dict((title, command) for title, command, _ in categories["发言统计"])
    group_admin_entries = dict((title, command) for title, command, _ in categories["本群设置"])

    assert live_entries["直播日程"] == "#枝江直播 / #直播日程 / #本周直播"
    assert nte_entries["异环帮助"] == "#nte帮助 / nte帮助"
    assert nte_entries["当前群排行"] == "#nte薄荷排行 / #nte最强排行"
    assert nte_entries["机器人总排行"] == "#nte薄荷总排行 / #nte最强总排行"
    assert wuwa_entries["鸣潮帮助"] == "#ww帮助 / #ww完整帮助"
    assert "#ww练度排行" in wuwa_entries["当前群排行"]
    assert "#ww今汐评分排行" in wuwa_entries["当前群排行"]
    assert "#ww练度总排行" in wuwa_entries["机器人总排行"]
    assert ranking_entries["群游戏榜单"] == "#转盘榜 / #炸弹榜 / #骰子榜 / #猜数榜"
    assert ranking_entries["域游戏榜单"] == "#转盘总榜 / #炸弹总榜 / #骰子总榜 / #猜数总榜"
    assert "缘分档案" not in game_entries
    assert today_wife_entries["今日缘分"] == "#今日老婆 / #今日缘分 / #强取 @群友"
    assert today_wife_entries["缘分档案"] == "#我的缘分 / #群缘分 / #群缘分 历史 / #离婚"
    assert "个人发言统计" not in stats_entries
    assert stats_entries["当前群发言排行"].startswith("#发言排行")
    assert stats_entries["当前集群发言排行"].startswith("#集群发言排行")
    assert group_admin_entries["本群功能状态"] == "#群设置 / #本群设置"
    assert "#群设置 <功能> 开|关" in group_admin_entries["本群功能开关"]


def test_user_help_hides_asoul_third_party_help_and_attributes_live_schedule():
    categories = {heading: (note, entries) for heading, note, entries in commands.user_help_categories()}

    note, entries = categories["直播与日程"]
    assert note == "本功能由爱驼提供技术支持"
    assert all("A魂帮助" not in command and "bot帮助" not in command for _, command, _ in entries)
    assert "#A魂帮助" not in commands.user_help_text()


def test_user_help_points_to_the_interactive_short_link():
    responses = []

    class Matcher:
        async def finish(self, message):
            responses.append(message)

    asyncio.run(commands.send_user_help_image(Matcher()))

    assert responses == ["帮助在线：short.example.invalid/h"]


def test_user_help_text_is_one_folded_forward_node():
    nodes = commands.user_help_text_forward_nodes(2120682836)

    assert len(nodes) == 1
    assert nodes[0]["data"]["name"] == "普通用户帮助文字 第1页"
    assert nodes[0]["data"]["content"] == [
        {"type": "text", "data": {"text": commands.user_help_text()}}
    ]
