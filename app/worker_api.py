"""Python 执行器 HTTP 接口 —— 供 Java 编排服务调用。

分工：
    Java（java-orchestrator）负责并发调度、任务状态机、幂等、取消；
    Python（本服务）负责真正执行一条用例：渲染脚本 → 预检 → 跑 pytest → 分类 → 开缺陷单。

为什么要拆成 HTTP 而不是 Java 直接调 Python 脚本？
    1. 进程隔离：用例执行会起 pytest 子进程、可能卡死，放在独立进程里不会拖垮调度器；
    2. 可独立扩缩容：执行器无状态，可以起多份，Java 侧只改 worker-base-url；
    3. 契约清晰：请求/响应是 JSON，语言无关，将来换成 Go 写的执行器也能对接。

启动：python -m uvicorn app.worker_api:app --host 127.0.0.1 --port 8100
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app import config, pipeline, repository
from app.m2_automation import env_manager, runner
from app.m3_defect import analyzer
from app.m3_defect.classifier import FailureKind

app = FastAPI(
    title="Game QA Worker",
    description="单条用例执行器（Java 编排服务的下游）",
    version="1.0.0",
)


class ExecuteRequest(BaseModel):
    batchId: str | None = None
    account: str | None = None


@app.get("/api/worker/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "mockServerUp": env_manager.server_alive(),
        "llmMode": config.llm_mode_label(),
        "validCases": repository.count_cases("valid"),
    }


@app.get("/api/worker/cases")
def list_cases(limit: int = 100) -> list[dict[str, Any]]:
    """给编排服务/脚本用的用例清单，便于提交批次前挑选 caseId。"""
    cases = repository.list_cases(status="valid")[: max(1, limit)]
    return [
        {
            "caseId": c["id"],
            "title": c["title"],
            "category": c["category"],
            "priority": c["priority"],
            "score": c["score"],
        }
        for c in cases
    ]


@app.post("/api/worker/cases/{case_id}/execute")
def execute(case_id: str, request: ExecuteRequest) -> dict[str, Any]:
    case = repository.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail=f"用例不存在: {case_id}")

    result = runner.execute(
        case, case_id, index=1,
        batch_id=request.batchId or "",
        account=request.account,
        tag=request.batchId or "",   # 并发下避免不同批次写同一个脚本文件
    )

    if not result.passed:
        kind = FailureKind(result.failure_kind)
        if kind in pipeline.DEFECT_KINDS:
            analyzer.create_defect(result.run_id, case, kind, result.log, result.account)

    return {
        "caseId": case_id,
        "caseTitle": case.get("title", ""),
        "status": "passed" if result.passed else "failed",
        "failureKind": result.failure_kind,
        "reason": result.reason,
        "durationMs": result.duration_ms,
        "attempts": result.attempts,
        "runId": result.run_id,
    }


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8100, log_level="warning")
