"""Python 执行器 HTTP 接口单测（不依赖被测服，只验证协议契约）。

契约变更会让 Java 侧直接反序列化失败，所以这里把字段名钉死。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.worker_api import app

client = TestClient(app)


def test_health_contract() -> None:
    resp = client.get("/api/worker/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "mockServerUp" in body and "llmMode" in body


def test_case_list_contract() -> None:
    resp = client.get("/api/worker/cases?limit=5")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    if data:  # 用例库可能为空（干净环境）
        assert {"caseId", "title", "category", "priority"} <= set(data[0])


def test_execute_unknown_case_returns_404() -> None:
    resp = client.post("/api/worker/cases/not_exist_id/execute", json={"batchId": "jb_test"})
    assert resp.status_code == 404
    assert "用例不存在" in resp.json()["detail"]


def test_execute_response_fields_match_java_dto() -> None:
    """Java 侧 TaskResult 依赖这些字段名，缺一个就会反序列化失败。"""
    from app.worker_api import ExecuteRequest

    expected = {"caseId", "caseTitle", "status", "failureKind",
                "reason", "durationMs", "attempts", "runId"}
    assert set(ExecuteRequest.model_fields) == {"batchId", "account"}
    # 用 pydantic 的字段约束不到返回值，这里退一步：检查源码里的键名集合
    import inspect

    src = inspect.getsource(__import__("app.worker_api", fromlist=["x"]).execute)
    for field in expected:
        assert f'"{field}"' in src, f"响应缺少字段 {field}"
