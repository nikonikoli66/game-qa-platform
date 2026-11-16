package com.gameqa.orchestrator.domain;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

/**
 * 状态机单测：这些非法迁移必须被拦住，否则会出现"失败的任务被改成通过"这类脏数据。
 */
class TaskStatusTest {

    @Test
    @DisplayName("正常流转：PENDING → RUNNING → PASSED/FAILED 都合法")
    void happyPath() {
        assertTrue(TaskStatus.PENDING.canTransitTo(TaskStatus.RUNNING));
        assertTrue(TaskStatus.RUNNING.canTransitTo(TaskStatus.PASSED));
        assertTrue(TaskStatus.RUNNING.canTransitTo(TaskStatus.FAILED));
    }

    @Test
    @DisplayName("未开始的任务可以被取消")
    void pendingCanBeCanceled() {
        assertTrue(TaskStatus.PENDING.canTransitTo(TaskStatus.CANCELED));
    }

    @Test
    @DisplayName("执行中的任务不允许被直接取消（避免中断执行器）")
    void runningCannotBeCanceled() {
        assertFalse(TaskStatus.RUNNING.canTransitTo(TaskStatus.CANCELED));
    }

    @Test
    @DisplayName("终态不可再变：已完成的任务不能被改写")
    void terminalStatesAreFinal() {
        for (TaskStatus terminal : new TaskStatus[]{TaskStatus.PASSED, TaskStatus.FAILED, TaskStatus.CANCELED}) {
            assertTrue(terminal.isTerminal());
            for (TaskStatus next : TaskStatus.values()) {
                assertFalse(terminal.canTransitTo(next),
                        terminal + " 不应允许迁移到 " + next);
            }
        }
    }

    @Test
    @DisplayName("跳级流转被拒绝：PENDING 不能直接变 PASSED")
    void noShortcut() {
        assertFalse(TaskStatus.PENDING.canTransitTo(TaskStatus.PASSED));
        assertFalse(TaskStatus.PENDING.canTransitTo(TaskStatus.FAILED));
    }

    @Test
    @DisplayName("执行器返回的状态字符串解析")
    void parseWorkerStatus() {
        assertTrue(TaskStatus.fromWorkerStatus("passed") == TaskStatus.PASSED);
        assertTrue(TaskStatus.fromWorkerStatus("PASSED") == TaskStatus.PASSED);
        assertTrue(TaskStatus.fromWorkerStatus("failed") == TaskStatus.FAILED);
        assertTrue(TaskStatus.fromWorkerStatus(null) == TaskStatus.FAILED);
    }
}
