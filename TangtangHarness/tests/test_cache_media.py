"""The new input tail contains explicit images, not automatic repeats of history."""
import asyncio
import base64
import io
from dataclasses import replace

from PIL import Image

from tangtang_harness.config import HarnessConfig, ModelProfile
from tangtang_harness.context import build_context
from tangtang_harness.log_context import persist_cache_context
from tangtang_harness.media import MediaResolver, image_sources
from tangtang_harness.store import Store
from tangtang_harness.types import InboundEvent


def image_segment():
    output = io.BytesIO()
    Image.new('RGB', (2, 2), 'red').save(output, format='PNG')
    return {'type': 'image', 'data': {'file': 'base64://' + base64.b64encode(output.getvalue()).decode('ascii')}}


def event(number, *, images=False, quoted=None):
    return InboundEvent(str(number), 999, 101, 201, '合成图片聊天',
                        segments=(image_segment(),) if images else (), quoted=quoted,
                        timestamp=1_700_000_000 + number)


def test_explicit_quote_remains_available_without_implicit_history():
    prior = event(1, images=True)
    plain = event(2)
    assert image_sources(plain, [prior], include_history=False) == []
    assert len(image_sources(plain, [prior])) == 1
    quoted = replace(plain, quoted={'user_id': 101, 'segments': [image_segment()]})
    sources = image_sources(quoted, [prior], include_history=False)
    assert len(sources) == 1
    assert '引用消息' in sources[0][1]


def test_prior_image_stays_in_prefix_and_does_not_repeat_in_next_tail(tmp_path):
    store = Store(tmp_path)
    profile = ModelProfile('model-3', 'Synthetic 3', 'custom', 'synthetic-model',
                           'https://example.invalid/v1', vision=True)
    config = HarnessConfig(root=tmp_path, profiles=(profile,), active_model=profile.id)
    media = MediaResolver(store)
    first = event(1, images=True)
    store.append_event(first)
    first_media = asyncio.run(media.resolve(first, include_history=False))
    assert len(first_media.assets) == 1
    prepared = build_context(config, store, first, profile, images=first_media.parts)
    persist_cache_context(store, prepared)
    following = event(2)
    store.append_event(following)
    following_media = asyncio.run(media.resolve(following, include_history=False))
    assert following_media.parts == []
    next_context = build_context(config, store, following, profile, images=following_media.parts)
    assert next_context.messages[:len(prepared.messages)] == prepared.messages
    assert isinstance(next_context.user_content, str)
    parts = [part for item in next_context.messages if isinstance(item['content'], list)
             for part in item['content']]
    assert sum(part['type'] == 'image_url' for part in parts) == 1
    quoted = replace(event(3), quoted={'user_id': 101, 'segments': [image_segment()]})
    quote_media = asyncio.run(media.resolve(quoted, include_history=False))
    assert len(quote_media.assets) == 1
