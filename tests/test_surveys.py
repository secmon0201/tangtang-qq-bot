from PIL import Image
import asyncio
from types import SimpleNamespace

from bot.db import Database
from bot.services.reports import ReportRenderer
from bot.services.surveys import (
    parse_survey_create_payload,
    parse_survey_id,
)


def make_db(tmp_path):
    return Database(tmp_path / "bot.db")


def survey_plugin():
    import nonebot

    try:
        nonebot.get_driver()
    except ValueError:
        nonebot.init()
    from bot.plugins import surveys

    return surveys


def test_survey_create_parser_accepts_questionnaire_labels_and_multiple_groups():
    payload = parse_survey_create_payload(
        """问卷内容：发个问卷调查，要不要给糖糖加上异环nte功能。
调查群：123456, 654321，195846214"""
    )
    assert payload.question.startswith("发个问卷调查")
    assert payload.group_ids == (123456, 654321, 195846214)


def test_newly_published_survey_ends_previous_survey_and_becomes_feedback_target(tmp_path):
    db = make_db(tmp_path)
    first = db.create_survey("第一份公告", 99, (1001,))
    second = db.create_survey("第二份公告", 99, (1001,))

    assert db.publish_survey(first)
    assert db.current_feedback_survey()["survey_id"] == first
    assert db.publish_survey(second)
    assert db.survey(first)["status"] == "ended"
    assert db.current_feedback_survey()["survey_id"] == second


def test_survey_broadcasts_are_marked_sent_and_not_repeated(tmp_path):
    db = make_db(tmp_path)
    survey_id = db.create_survey("公告内容", 99, (1001, 1002))
    assert [row["group_id"] for row in db.pending_survey_broadcasts(survey_id)] == [1001, 1002]
    db.mark_survey_broadcast_sent(survey_id, 1001)
    assert [row["group_id"] for row in db.pending_survey_broadcasts(survey_id)] == [1002]


def test_feedback_is_stored_and_delivered_once_per_super_admin(tmp_path):
    db = make_db(tmp_path)
    db.configure_groups((1001,))
    survey_id = db.create_survey("反馈公告", 99, (1001,))
    db.publish_survey(survey_id)
    feedback_id = db.record_feedback(survey_id, 42, "希望增加深色模式", 1001)
    db.ensure_feedback_deliveries((feedback_id,), (9, 10))

    assert [row["user_id"] for row in db.feedback_entries()] == [42]
    assert [row["feedback_id"] for row in db.pending_feedback_deliveries(9)] == [feedback_id]
    assert [row["feedback_id"] for row in db.pending_feedback_deliveries(10)] == [feedback_id]

    db.mark_feedback_delivery_sent(feedback_id, 9)
    assert db.pending_feedback_deliveries(9) == []
    assert [row["feedback_id"] for row in db.pending_feedback_deliveries(10)] == [feedback_id]


def test_feedback_delivery_errors_remain_pending_for_retry(tmp_path):
    db = make_db(tmp_path)
    survey_id = db.create_survey("反馈公告", 99, (1001,))
    db.publish_survey(survey_id)
    feedback_id = db.record_feedback(survey_id, 42, "按钮文字太小")
    db.ensure_feedback_deliveries((feedback_id,), (9,))
    db.mark_feedback_delivery_error(feedback_id, 9, "timeout")

    row = db.pending_feedback_deliveries(9)[0]
    assert row["feedback_id"] == feedback_id


