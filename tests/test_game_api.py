from bot.services.game_api_gate import (
    BARE_GAME_COMMAND_RE,
    BARE_NTE_COMMAND_RE,
    HASH_GAME_COMMAND_RE,
    HASH_OTHER_GAME_COMMAND_RE,
    NTE_GAME_COMMAND_RE,
    game_message_disposition,
)


def test_hash_game_command_regex_accepts_only_the_nte_prefix():
    assert HASH_GAME_COMMAND_RE.match("#nte帮助")
    assert HASH_GAME_COMMAND_RE.match("#nte 帮助")
    assert HASH_GAME_COMMAND_RE.match("#NTE帮助")
    assert HASH_GAME_COMMAND_RE.match("#nte")
    assert not HASH_GAME_COMMAND_RE.match("#ntext")
    assert not HASH_GAME_COMMAND_RE.match("#yh帮助")
    assert not HASH_GAME_COMMAND_RE.match("nte帮助")


def test_bare_game_command_regex_covers_disabled_and_legacy_prefixes():
    assert BARE_GAME_COMMAND_RE.match("gs帮助")
    assert BARE_GAME_COMMAND_RE.match("nte帮助")
    assert BARE_GAME_COMMAND_RE.match("yh公告")
    assert BARE_GAME_COMMAND_RE.match("ww 查询")
    assert BARE_GAME_COMMAND_RE.match("gsuid 查询")
    assert not BARE_GAME_COMMAND_RE.match("#nte帮助")
    assert not BARE_GAME_COMMAND_RE.match("ntext")
    assert not BARE_GAME_COMMAND_RE.match("统计今日")


def test_nte_command_regex_accepts_hash_and_bare_case_variants():
    for command in ("#nte帮助", "nte帮助", "#NTE帮助", "NTE帮助", "#ntebot早雾排名", "nte群最强排行"):
        assert NTE_GAME_COMMAND_RE.match(command)
    assert BARE_NTE_COMMAND_RE.match("nte帮助")
    assert BARE_NTE_COMMAND_RE.match("NTE帮助")
    assert not NTE_GAME_COMMAND_RE.match("ntext")


def test_hash_other_game_command_regex_blocks_non_nte_game_prefixes():
    assert HASH_OTHER_GAME_COMMAND_RE.match("#gs帮助")
    assert HASH_OTHER_GAME_COMMAND_RE.match("#yh帮助")
    assert HASH_OTHER_GAME_COMMAND_RE.match("#ww 查询")
    assert not HASH_OTHER_GAME_COMMAND_RE.match("#nte帮助")
    assert not HASH_OTHER_GAME_COMMAND_RE.match("#游戏列表")


def _disposition(text: str, **overrides: object) -> str:
    kwargs = {
        "is_group": True,
        "master_enabled": True,
        "hot_enabled": True,
        "in_scope": True,
    }
    kwargs.update(overrides)
    return game_message_disposition(text, **kwargs)


def test_game_message_disposition_passes_valid_nte_group_commands():
    assert _disposition("#nte帮助") == "pass"
    assert _disposition("#NTE角色列表") == "pass"
    assert _disposition("nte帮助") == "pass"
    assert _disposition("NTE角色列表") == "pass"
    assert _disposition("普通聊天") == "pass"
    assert _disposition("yh帮助") == "no-hash-prefix"
    assert _disposition("#yh帮助") == "disabled-prefix"
    assert _disposition("#gs帮助") == "disabled-prefix"
    assert _disposition("#nte帮助", is_group=False) == "private-chat"
    assert _disposition("#nte帮助", master_enabled=False) == "disabled"
    assert _disposition("#nte帮助", hot_enabled=False) == "disabled"
    assert _disposition("#nte帮助", in_scope=False) == "out-of-scope"
