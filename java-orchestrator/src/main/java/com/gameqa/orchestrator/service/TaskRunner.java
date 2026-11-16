package com.gameqa.orchestrator.service;

import java.time.Instant;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Service;

import com.gameqa.orchestrator.domain.TaskEntity;
import com.gameqa.orchestrator.domain.TaskStatus;
import com.gameqa.orchestrator.dto.TaskResult;
import com.gameqa.orchestrator.repo.TaskRepository;

/**
 * 单条任务的执行单元（跑在 orchestratorExecutor 线程池里）。
 *
 * 关键点：先用 DB 级 CAS 把 PENDING 抢成 RUNNING，抢不到就直接放弃。
 * 这样即使同一条任务被投递两次（重试、重复提交），也只会被执行一次 —— 幂等不靠调用方自觉。
 */
@Service
public class TaskRunner {

    private static final Logger log = LoggerFactory.getLogger(TaskRunner.class);

    private final TaskRepository taskRepository;
    private final WorkerClient workerClient;

    public TaskRunner(TaskRepository taskRepository, WorkerClient workerClient) {
        this.taskRepository = taskRepository;
        this.workerClient = workerClient;
    }

    @Async("orchestratorExecutor")
    public void run(String batchId, String taskId) {
        int claimed = taskRepository.compareAndSetStatus(taskId, TaskStatus.PENDING, TaskStatus.RUNNING, Instant.now());
        if (claimed == 0) {
            log.info("任务 {} 未被抢占（已取消或已被执行），跳过", taskId);
            return;
        }
        TaskEntity task = taskRepository.findById(taskId).orElse(null);
        if (task == null) {
            log.warn("任务 {} 抢到执行权但查不到实体，可能已被清理", taskId);
            return;
        }

        long startedAt = System.currentTimeMillis();
        try {
            TaskResult result = workerClient.execute(batchId, task.getCaseId());
            finish(task, result, startedAt);
        } catch (RuntimeException ex) {
            String reason = "调用执行器失败：" + ex.getClass().getSimpleName() + " - " + ex.getMessage();
            log.warn("任务 {} 执行异常: {}", taskId, reason);
            task.applyResult(TaskStatus.FAILED, "WORKER_ERROR", truncate(reason),
                    System.currentTimeMillis() - startedAt, 0, null, Instant.now());
            taskRepository.save(task);
        }
    }

    private void finish(TaskEntity task, TaskResult result, long startedAt) {
        TaskStatus next = TaskStatus.fromWorkerStatus(result.status());
        if (!task.getStatus().canTransitTo(next)) {
            // 状态机兜底：非法迁移一律不动，宁可留痕也不写脏数据
            log.warn("任务 {} 当前状态 {} 不允许迁移到 {}，已忽略结果", task.getId(), task.getStatus(), next);
            return;
        }
        long duration = result.durationMs() > 0 ? result.durationMs() : System.currentTimeMillis() - startedAt;
        task.applyResult(next, result.failureKind(), truncate(result.reason()),
                duration, result.attempts(), result.runId(), Instant.now());
        if (result.caseTitle() != null && !result.caseTitle().isBlank()) {
            task.setCaseTitle(result.caseTitle());
        }
        taskRepository.save(task);
    }

    private static String truncate(String text) {
        if (text == null) {
            return null;
        }
        return text.length() <= 480 ? text : text.substring(0, 480) + "…";
    }
}
