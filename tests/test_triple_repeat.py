from bot.services.triple_repeat import TripleRepeatTracker


def test_triple_repeat_triggers_once_for_three_consecutive_matching_messages():
    tracker = TripleRepeatTracker()

    assert not tracker.observe(1001, "复读内容")
    assert not tracker.observe(1001, "复读内容")
    assert tracker.observe(1001, "复读内容")
    assert not tracker.observe(1001, "复读内容")


def test_triple_repeat_resets_when_text_changes_or_group_differs():
    tracker = TripleRepeatTracker()

    assert not tracker.observe(1001, "A")
    assert not tracker.observe(1001, "A")
    assert not tracker.observe(1001, "B")
    assert not tracker.observe(1002, "A")
    assert not tracker.observe(1001, "B")
    assert tracker.observe(1001, "B")
