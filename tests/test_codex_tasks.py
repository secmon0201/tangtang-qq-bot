from pathlib import Path

from bot.db import Database
from bot.services.codex_tasks import (
    initial_command,
    parse_codex_event,
    resume_command,
    task_title_from_prompt,
)


def test_task_turn_can_resume_the_same_codex_thread(tmp_path):
    db = Database(tmp_path / "bot.db")
    task_id = db.create_codex_task("直播防护", "先检查现有实现。", 595861835)

    assert db.codex_task(task_id)["status"] == "draft"
    assert db.start_codex_task(task_id) == "queued"
    first = db.claim_next_codex_task_message()
    assert first is not None
    assert first["codex_thread_id"] == ""

    db.set_codex_task_thread(task_id, "thread-123")
    completed = db.finish_codex_task_message(
        int(first["message_id"]), "completed", result="已检查。"
    )
    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["codex_thread_id"] == "thread-123"

    db.append_codex_task_message(task_id, "继续补测试。", 595861835)
    assert db.start_codex_task(task_id) == "queued"
    second = db.claim_next_codex_task_message()
    assert second is not None
    assert second["codex_thread_id"] == "thread-123"
    assert second["content"] == "继续补测试。"


def test_only_one_task_can_occupy_the_worker_queue(tmp_path):
    db = Database(tmp_path / "bot.db")
    first_id = db.create_codex_task("第一个", "任务一", 1)
    second_id = db.create_codex_task("第二个", "任务二", 1)

    assert db.start_codex_task(first_id) == "queued"
    assert db.start_codex_task(second_id) == f"busy:{first_id}"


def test_pause_preserves_future_continuations_but_cancels_current_turn(tmp_path):
    db = Database(tmp_path / "bot.db")
    task_id = db.create_codex_task("持续任务", "第一轮", 1)
    assert db.start_codex_task(task_id) == "queued"
    first = db.claim_next_codex_task_message()
    assert first is not None
    db.append_codex_task_message(task_id, "第二轮", 1)

    assert db.stop_codex_task(task_id) == "stopping"
    row = db.finish_codex_task_message(int(first["message_id"]), "cancelled", error="已暂停")

    assert row is not None
    assert row["status"] == "draft"
    assert row["queued_count"] == 1
    assert db.start_codex_task(task_id) == "queued"


def test_cancel_discards_queued_work_and_retry_copies_an_interrupted_turn(tmp_path):
    db = Database(tmp_path / "bot.db")
    task_id = db.create_codex_task("任务", "会中断的内容", 1)
    assert db.start_codex_task(task_id) == "queued"
    turn = db.claim_next_codex_task_message()
    assert turn is not None
    db.finish_codex_task_message(int(turn["message_id"]), "interrupted", error="重启")

    assert db.retry_codex_task(task_id, 1) == "queued"
    assert db.codex_task(task_id)["queued_count"] == 1
    assert db.stop_codex_task(task_id, cancel_all=True) == "cancelled"
    row = db.codex_task(task_id)
    assert row is not None
    assert row["status"] == "cancelled"
    assert row["queued_count"] == 0
    assert db.retry_codex_task(task_id, 1) == "cancelled"


def test_codex_json_event_parser_and_commands_are_session_aware():
    thread_id, message = parse_codex_event('{"type":"thread.started","thread_id":"abc"}')
    assert thread_id == "abc"
    assert message is None
    thread_id, message = parse_codex_event(
        '{"type":"item.completed","item":{"type":"agent_message","text":"完成"}}'
    )
    assert thread_id is None
    assert message == "完成"
    assert parse_codex_event("not json") == (None, None)

    command = Path("C:/workspace/codex.cmd")
    first = initial_command(command, "任务", "workspace-write")
    resumed = resume_command(command, "thread-1", "续办")
    assert first[:6] == [str(command), "exec", "--json", "--sandbox", "workspace-write", "--cd"]
    assert resumed == [str(command), "exec", "resume", "--json", "thread-1", "续办"]
    assert task_title_from_prompt("  先做这个\n再做那个  ") == "先做这个 再做那个"
