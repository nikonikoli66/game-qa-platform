package com.gameqa.orchestrator.dto;

/**
 * 单条任务视图。
 */
public record TaskView(
        String taskId,
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
