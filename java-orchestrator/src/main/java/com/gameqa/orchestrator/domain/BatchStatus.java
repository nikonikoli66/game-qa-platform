package com.gameqa.orchestrator.domain;

/**
 * 批次状态（由任务状态推导，不单独存储，避免出现"批次说完成了但还有任务在跑"的不一致）。
 */
public enum BatchStatus {
    RUNNING,
    FINISHED,
    CANCELED
}
