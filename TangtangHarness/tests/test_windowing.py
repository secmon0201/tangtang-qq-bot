from tangtang_harness.windowing import (
    CostRates,
    CostScenario,
    UsageSample,
    WindowManager,
    compare_window_costs,
    cost_breakdown,
)


def priced_rates():
    return CostRates(input_per_million=1.0, cache_read_per_million=0.1,
                     cache_write_per_million=0.2, output_per_million=2.0,
                     currency="¥")


def test_group_epoch_is_long_lived_but_private_idle_releases_runtime():
    manager = WindowManager(private_idle_seconds=60)
    group = manager.get_or_open("group:200", now=100)
    manager.begin_turn("group:200", now=101)
    manager.complete_turn("group:200", snapshot_revision=3, now=102)
    assert manager.get_or_open("group:200", now=10_000).epoch == group.epoch
    private = manager.get_or_open("private:300", now=100)
    manager.begin_turn("private:300", now=101)
    manager.complete_turn("private:300", snapshot_revision=4, now=102)
    released = manager.release_idle(now=200)
    assert released[0].from_epoch == private.epoch and released[0].to_epoch is None
    assert manager.session("private:300").active is None
    reopened = manager.get_or_open("private:300", now=201)
    assert reopened.epoch == private.epoch + 1
    assert manager.session("private:300").completed_turns == 1


def test_switch_records_reason_and_snapshot_only_after_completed_turn():
    manager = WindowManager()
    manager.begin_turn("group:200", now=10)
    try:
        manager.switch_after_turn("group:200", reason="费用回本", snapshot_revision=7, now=11)
    except RuntimeError as error:
        assert "轮次完成" in str(error)
    else:
        raise AssertionError("切换不能发生在进行中的轮次")
    manager.complete_turn("group:200", snapshot_revision=6, now=12)
    next_epoch = manager.switch_after_turn("group:200", reason="费用回本", snapshot_revision=7, now=13)
    assert next_epoch.epoch == 2
    transition = manager.transitions("group:200")[-1]
    assert transition.from_epoch == 1 and transition.to_epoch == 2
    assert transition.reason == "费用回本" and transition.snapshot_revision == 7
    try:
        manager.switch_after_turn("group:200", reason="重复切换", snapshot_revision=8, now=14)
    except RuntimeError as error:
        assert "轮次完成" in str(error)
    else:
        raise AssertionError("同一轮完成后不能重复切换")


def test_unknown_usage_is_not_filled_with_zero():
    rates = priced_rates()
    result = cost_breakdown(UsageSample(input_tokens=100, cache_read_tokens=80,
                                        cache_write_tokens=None, output_tokens=20), rates)
    assert result.hit_cost == 80 * 0.1 / 1_000_000
    assert result.miss_tokens is None
    assert result.write_cost is None
    assert result.total_cost is None


def test_compare_projects_known_future_cost_and_recommends_switch():
    rates = priced_rates()
    continued = CostScenario.steady("旧段", UsageSample(
        input_tokens=100_000, cache_read_tokens=90_000, cache_miss_tokens=10_000,
        cache_write_tokens=0, output_tokens=100))
    rebuilt = CostScenario(
        "新段", UsageSample(input_tokens=100_000, cache_read_tokens=0,
                            cache_miss_tokens=100_000, cache_write_tokens=100_000,
                            output_tokens=100),
        UsageSample(input_tokens=20_000, cache_read_tokens=19_000,
                    cache_miss_tokens=1_000, cache_write_tokens=0, output_tokens=100),
    )
    evaluation = compare_window_costs(continued, rebuilt, 10, rates)
    assert evaluation.recommendation == "switch"
    assert evaluation.saving_if_rebuilt is not None and evaluation.saving_if_rebuilt > 0
    assert "本轮完成后" in evaluation.explanation


def test_compare_keeps_recommendation_unknown_when_any_future_usage_is_unknown():
    rates = priced_rates()
    old = CostScenario.steady("旧段", UsageSample(input_tokens=100, cache_read_tokens=80,
                                                    cache_miss_tokens=20, cache_write_tokens=0,
                                                    output_tokens=20))
    new = CostScenario("新段", UsageSample(input_tokens=100, cache_read_tokens=0,
                                           cache_miss_tokens=100, cache_write_tokens=100,
                                           output_tokens=20), None)
    evaluation = compare_window_costs(old, new, 2, rates)
    assert evaluation.recommendation == "unknown"
    assert evaluation.saving_if_rebuilt is None
    assert evaluation.rebuild_projection.unknown_rounds == 1

