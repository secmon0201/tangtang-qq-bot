from types import SimpleNamespace

from tangtang_harness.billing import UsageBreakdown, aggregate_breakdowns
from tangtang_harness.models import normalize_usage


def profile(**values):
    defaults = dict(input_price_per_million=1, output_price_per_million=2,
                    cache_read_price_per_million=.1, cache_write_price_per_million=.5)
    defaults.update(values)
    return SimpleNamespace(**defaults)


def test_breakdown_separates_total_input_and_cache_write():
    item = UsageBreakdown.from_usage({
        'input_tokens': 1000, 'cache_read_tokens': 700,
        'cache_write_tokens': 100, 'output_tokens': 20,
        'currency': 'CNY',
    }, profile())
    assert item.cache_miss_tokens == 200
    assert item.hit_cost == .00007
    assert item.miss_cost == .0002
    assert item.write_cost == .00005
    assert item.output_cost == .00004
    assert item.total_cost == .00036


def test_missing_cache_field_stays_unknown():
    item = UsageBreakdown.from_usage({'input_tokens': 1000, 'output_tokens': 20}, profile())
    assert item.cache_ratio is None
    assert item.total_cost is None
    assert item.cost_status == 'partial'


def test_normalize_usage_does_not_double_count_write_tokens():
    item = normalize_usage({'usage': {
        'input_tokens': 1000,
        'input_tokens_details': {'cached_tokens': 700},
        'cache_creation_input_tokens': 100,
        'output_tokens': 20,
    }})
    assert item['cache_miss_tokens'] == 300


def test_aggregate_keeps_partial_cost_explicit():
    result = aggregate_breakdowns([
        UsageBreakdown.from_usage({'input_tokens': 10, 'cache_read_tokens': 5, 'cache_write_tokens': 0, 'output_tokens': 2}, profile()).to_dict(),
        UsageBreakdown.from_usage({'input_tokens': 10, 'output_tokens': 2}, profile()).to_dict(),
    ])
    assert result['request_count'] == 2
    assert result['total_cost'] is not None
    assert result['cost_status'] == 'partial'
