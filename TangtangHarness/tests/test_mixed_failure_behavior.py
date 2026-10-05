import asyncio

import pytest

from tangtang_harness.runtime import Runtime
from tangtang_harness.types import ToolResult
from test_runtime_and_console import FakeBot, FakeModel, configured, packet


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["error", "failed", "timeout", "raises"])
async def test_mixed_technical_failure_keeps_local_results_and_stops_model_analysis(tmp_path, failure):
    bot, model = FakeBot(), FakeModel()
    runtime = Runtime(configured(tmp_path, "live"), bot=bot, model_client=model)
    backend = asyncio.Queue()
    runtime.subscribers.add(backend)
    executed = []

    async def execute(event, call):
        executed.append(call.name)
        if call.name == "week_live":
            if failure == "raises":
                raise RuntimeError("ReadTimeout: provider read timed out")
            return ToolResult(failure, "ReadTimeout: provider read timed out",
                              {"error": "ReadTimeout: provider read timed out"})
        return ToolResult("ok", "正常本地结果：" + call.name, {"facts": {"count": 2}})

    runtime.execute_call = execute
    accepted = await runtime.receive(packet("#harness #发言排行 然后 #本周直播 然后 #帮助 然后 分析一下"))
    assert accepted == {"status": "accepted", "route": "mixed"}
    await asyncio.gather(*tuple(runtime.tasks))

    assert executed == ["ranking", "week_live", "user_help"]
    assert [str(message) for _, message in bot.sent] == ["正常本地结果：ranking", "正常本地结果：user_help"]
    assert not model.calls
    stored = runtime.store.tool_results("group:102")
    assert len(stored) == 3
    error = next(row for row in stored if row["name"] == "week_live")
    assert error["result"]["status"] == ("error" if failure == "raises" else failure)
    assert "ReadTimeout" in str(error["result"])
    assert any(item["type"] == "tool" and item.get("status") in {"error", "failed", "timeout"}
               for item in list(backend._queue))
    await runtime.close()


@pytest.mark.asyncio
async def test_successful_mixed_request_still_calls_one_analysis_model(tmp_path):
    bot, model = FakeBot(), FakeModel()
    runtime = Runtime(configured(tmp_path, "live"), bot=bot, model_client=model)

    async def execute(event, call):
        return ToolResult("ok", "本地排名已查询", {"facts": {"count": 2}})

    runtime.execute_call = execute
    assert (await runtime.receive(packet("#harness #发言排行 然后 分析一下")))["route"] == "mixed"
    await asyncio.gather(*tuple(runtime.tasks))
    assert len(model.calls) == 1
    assert len(bot.sent) == 2
    assert str(bot.sent[0][1]) == "本地排名已查询"
    assert str(bot.sent[1][1]) == "测试答复"
    assert len(runtime.store.tool_results("group:102")) == 1
    await runtime.close()
