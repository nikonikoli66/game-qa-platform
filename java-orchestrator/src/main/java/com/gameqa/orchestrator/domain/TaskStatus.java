package com.gameqa.orchestrator.domain;

/**
 * 任务状态机。
 *
 * 把合法迁移写进枚举而不是散落在 if 里，好处是：
 *   1. 非法迁移（比如已失败的任务被改成通过）在这里就被拦住，可直接单测；
 *   2. 新增状态时必须显式处理所有分支，编译器会提醒你漏了 case。
 *
 *   PENDING ──► RUNNING ──► PASSED
 *      │            └────► FAILED
 *      └──────► CANCELED（只能取消未开始的）
 */
public enum TaskStatus {

    PENDING,
    RUNNING,
    PASSED,
    FAILED,
    CANCELED;

    public boolean isTerminal() {
        return this == PASSED || this == FAILED || this == CANCELED;
    }

    public boolean canTransitTo(TaskStatus next) {
        return switch (this) {
            case PENDING -> next == RUNNING || next == CANCELED;
            case RUNNING -> next == PASSED || next == FAILED;
            case PASSED, FAILED, CANCELED -> false;
        };
    }

    /** 任务结果是否算"通过"。 */
    public static TaskStatus fromWorkerStatus(String workerStatus) {
        return "passed".equalsIgnoreCase(workerStatus) ? PASSED : FAILED;
    }
}
