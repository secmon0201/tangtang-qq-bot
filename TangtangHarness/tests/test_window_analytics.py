from tangtang_harness.analytics import Analytics
from tangtang_harness.config import HarnessConfig, ModelProfile
from tangtang_harness.runtime import Runtime
from tangtang_harness.types import InboundEvent


def test_window_analytics_groups_actual_request_epochs(tmp_path):
    profile = ModelProfile('p', 'P', 'custom', 'model', 'https://example.invalid/v1',
                           input_price_per_million=1, output_price_per_million=2,
                           cache_read_price_per_million=.1, cache_write_price_per_million=.2,
                           currency='CNY')
    config = HarnessConfig(root=tmp_path, profiles=(profile,), active_model='p')
    runtime = Runtime(config)
    event = InboundEvent('1', 1, 10, 20, '合成')
    for request_id, epoch, read in (('one', 1, 80), ('two', 1, 90), ('three', 2, 0)):
        rid = runtime.store.add_request(event, profile, {'messages': []}, request_id=request_id,
                                        telemetry={'context_epoch': epoch, 'snapshot_revision': epoch})
        runtime.store.finish_request(rid, usage={
            'input_tokens': 100, 'cache_read_tokens': read,
            'cache_write_tokens': 0, 'cache_miss_tokens': 100 - read,
            'output_tokens': 10, 'currency': 'CNY',
            'billing': {'hit_cost': read * .1 / 1_000_000,
                        'miss_cost': (100 - read) / 1_000_000,
                        'write_cost': 0, 'output_cost': 20 / 1_000_000,
                        'total_cost': (read * .1 + 100 - read + 20) / 1_000_000,
                        'cost_status': 'estimated'},
        })
    items = Analytics(runtime).windows({})['items']
    assert [(item['epoch'], item['request_count']) for item in items] == [(2, 1), (1, 2)]
    assert items[1]['cache_ratio'] == .85
    assert items[1]['cost_coverage_ratio'] == 1


def test_window_analytics_keeps_partial_component_cost_unknown(tmp_path):
    profile = ModelProfile('p', 'P', 'custom', 'model', 'https://example.invalid/v1')
    runtime = Runtime(HarnessConfig(root=tmp_path, profiles=(profile,), active_model='p'))
    event = InboundEvent('1', 1, 10, 20, '合成')
    for request_id, billing in (
        ('known', {'output_cost': 0.01, 'total_cost': 0.01, 'cost_status': 'estimated'}),
        ('unknown', {'output_cost': None, 'total_cost': None, 'cost_status': 'unknown'}),
    ):
        rid = runtime.store.add_request(event, profile, {'messages': []}, request_id=request_id,
                                        telemetry={'context_epoch': 1})
        runtime.store.finish_request(rid, usage={'billing': billing})
    item = Analytics(runtime).windows({})['items'][0]
    assert item['output_cost'] is None
    assert item['cost_component_coverage']['output_cost'] == .5