def test_survey_broadcast_is_one_non_forward_image_and_text_message(monkeypatch, tmp_path):
    surveys = survey_plugin()

    db = make_db(tmp_path)
    survey_id = db.create_survey("公告内容", 99, (1001,))
    poster = tmp_path / "survey.png"
    Image.new("RGB", (10, 10), "white").save(poster)
    sent = []

    async def fake_call_api(bot, action, **kwargs):
        sent.append((action, kwargs))

    monkeypatch.setattr(surveys, "db", db)
    monkeypatch.setattr(surveys, "survey_poster_path", lambda _survey: poster)
    monkeypatch.setattr(surveys, "call_qq_action", fake_call_api)

    class FakeBot:
        self_id = 123

    sent_count, failed_count = asyncio.run(surveys.send_survey_broadcasts(FakeBot(), db.survey(survey_id)))

    assert (sent_count, failed_count) == (1, 0)
    assert sent[0][0] == "send_group_msg"
    message = sent[0][1]["message"]
    assert message[0].type == "image"
    assert "###+内容" in str(message)


def test_feedback_notifications_are_delivered_to_each_super_admin(monkeypatch, tmp_path):
    surveys = survey_plugin()

    db = make_db(tmp_path)
    survey_id = db.create_survey("反馈公告", 99, (1001,))
    db.publish_survey(survey_id)
    feedback_id = db.record_feedback(survey_id, 42, "希望增加深色模式")
    sent = []

    async def fake_call_api(_bot, action, **kwargs):
        sent.append((action, kwargs))

    monkeypatch.setattr(surveys, "db", db)
    monkeypatch.setattr(surveys, "call_qq_action", fake_call_api)
    monkeypatch.setattr(surveys, "settings", SimpleNamespace(operator_ids=frozenset({9, 10})))

    asyncio.run(surveys.deliver_feedback_notifications(object()))

    assert {kwargs["user_id"] for action, kwargs in sent if action == "send_private_forward_msg"} == {9, 10}
    assert db.pending_feedback_deliveries(9) == []
    assert db.pending_feedback_deliveries(10) == []
    assert feedback_id in {row["feedback_id"] for row in db.feedback_entries()}


def test_survey_id_input_is_tolerant():
    assert parse_survey_id("ID：1") == 1
    assert parse_survey_id("# 1") == 1


def test_feedback_entries_are_filtered_and_paginated_by_survey(tmp_path):
    db = make_db(tmp_path)
    first = db.create_survey("第一份公告", 99, (1001,))
    second = db.create_survey("第二份公告", 99, (1001,))
    db.publish_survey(first)
    for index in range(201):
        db.record_feedback(first, index + 1, f"第一份 {index}")
    db.publish_survey(second)
    db.record_feedback(second, 1, "第二份")

    assert len(db.feedback_entries(first, limit=200)) == 200
    assert len(db.feedback_entries(first, limit=200, offset=200)) == 1
    assert [row["content"] for row in db.feedback_entries(second)] == ["第二份"]


def test_feedback_forward_groups_twenty_entries_per_dialog_and_ten_dialogs_per_record():
    surveys = survey_plugin()
    rows = [
        {
            "feedback_id": index,
            "survey_id": 1,
            "user_id": index,
            "source_group_id": None,
            "created_at": "2026-08-01T00:00:00+00:00",
            "content": f"反馈 {index}",
        }
        for index in range(1, 202)
    ]

    records = surveys.feedback_records(rows)

    assert [len(record) for record in records] == [10, 1]
    assert len(records[0][0]) == 20
    assert len(records[1][0]) == 1
    assert surveys.parse_feedback_lookup("") == (None, 1)
    assert surveys.parse_feedback_lookup("-2") == (None, 2)
    assert surveys.parse_feedback_lookup("12") == (12, 1)
    assert surveys.parse_feedback_lookup("12 -2") == (12, 2)


def test_survey_poster_is_a_nonempty_png(tmp_path):
    path = ReportRenderer(tmp_path, command_prefix="#").render_survey_poster(
        1,
        "发个问卷调查，要不要给糖糖加上异环nte功能，类似ww登录这种。想要的发：#1",
    )
    assert path.exists()
    with Image.open(path) as image:
        assert image.width == ReportRenderer.WIDTH
        assert image.height > ReportRenderer.HEADER_HEIGHT
        assert image.getbbox() is not None
