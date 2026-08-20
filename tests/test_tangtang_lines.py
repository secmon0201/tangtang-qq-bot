from __future__ import annotations

from bot.services import tangtang_lines


def test_banks_have_expected_sizes():
    assert len(tangtang_lines.PROFILE_ALREADY_RUNNING_TEXTS) == 50
    assert len(tangtang_lines.PROFILE_STARTED_TEXTS) >= 10


def test_banks_are_unique():
    assert len(set(tangtang_lines.PROFILE_ALREADY_RUNNING_TEXTS)) == len(
        tangtang_lines.PROFILE_ALREADY_RUNNING_TEXTS
    )
    assert len(set(tangtang_lines.PROFILE_STARTED_TEXTS)) == len(
        tangtang_lines.PROFILE_STARTED_TEXTS
    )


def test_picks_return_bank_members():
    assert tangtang_lines.profile_already_running() in tangtang_lines.PROFILE_ALREADY_RUNNING_TEXTS
    assert tangtang_lines.profile_started() in tangtang_lines.PROFILE_STARTED_TEXTS


def test_pick_avoids_immediate_repeat(monkeypatch):
    tangtang_lines._last_pick.clear()
    monkeypatch.setattr(tangtang_lines.random, "choice", lambda seq: seq[0])
    first = tangtang_lines.profile_already_running()
    second = tangtang_lines.profile_already_running()
    assert first != second
    assert first in tangtang_lines.PROFILE_ALREADY_RUNNING_TEXTS
    assert second in tangtang_lines.PROFILE_ALREADY_RUNNING_TEXTS
