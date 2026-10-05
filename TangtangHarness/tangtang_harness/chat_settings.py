"""QQ management syntax for model and proactive settings, without side effects."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import HarnessConfig
from .proactive_policy import LABELS, STRATEGIES


_SWITCHES = {"开": True, "开启": True, "打开": True, "启用": True, "on": True,
             "关": False, "关闭": False, "停用": False, "off": False}
_GATES = {"被呼叫会话": "mention_chat_enabled", "人格后台整理": "background_enabled",
          "糖糖主动聊天": "proactive_enabled", "语音": "speech_enabled"}


@dataclass(slots=True)
class ChatSettingsCommand:
    section: str
    changes: dict[str, Any] = field(default_factory=dict)
    private_only: bool = False
    requires_operator: bool = True


def parse_chat_settings(text: str, config: HarnessConfig, *, group_id: int | None = None,
                        managed_group_ids: tuple[int, ...] = ()) -> ChatSettingsCommand:
    tokens = text.lstrip("# ").split()
    private_only = bool(tokens and tokens[0] == "系统设置")
    if private_only:
        tokens.pop(0)
    if not tokens:
        return ChatSettingsCommand("chat", private_only=private_only)
    head, args = tokens[0], tokens[1:]
    if head in {"糖糖模型", "模型"}:
        result = ChatSettingsCommand("model", private_only=private_only)
        requested = " ".join(args)
        if requested and requested.casefold() not in {"状态", "status", "列表", "list"}:
            profile = next((item for item in config.profiles if requested in {item.id, item.name}), None)
            if profile is None:
                raise ValueError("模型档案不存在；请使用已登记的档案名。")
            result.changes["active_model"] = profile.id
        return result
    if head in _GATES:
        result = ChatSettingsCommand(_GATES[head], private_only=private_only)
        if not args or args == ["状态"] or args == ["status"]:
            return result
        if len(args) != 1 or args[0].casefold() not in _SWITCHES:
            raise ValueError(f"用法：#系统设置 {head} 状态|开|关。")
        result.changes[_GATES[head]] = _SWITCHES[args[0].casefold()]
        return result
    if head not in {"糖糖主动回复", "糖糖主动", "主动回复"}:
        raise ValueError("没有这个聊天管理指令。")
    result = ChatSettingsCommand("proactive", private_only=private_only)
    if not args or args in (["状态"], ["status"]):
        return result
    action = args[0].casefold()
    if len(args) == 1 and action in _SWITCHES:
        result.changes["proactive_enabled"] = _SWITCHES[action]
        return result
    extra = {**config.extra}
    if action == "策略" and len(args) in {2, 3}:
        strategy = next((key for key in STRATEGIES if args[1] in {key, LABELS[key]}), "")
        if not strategy:
            raise ValueError("策略可选：旧规则、活跃群、低流量群。")
        target = args[2] if len(args) == 3 else str(group_id or "")
        if target == "全部":
            targets = tuple(managed_group_ids)
            extra["proactive_strategy"] = strategy
        elif target.isdigit() and int(target) in managed_group_ids:
            targets = (int(target),)
        else:
            raise ValueError("请在已接管群内使用，或指定已接管群号/全部。")
        overrides = {key: {**value} for key, value in extra.get("proactive_by_group", {}).items()}
        for target_group in targets:
            overrides[str(target_group)] = {**overrides.get(str(target_group), {}), "proactive_strategy": strategy}
        extra["proactive_by_group"] = overrides
    elif action in {"概率", "命中率", "probability"} and len(args) == 2:
        raw = args[1]
        value = float(raw.rstrip("%"))
        if raw.endswith("%") or value > 1:
            value /= 100
        if not 0 <= value <= 1:
            raise ValueError("概率范围为 0–100%。")
        extra["proactive_probability"] = value
        _update_group_parameter(extra, "proactive_probability", value)
    elif action in {"冷却", "cooldown", "间隔", "interval"} and len(args) == 2:
        value = int(args[1])
        cooldown = action in {"冷却", "cooldown"}
        maximum = 1440 if cooldown else 10000
        if not 0 <= value <= maximum:
            raise ValueError("冷却范围为 0–1440 分钟。" if cooldown else "间隔范围为 0–10000 条消息。")
        key = "proactive_cooldown_seconds" if cooldown else "proactive_message_interval"
        extra[key] = value * 60 if cooldown else value
        _update_group_parameter(extra, key, extra[key])
    else:
        raise ValueError("用法：#主动回复 状态|开启|关闭|概率 0-100%|冷却 0-1440|间隔 0-10000；策略 旧规则|活跃群|低流量群 [群号|全部]。")
    result.changes["extra"] = extra
    return result


def _update_group_parameter(extra: dict, key: str, value: Any) -> None:
    """The old global parameter command updated every existing group override."""
    if "proactive_by_group" in extra:
        extra["proactive_by_group"] = {group: {**settings, key: value}
                                       for group, settings in extra["proactive_by_group"].items()}


def chat_settings_status(command: ChatSettingsCommand, config: HarnessConfig,
                         *, managed_group_ids: tuple[int, ...] = ()) -> str:
    if command.section == "model":
        active = config.active_profile
        rows = [f"当前模型：{active.name if active else '未配置'}"]
        rows.extend(f"{'当前 ' if item.id == config.active_model else ''}{item.name}（{item.id}）：{item.model}"
                    for item in config.profiles)
        return "\n".join(rows)
    if command.section in _GATES.values():
        label = next(label for label, key in _GATES.items() if key == command.section)
        return label + "：" + ("开" if getattr(config, command.section) else "关")
    if command.section == "proactive":
        extra = config.extra
        rows = ["主动回复：" + ("开" if config.proactive_enabled else "关"),
                f"旧规则概率：{extra.get('proactive_probability', .02) * 100:g}%",
                f"冷却：{extra.get('proactive_cooldown_seconds', 900) / 60:g} 分钟",
                f"间隔：{extra.get('proactive_message_interval', 20)} 条消息"]
        for group in managed_group_ids:
            settings = {**extra, **extra.get("proactive_by_group", {}).get(str(group), {})}
            strategy = settings.get("proactive_strategy", "active_v1")
            rows.append(f"{group}：{LABELS.get(strategy, strategy)}")
        return "\n".join(rows)
    return f"人格：达妮娅；模型：{config.active_model or '未配置'}；模式：{config.mode}"
