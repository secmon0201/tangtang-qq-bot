from __future__ import annotations


def _command(prefix: str, action: str) -> str:
    return f"{prefix}白名单 {action}".strip()


def build_whitelist_menu_text(command_prefix: str = "") -> str:
    """Return the copyable text fallback for the whitelist menu."""
    prefix = command_prefix.strip()
    return "\n".join(
        (
            "白名单操作菜单",
            "1. 查看列表",
            f"   {_command(prefix, '列表')}",
            "2. 添加用户",
            f"   {_command(prefix, '添加 QQ号 [备注]')}",
            "3. 删除用户",
            f"   {_command(prefix, '删除 QQ号')}",
            "群内和私聊均使用 # 指令；以上文字命令均可直接复制。",
        )
    )


def whitelist_menu_sections(command_prefix: str = "") -> list[tuple[str, str, str]]:
    """Return sections for the deterministic local image menu."""
    prefix = command_prefix.strip()
    return [
        ("查看列表", _command(prefix, "列表"), "展示当前查重白名单。"),
        ("添加用户", _command(prefix, "添加 QQ号 [备注]"), "QQ号必填，备注可省略。"),
        ("删除用户", _command(prefix, "删除 QQ号"), "仅删除白名单记录，不删除 QQ 用户。"),
    ]
