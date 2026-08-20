import asyncio

from PIL import Image
import nonebot

nonebot.init()

from bot.plugins import commands
from bot.services.reports import ReportRenderer


def test_user_help_text_contains_only_copyable_public_commands():
    text = commands.user_help_text()

    assert text.splitlines()[0] == "#帮助"
    assert "#帮助文字" in text
    assert "#活动大厅" in text
    assert "#查看获奖名单 <活动ID>" in text
    assert "#俄罗斯转盘榜单" in text
    assert "#装弹成语 [专业/娱乐] [60-600]" in text
    assert "#群老婆" in text
    assert "#强取 @群友" in text
    assert "#我的老婆" in text
    assert "#解缘" in text
    assert "枝江百科" not in text
    assert "A魂百科" not in text
    assert "[-a]" not in text
    assert "#创建活动" not in text
    assert "#查重" not in text
    assert "说明" not in text


def test_user_help_sections_exclude_privileged_operations():
    sections = commands.user_help_sections()
    contents = "\n".join(command for _, command, _ in sections)

    assert "创建活动" not in contents
    assert "查重" not in contents
    assert [heading for heading, _, _ in commands.user_help_categories()] == [
        "使用说明", "直播与日程", "活动功能", "今日老婆", "小游戏", "小游戏榜单", "A 海岸发言统计",
    ]
    assert any(title == "活动参与" for title, _, _ in sections)


def test_user_help_merges_equivalent_commands_and_game_rankings():
    categories = {heading: entries for heading, _, entries in commands.user_help_categories()}
    live_entries = dict((title, command) for title, command, _ in categories["直播与日程"])
    ranking_entries = dict((title, command) for title, command, _ in categories["小游戏榜单"])
    game_entries = dict((title, command) for title, command, _ in categories["小游戏"])
    today_wife_entries = dict((title, command) for title, command, _ in categories["今日老婆"])

    assert live_entries["直播日程"] == "#枝江直播 / #直播日程 / #本周直播"
    assert ranking_entries["群游戏榜单"] == "#转盘榜 / #炸弹榜 / #骰子榜 / #猜数榜"
    assert ranking_entries["总游戏榜单"] == "#转盘总榜 / #炸弹总榜 / #骰子总榜 / #猜数总榜"
    assert "缘分档案" not in game_entries
    assert today_wife_entries["今日缘分"] == "#今日老婆 / #今日缘分 / #强取 @群友 / #互动 靠近|倾听|回应|修复|助攻 [@群友]"
    assert today_wife_entries["缘分档案"] == "#我的缘分 / #群缘分 / #群缘分 历史 / #离婚"


def test_user_help_hides_asoul_third_party_help_and_attributes_live_schedule():
    categories = {heading: (note, entries) for heading, note, entries in commands.user_help_categories()}

    note, entries = categories["直播与日程"]
    assert note == "本功能由爱驼提供技术支持"
    assert all("A魂帮助" not in command and "bot帮助" not in command for _, command, _ in entries)
    assert "#A魂帮助" not in commands.user_help_text()


def test_user_help_sections_render_as_a_local_image(tmp_path):
    renderer = ReportRenderer(tmp_path)

    path = renderer.render_user_help(
        "普通用户帮助",
        "按功能分类；文字版请发送 #帮助文字",
        commands.user_help_categories(),
    )

    with Image.open(path) as image:
        assert image.width == ReportRenderer.WIDTH
        assert image.height > ReportRenderer.HEADER_HEIGHT
        assert image.getbbox() is not None


def test_user_help_text_is_one_folded_forward_node():
    nodes = commands.user_help_text_forward_nodes(2120682836)

    assert len(nodes) == 1
    assert nodes[0]["data"]["name"] == "普通用户帮助文字 第1页"
    assert nodes[0]["data"]["content"] == [
        {"type": "text", "data": {"text": commands.user_help_text()}}
    ]


def test_user_help_image_reply_contains_no_text_segment(tmp_path):
    image_path = tmp_path / "help.png"
    image_path.touch()

    message = commands.user_help_image_message(image_path)

    assert message.type == "image"


def test_successful_user_help_image_has_one_response(monkeypatch, tmp_path):
    image_path = tmp_path / "help.png"
    image_path.touch()
    responses = []

    class Matcher:
        async def finish(self, message):
            responses.append(message)

    monkeypatch.setattr(commands.report_renderer, "render_user_help", lambda *_: image_path)
    asyncio.run(commands.send_user_help_image(Matcher()))

    assert len(responses) == 1
    assert responses[0].type == "image"
