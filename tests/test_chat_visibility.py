"""Message-position, blacklist and retained multimodal conversation contracts."""
import asyncio
import base64
import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.exception import IgnoredException

from bot.db import Database
from bot.services.agent_context import ContextEnvelope
from bot.services.tangtang_chat import TangtangProvider
from bot.services.tangtang_db import TangtangDb
from bot.services.tangtang_media import ImageReference, TangtangMediaResolver
from bot.services.vision_request import enforce_vision_limits, image_parts
from tests.test_tangtang_chat import enabled_config, group_message, make_service
from tests.test_tangtang_media import _data_url


def record(service, mid, uid, image=False, text="message"):
    service.record_group_message(1001, f"member-{uid}", text, user_id=uid, message_id=str(mid),
        media_references=(ImageReference("current", 1, _data_url(uid + 1, 2)),) if image else ())


def test_previous_group_and_user_are_distinct_and_survive_restart(tmp_path, monkeypatch):
    service, *_ = make_service(tmp_path, monkeypatch)
    record(service, 1, 11, True)
    for mid in range(2, 20):
        record(service, mid, 22)
    record(service, 20, 22, True)
    record(service, 21, 11, text="call")
    record(service, 22, 22, True)  # Newer arrivals must not change this call's predecessor.
    service._base_db = TangtangDb(service._base_db.path)
    refs = service._context_image_references(1001, user_id=11, exclude_message_id="21", limit=600)
    assert [ref.sender_id for ref in refs] == [11, 22]
    record(service, 23, 11)
    record(service, 24, 11, text="call")
    assert service._context_image_references(1001, user_id=11, exclude_message_id="24", limit=600) == ()


def test_same_previous_message_is_read_once_and_blacklisted_predecessor_does_not_backfill(tmp_path, monkeypatch):
    service, *_ = make_service(tmp_path, monkeypatch)
    record(service, 1, 11, True)
    record(service, 2, 11, True)
    assert len(service._context_image_references(1001, user_id=11, exclude_message_id="3", limit=600)) == 1
    service.blocked_users = lambda group: frozenset({11})
    assert service._context_image_references(1001, user_id=22, exclude_message_id="3", limit=600) == ()


@pytest.mark.parametrize("proactive", [False, True])
def test_current_and_quoted_images_are_selected_by_trigger(tmp_path, monkeypatch, proactive):
    service, _, provider, _ = make_service(tmp_path, monkeypatch)
    record(service, 1, 11, True)
    event = group_message(group_id=1001, user_id=11, message_id=20, text="糖糖看图")
    event.message += MessageSegment.image(_data_url(4, 2))
    event.reply = SimpleNamespace(message_id=0, sender=SimpleNamespace(user_id=22, nickname="quoted"),
                                 message=Message(MessageSegment.image(_data_url(5, 2))) + "quoted-evidence")
    for mid in range(2, 19):
        record(service, mid, 22)
    asyncio.run(service._model_reply(SimpleNamespace(self_id=2), event, enabled_config(TANGTANG_CONTEXT_LAYOUT="v2"), proactive=proactive, call_text=event.get_plaintext()))
    sources = [image.source for image in provider.images[-1]]
    assert sources == (["current"] if proactive else ["current", "reply", "context"])
    if not proactive:
        assert provider.envelopes[-1].current_text.endswith("引用消息：quoted-evidence")


def test_blacklisted_actor_never_calls_provider_or_sends(tmp_path, monkeypatch):
    service, sent, provider, _ = make_service(tmp_path, monkeypatch)
    service.blocked_users = lambda group: frozenset({3})
    event = group_message(group_id=1001, text="糖糖你好")
    asyncio.run(service.handle(SimpleNamespace(self_id=2), event, enabled_config()))
    asyncio.run(service.handle_proactive(SimpleNamespace(self_id=2), event, enabled_config()))
    assert provider.calls == 0
    assert sent == []


def test_blacklisted_text_and_quote_never_enter_prompt(tmp_path, monkeypatch):
    service, _, provider, _ = make_service(tmp_path, monkeypatch)
    record(service, 1, 22, True, "private-blacklisted-text")
    service.blocked_users = lambda group: frozenset({22})
    event = group_message(group_id=1001, text="糖糖看看")
    event.reply = SimpleNamespace(sender=SimpleNamespace(user_id=22, nickname="blocked"),
        message=Message("private-blacklisted-text") + MessageSegment.image(_data_url()))
    asyncio.run(service.handle(SimpleNamespace(self_id=2), event, enabled_config(TANGTANG_CONTEXT_LAYOUT="v2")))
    envelope = provider.envelopes[-1]
    assert "private-blacklisted-text" not in envelope.current_text
    assert provider.images[-1] == ()


