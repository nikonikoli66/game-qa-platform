package com.gameqa.orchestrator.dto;

import java.time.Instant;

/**
 * 批次进度视图（每次查询由任务状态实时推导，不信任存量计数器）。
 *
 * @param progress 0.0~1.0 的完成度
 */
public record BatchView(
        String batchId,
        String status,
        int total,
        int passed,
        int failed,
        int canceled,
        int running,
        int pending,
        double progress,
        long elapsedMs,
        Instant createdAt
) {
}
