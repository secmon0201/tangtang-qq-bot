"""Super-admin skill platform commands: status, export and user purge."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import nonebot
from nonebot.exception import FinishedException

nonebot.init()

from bot.plugins import skill_admin as plugin


class _Message:
    def __init__(self, text: str) -> None:
        self._text = text

    def extract_plain_text(self) -> str:
        return self._text


def _event(user_id: int) -> SimpleNamespace:
    return SimpleNamespace(user_id=user_id)


def _run_handler(monkeypatch, text: str, user_id: int) -> None:
    try:
        asyncio.run(plugin.handle_skill_admin(_event(user_id), _Message(text)))
    except FinishedException:
        pass


def test_non_super_admin_is_rejected(monkeypatch):
    sent: list[str] = []

    async def fake_finish(message) -> None:
        sent.append(str(message))
        raise FinishedException

    monkeypatch.setattr(plugin.skill_admin, "finish", fake_finish)
    monkeypatch.setattr(plugin, "is_super_admin", lambda _uid: False)
    _run_handler(monkeypatch, "状态", 1)
    assert sent == ["技能审计仅限超级管理员。"]


def test_status_reports_registry_and_capabilities(monkeypatch, tmp_path):
    sent: list[str] = []

    async def fake_finish(message) -> None:
        sent.append(str(message))
        raise FinishedException

    monkeypatch.setattr(plugin.skill_admin, "finish", fake_finish)
    monkeypatch.setattr(plugin, "is_super_admin", lambda _uid: True)
    from bot.services.skill_audit import SkillAuditLedger

    monkeypatch.setattr(plugin, "ledger", SkillAuditLedger(tmp_path / "audit.db"))
    _run_handler(monkeypatch, "状态", 1)
    assert sent
    assert "注册技能：" in sent[0]
    assert "本地动作：" in sent[0]
    assert "上游能力：" in sent[0]


def test_data_export_writes_the_ledger(monkeypatch, tmp_path):
    sent: list[str] = []

    async def fake_finish(message) -> None:
        sent.append(str(message))
        raise FinishedException

    monkeypatch.setattr(plugin.skill_admin, "finish", fake_finish)
    monkeypatch.setattr(plugin, "is_super_admin", lambda _uid: True)
    from bot.services.skill_audit import SkillAuditLedger

    ledger = SkillAuditLedger(tmp_path / "audit.db", now=lambda: 100.0)
    ledger.record(skill_id="commands", category="parameter_error", severity="warning")
    monkeypatch.setattr(plugin, "ledger", ledger)
    import bot.config

    monkeypatch.setattr(bot.config, "ROOT", tmp_path)
    _run_handler(monkeypatch, "数据 导出", 1)
    assert sent and "已导出" in sent[0]
    exported = json.loads(
        (tmp_path / "data" / "skills" / "export-audit.json").read_text(encoding="utf-8")
    )
    assert isinstance(exported, list) and len(exported) == 1
