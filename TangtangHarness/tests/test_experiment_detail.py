from fastapi.testclient import TestClient

from tangtang_harness.app import create_app
from tangtang_harness.config import HarnessConfig
from tangtang_harness.runtime import Runtime


def test_experiment_detail_is_read_only_preserves_full_body_and_redacts_credentials(tmp_path):
    rt = Runtime(HarnessConfig(root=tmp_path, mode="observe"))
    record = {
        "id": "example",
        "status": "offline",
        "model_calls": 0,
        "qq_writes": 0,
        "result": {"steps": [{"preview": {"payload": {
            "messages": [{"role": "user", "content": "完整实验正文"}],
            "api_key": "credential-sentinel",
        }}}]},
    }
    rt.store.set_setting("experiment:example", record)
    with TestClient(create_app(runtime=rt)) as client:
        response = client.get("/api/experiments/example")
        assert response.status_code == 200
        payload = response.json()["result"]["steps"][0]["preview"]["payload"]
        assert payload["messages"][0]["content"] == "完整实验正文"
        assert payload["api_key"] == "[redacted]"
        assert "credential-sentinel" not in response.text
        assert client.get("/api/experiments/missing").status_code == 404
    assert rt.store.get_setting("experiment:example") == record
    assert rt.store.requests() == []
    with rt.store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0] == 0
