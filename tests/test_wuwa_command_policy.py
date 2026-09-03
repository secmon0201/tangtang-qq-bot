from bot.services.wuwa_command_policy import (
    ROVER_REMINDER_DISABLED_MESSAGE,
    is_disabled_rover_reminder_command,
)


def test_rover_reminder_commands_are_intercepted_for_all_supported_prefixes():
    for command in (
        "#ww开启推送",
        "ww 关闭体力推送",
        "WW推送邮箱 123@qq.com",
        "ww推送邮箱123@qq.com",
        "#WW 体力推送邮箱 test@example.com",
        "ww推送阈值 180",
        "ww推送阈值180",
        "#ww 体力阈值 200",
        "#ww体力推送阈值 230",
    ):
        assert is_disabled_rover_reminder_command(command), command


def test_rover_reminder_interceptor_does_not_capture_other_wuwa_commands():
    for command in (
        "#ww体力",
        "#ww签到",
        "#ww分析帮助",
        "#ww开启自动签到",
        "普通聊天",
    ):
        assert not is_disabled_rover_reminder_command(command), command


def test_rover_reminder_disabled_reply_states_no_side_effects():
    assert "已关闭" in ROVER_REMINDER_DISABLED_MESSAGE
    assert "不会保存邮箱" in ROVER_REMINDER_DISABLED_MESSAGE
    assert "不会" in ROVER_REMINDER_DISABLED_MESSAGE and "发送邮件" in ROVER_REMINDER_DISABLED_MESSAGE
