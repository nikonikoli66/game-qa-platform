package com.gameqa.orchestrator.dto;

/**
 * Python 执行器返回的单条用例结果。
 * 字段名与 app/worker_api.py 的 JSON 输出严格对应。
 */
public record TaskResult(
        String caseId,
        String caseTitle,
        String status,
        String failureKind,
        String reason,
        long durationMs,
        int attempts,
        String runId
) {
}