def test_blacklist_change_during_model_request_cancels_delivery(tmp_path, monkeypatch):
    service, sent, provider, _ = make_service(tmp_path, monkeypatch)
    blocked = set()
    service.blocked_users = lambda group: frozenset(blocked)
    original = provider.generate_agent
    async def generate(*args, **kwargs):
        result = await original(*args, **kwargs)
        blocked.add(3)
        return result
    provider.generate_agent = generate
    asyncio.run(service.handle(SimpleNamespace(self_id=2), group_message(group_id=1001, text="糖糖你好"), enabled_config()))
    assert provider.calls == 1
    assert sent == []


def test_service_retains_image_then_invalidates_tainted_prefix_on_blacklist_change(tmp_path, monkeypatch):
    service, sent, provider, _ = make_service(tmp_path, monkeypatch)
    blocked = set()
    service.blocked_users = lambda group: frozenset(blocked)
    service._base_db.blocked_users = service.blocked_users
    config = enabled_config(TANGTANG_CONTEXT_LAYOUT="v2")
    record(service, 1, 22, True, "formerly-visible-text")
    for mid, text in ((2, "糖糖先看"), (3, "糖糖接着聊"), (4, "糖糖再看看")):
        if mid == 4:
            blocked.add(22)
        event = group_message(group_id=1001, message_id=mid, text=text)
        record(service, mid, 3, text=text)
        asyncio.run(service.handle(SimpleNamespace(self_id=2), event, config))
    assert len(sent) == 3
    assert any(isinstance(item.get("content"), list) for item in provider.envelopes[1].conversation_items)
    assert "formerly-visible-text" in json.dumps(provider.envelopes[1].conversation_items, ensure_ascii=False)
    assert provider.envelopes[2].conversation_items == ()
    assert "formerly-visible-text" not in provider.envelopes[2].current_text
    assert provider.images[2] == ()


def test_successfully_seen_image_survives_a_silent_turn_without_fake_reply(tmp_path, monkeypatch):
    service, sent, provider, _ = make_service(tmp_path, monkeypatch, response="[沉默]")
    event = group_message(group_id=1001, text="糖糖看看", message_id=1)
    event.message += MessageSegment.image(_data_url())
    config = enabled_config(TANGTANG_CONTEXT_LAYOUT="v2")
    asyncio.run(service.handle(SimpleNamespace(self_id=2), event, config))
    provider.response = "[接话]\n好的呀"
    asyncio.run(service.handle(SimpleNamespace(self_id=2), group_message(group_id=1001, text="糖糖继续", message_id=2), config))
    history = provider.envelopes[-1].conversation_items
    assert len(history) == 1
    assert history[0]["role"] == "user"
    assert any(part['type'] == 'image_url' for part in history[0]['content'])
    assert len(sent) == 1


def test_summary_excludes_blacklisted_sources_and_skips_only_blocked_batch(tmp_path):
    from bot.services.group_summary import GroupSummaryService, GroupSummaryWorker
    from tests.test_group_summary import Loader, SummaryProvider
    db = TangtangDb(tmp_path / "summary.db")
    db.insert_group_message(group_id=1001, user_id=22, nickname="member", text="topic", message_id="1", created_at="now")
    topic = db.group_summary_merge(1001, topic_id=None, title="topic", summary="formerly-visible", keywords=(),
        participants=(), unresolved=(), state="active", message_ids=(1,), now="now")
    assert topic['topic_id']
    db.blocked_users = lambda group: frozenset({22})
    assert db.group_summary_sources(1001) == []
    provider = SummaryProvider()
    worker = GroupSummaryWorker(GroupSummaryService(db, provider,
        Loader(enabled_config(TANGTANG_GROUP_SUMMARY_ENABLED="true")), chat_id=lambda: "now"))
    asyncio.run(worker.tick((1001,)))
    assert provider.calls == 0
    assert db.group_summary_pending(1001) == []


