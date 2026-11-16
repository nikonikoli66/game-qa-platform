package com.gameqa.orchestrator.dto;

/**
 * @param idempotent true 表示命中幂等（本次没有新建批次，返回的是原批次）
 */
public record CreateBatchResponse(
        String batchId,
        boolean idempotent,
        String message,
        int taskCount
) {
}
