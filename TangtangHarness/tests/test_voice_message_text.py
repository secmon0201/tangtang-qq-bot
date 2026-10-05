import json

import pytest

from tangtang_harness.message_text import VOICE_MARKER, message_text, normalize_voice
from tangtang_harness.onebot import Message, MessageSegment
from tangtang_harness.types import InboundEvent


AUDIO = "base64://" + "synthetic-audio-data" * 5000


@pytest.mark.parametrize("kind", ["record", "audio", "voice"])
def test_audio_segment_is_a_marker_and_has_no_retained_transport(kind):
    segment = {"type": kind, "data": {"file": AUDIO, "url": "https://example.invalid/audio", "text": "不要保留识别内容"}}
    mixed = [{"type": "text", "data": {"text": "前面"}}, segment,
             {"type": "text", "data": {"text": "后面"}}]
    assert message_text(mixed) == "前面[语音]后面"
    assert normalize_voice(mixed)[1] == {"type": kind, "data": {}}
    assert segment["data"]["file"] == AUDIO


@pytest.mark.parametrize("kind", ["record", "audio", "voice"])
def test_cq_voice_keeps_adjacent_text_without_audio_payload(kind):
    source = f"前面[CQ:{kind},file={AUDIO},text=不要识别]后面"
    assert message_text(source) == "前面[语音]后面"
    assert normalize_voice(source) == "前面[语音]后面"


@pytest.mark.parametrize("formatter", [str, json.dumps])
def test_serialized_transport_lists_are_compact_readable_text(formatter):
    segments = [{"type": "text", "data": {"text": "前面"}},
                {"type": "record", "data": {"file": AUDIO}},
                {"type": "text", "data": {"text": "后面"}}]
    assert message_text(formatter(segments)) == "前面[语音]后面"
    assert normalize_voice(formatter(segments)) == "前面[语音]后面"


def test_message_segment_repr_is_parsed_without_executing_code():
    message = Message([MessageSegment.text("前面"), MessageSegment.record(AUDIO), MessageSegment.text("后面")])
    assert message_text(repr(message)) == "前面[语音]后面"
    assert message_text(message) == "前面[语音]后面"
    assert message_text(repr(MessageSegment.record(AUDIO))) == VOICE_MARKER
    source = "[MessageSegment(type='record', data=__import__('os').getcwd())]"
    assert message_text(source) == source


def test_normalization_preserves_images_and_unrelated_text():
    image = {"type": "image", "data": {"file": "base64://synthetic-image", "url": "https://example.invalid/image"}}
    ordinary = "请说说 record 和 audio 的区别；base64://也是普通文字"
    payload = {"image": image, "ordinary": ordinary,
               "nested": {"message": [{"type": "record", "data": {"file": AUDIO}}]}}
    cleaned = normalize_voice(payload)
    assert cleaned["image"] == image and cleaned["ordinary"] == ordinary
    assert AUDIO not in json.dumps(cleaned)
    assert message_text("[record]") == VOICE_MARKER


def test_existing_image_data_avoids_voice_text_parsing(monkeypatch):
    def unexpected(_):
        raise AssertionError("image transport must not be parsed as message text")
    monkeypatch.setattr("tangtang_harness.message_text._serialized_voice", unexpected)
    image = "data:image/png;base64," + "synthetic-image" * 5000
    assert normalize_voice(image) == image
    assert normalize_voice("base64://synthetic-image") == "base64://synthetic-image"


@pytest.mark.parametrize("factory", [InboundEvent.from_onebot, InboundEvent.from_dict])
def test_event_entry_points_remove_voice_transport_from_text_and_quotes(factory):
    cq = f"[CQ:record,file={AUDIO}]"
    packet = {"event_id": "1", "message_id": "1", "self_id": 103, "user_id": 101,
              "group_id": 102, "message_type": "group", "text": cq, "message": cq,
              "segments": [{"type": "record", "data": {"file": AUDIO}}],
              "quoted": {"sender": {"user_id": 104}, "message": [{"type": "record", "data": {"file": AUDIO}}]}}
    event = factory(packet)
    assert event.text == VOICE_MARKER and event.quoted["text"] == VOICE_MARKER
    assert AUDIO not in json.dumps(event.to_dict())


def test_direct_event_construction_adds_voice_marker_and_keeps_text_order():
    segments = ({"type": "text", "data": {"text": "前面"}},
                {"type": "record", "data": {"file": AUDIO}},
                {"type": "text", "data": {"text": "后面"}})
    event = InboundEvent("1", 103, 101, 102, "前面后面", segments)
    assert event.text == "前面[语音]后面"
    assert event.segments[1] == {"type": "record", "data": {}}
    assert AUDIO not in json.dumps(event.to_dict())


def test_supplied_quote_text_keeps_voice_marker_when_message_has_mixed_segments():
    quote = {"text": "前面后面", "message": [
        {"type": "text", "data": {"text": "前面"}},
        {"type": "record", "data": {"file": AUDIO}},
        {"type": "text", "data": {"text": "后面"}},
    ]}
    event = InboundEvent("1", 103, 101, 102, "当前发言", quoted=quote)
    assert event.quoted["text"] == "前面[语音]后面"
    assert AUDIO not in json.dumps(event.to_dict())
