from bot.services.whitelist_menu import (
    build_whitelist_menu_text,
    whitelist_menu_sections,
)


def test_whitelist_menu_fallback_contains_all_copyable_operations():
    text = build_whitelist_menu_text("#")

    assert "#白名单 列表" in text
    assert "#白名单 添加 QQ号 [备注]" in text
    assert "#白名单 删除 QQ号" in text
    assert "群内和私聊均使用 # 指令" in text
    assert "@机器人" not in text


def test_whitelist_menu_sections_are_suitable_for_local_image_menu():
    sections = whitelist_menu_sections("")

    assert [section[0] for section in sections] == ["查看列表", "添加用户", "删除用户"]
    assert all(section[1].startswith("白名单 ") for section in sections)
