import asyncio
from types import SimpleNamespace

import bot.services.reactions as reactions


def test_random_reactions_only_claim_once_during_group_cooldown(monkeypatch):
    monkeypatch.setattr(
        reactions,
        "settings",
        SimpleNamespace(
            random_reaction_enabled=True,
            random_reaction_group_ids=(1001,),
            random_reaction_probability=1.0,
            random_reaction_cooldown_seconds=180,
        ),
    )
    reactions._next_reaction_at.clear()
    monkeypatch.setattr(reactions, "monotonic", lambda: 10.0)

    assert asyncio.run(reactions.claim_random_reaction(1001))
    assert not asyncio.run(reactions.claim_random_reaction(1001))
    assert not asyncio.run(reactions.claim_random_reaction(1002))


def test_random_reaction_decision_reports_probability_and_cooldown(monkeypatch):
    monkeypatch.setattr(
        reactions,
        "settings",
        SimpleNamespace(
            random_reaction_enabled=True,
            random_reaction_group_ids=(1001,),
            random_reaction_probability=0.5,
            random_reaction_cooldown_seconds=180,
        ),
    )
    reactions._next_reaction_at.clear()
    monkeypatch.setattr(reactions, "monotonic", lambda: 10.0)
    monkeypatch.setattr(reactions.random, "random", lambda: 0.5)

    assert (
        asyncio.run(reactions.random_reaction_decision(1001))
        == reactions.RANDOM_REACTION_PROBABILITY
    )
    monkeypatch.setattr(reactions.random, "random", lambda: 0.49)
    assert (
        asyncio.run(reactions.random_reaction_decision(1001))
        == reactions.RANDOM_REACTION_CLAIMED
    )
    assert (
        asyncio.run(reactions.random_reaction_decision(1001))
        == reactions.RANDOM_REACTION_COOLDOWN
    )


def test_random_reaction_diagnostic_logging_is_rate_limited(monkeypatch):
    reactions._next_decision_log_at.clear()
    monkeypatch.setattr(reactions, "monotonic", lambda: 10.0)

    assert reactions.should_log_random_reaction_decision(1001, "probability")
    assert not reactions.should_log_random_reaction_decision(1001, "probability")
    assert reactions.should_log_random_reaction_decision(1001, "cooldown")