@pytest.mark.parametrize("style,key", [("responses", "input"), ("chat_completions", "messages")])
def test_retained_images_survive_new_images_restart_and_plain_turn(tmp_path, style, key):
    db = TangtangDb(tmp_path / "history.db")
    sid = db.ensure_context_session(group_id=1001, user_id=3, layout_version="v2", persona_version="p", tool_version="t", now="now")
    image = asyncio.run(TangtangMediaResolver().resolve_references((ImageReference("current", 1, _data_url()),))).images[0]
    config = enabled_config(TANGTANG_API_STYLE=style)
    builder = TangtangProvider._responses_payload if style == "responses" else TangtangProvider._chat_payload
    first = ContextEnvelope.create(persona="persona", current_input="first")
    wire = builder(config, "persona", "", envelope=first, images=(image,))
    db.commit_context_items(sid, "one", (
        {"type": "message", "role": "user", "content": [{"type": "text", "text": first.current_text}, *image_parts((image,))]},
        {"type": "message", "role": "assistant", "content": "answer"}), now="now")
    db = TangtangDb(db.path)
    _, history = db.context_window(sid)
    for added in ((), (image,)):
        second = ContextEnvelope.create(persona="persona", conversation_items=history, current_input="second")
        actual = builder(config, "persona", "", envelope=second, images=added)
        assert actual[key][:len(wire[key])] == wire[key]
        assert json.dumps(actual[key][:len(wire[key])], ensure_ascii=False) == json.dumps(wire[key], ensure_ascii=False)
        enforce_vision_limits(actual)
    with db._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM chat_context_images").fetchone()[0] == 1
        assert "data:image" not in conn.execute("SELECT payload_json FROM chat_context_turns LIMIT 1").fetchone()[0]


@pytest.mark.parametrize("count,expected", [(14, (6000, 12)), (15, (4096, 8))])
def test_high_dimension_depends_on_total_retained_and_new_images(count, expected):
    value = _data_url(6000, 12)
    payload = {"messages": [
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": value}} for _ in range(count - 1)]},
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": value}}]},
    ]}
    enforce_vision_limits(payload)
    for msg in payload["messages"]:
        for part in msg["content"]:
            assert part["image_url"]["detail"] == "high"
            with Image.open(io.BytesIO(base64.b64decode(part["image_url"]["url"].split(",")[1]))) as img:
                assert img.size == expected
            if count == 14:
                assert part["image_url"]["url"] == value


def test_provider_rejects_invalid_role_count_and_body_without_evicting_history():
    part = {"type": "image_url", "image_url": {"url": _data_url()}}
    with pytest.raises(ValueError, match="user message"):
        enforce_vision_limits({"messages": [{"role": "assistant", "content": [part]}]})
    with pytest.raises(ValueError, match="600 images"):
        enforce_vision_limits({"messages": [{"role": "user", "content": [part] * 601}]})
    with pytest.raises(ValueError, match="48 MiB"):
        enforce_vision_limits({"messages": [{"role": "user", "content": "x" * (48 * 1024 * 1024)}]})


def test_large_image_batch_has_one_total_download_deadline():
    class StalledResolver(TangtangMediaResolver):
        attempts = 0
        async def _read_reference(self, value):
            self.attempts += 1
            await asyncio.Event().wait()
    resolver = StalledResolver(timeout_seconds=0.01)
    refs = tuple(ImageReference("current", i, f"https://example.invalid/{i}.png") for i in range(20))
    result = asyncio.run(resolver.resolve_references(refs))
    assert resolver.attempts == 1
    assert result.images == ()
    assert len(result.failures) == 20


def test_parallel_game_gate_defers_blocked_event_to_the_statistics_owner(monkeypatch):
    import bot.plugins.game_api as game_api
    monkeypatch.setattr(game_api, "database", lambda: SimpleNamespace(interaction_blocked=lambda group, user: True))
    event = group_message(group_id=1001, text="#gs unsupported")
    asyncio.run(game_api._(event))


@pytest.mark.parametrize("kind", ["active", "passive", "group"])
def test_all_identity_blacklists_keep_statistics_and_stop_commands(tmp_path, monkeypatch, kind):
    import bot.plugins.scope as scope
    db = Database(tmp_path / "main.db")
    db.seed_groups((1001,))
    if kind == "group":
        db.add_group_filter(1001, 3, 9)
    else:
        db.add_filter_members(kind, (3,), 9)
    monkeypatch.setattr(scope, "database", lambda: db)
    monkeypatch.setattr(scope, "group_domains", lambda: SimpleNamespace(all_group_ids=lambda: (1001,)))
    monkeypatch.setattr(scope, "settings", SimpleNamespace(stats_realtime_enabled=True))
    event = group_message(group_id=1001, text="#帮助")
    for _ in range(2):
        with pytest.raises(IgnoredException):
            asyncio.run(scope.guard_message_scope(SimpleNamespace(self_id=2), event))
    with db.connect() as conn:
        assert conn.execute("SELECT SUM(message_count) FROM daily_counts WHERE user_id=3").fetchone()[0] == 1
    assert db.interaction_blocked(1002, 3) == (kind != "group")
