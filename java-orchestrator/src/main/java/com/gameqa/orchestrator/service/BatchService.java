package com.gameqa.orchestrator.service;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.NoSuchElementException;
import java.util.Objects;
import java.util.Optional;
import java.util.UUID;
import java.util.concurrent.RejectedExecutionException;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.data.domain.Sort;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;

import com.gameqa.orchestrator.domain.BatchEntity;
import com.gameqa.orchestrator.domain.BatchStatus;
import com.gameqa.orchestrator.domain.TaskEntity;
import com.gameqa.orchestrator.domain.TaskStatus;
import com.gameqa.orchestrator.dto.BatchView;
import com.gameqa.orchestrator.dto.CancelResponse;
import com.gameqa.orchestrator.dto.CreateBatchRequest;
import com.gameqa.orchestrator.dto.CreateBatchResponse;
import com.gameqa.orchestrator.dto.TaskView;
import com.gameqa.orchestrator.repo.BatchRepository;
import com.gameqa.orchestrator.repo.TaskRepository;

/**
 * 批次编排：创建 / 派发 / 查询进度 / 取消。
 */
@Service
public class BatchService {

    private static final Logger log = LoggerFactory.getLogger(BatchService.class);

    private final BatchRepository batchRepository;
    private final TaskRepository taskRepository;
    private final TaskRunner taskRunner;
    private final long concurrency;

    public BatchService(BatchRepository batchRepository,
                        TaskRepository taskRepository,
                        TaskRunner taskRunner,
                        com.gameqa.orchestrator.config.OrchestratorProperties props) {
        this.batchRepository = batchRepository;
        this.taskRepository = taskRepository;
        this.taskRunner = taskRunner;
        this.concurrency = props.concurrency();
    }

    @Transactional
    public CreateBatchResponse create(CreateBatchRequest request) {
        List<String> caseIds = request.caseIds().stream()
                .filter(Objects::nonNull)
                .map(String::trim)
                .filter(id -> !id.isEmpty())
                .distinct()
                .toList();
        if (caseIds.isEmpty()) {
            throw new IllegalArgumentException("caseIds 去重后为空，没有可执行的用例");
        }

        String requestId = normalizeRequestId(request.clientRequestId());
        if (requestId != null) {
            Optional<BatchEntity> hit = batchRepository.findByClientRequestId(requestId);
            if (hit.isPresent()) {
                BatchEntity existed = hit.get();
                log.info("幂等命中：clientRequestId={} 已有批次 {}", requestId, existed.getId());
                return new CreateBatchResponse(existed.getId(), true,
                        "幂等命中：该 clientRequestId 已提交过，返回原批次而不是重复执行",
                        existed.getCaseCount());
            }
        }

        String batchId = "jb_" + UUID.randomUUID().toString().replace("-", "").substring(0, 12);
        Instant now = Instant.now();
        try {
            batchRepository.saveAndFlush(new BatchEntity(batchId, requestId, caseIds.size(), now));
        } catch (DataIntegrityViolationException ex) {
            // 并发下两个相同 clientRequestId 的请求同时越过了上层判重，靠唯一约束兜底
            BatchEntity existed = batchRepository.findByClientRequestId(requestId)
                    .orElseThrow(() -> ex);
            log.warn("幂等由唯一约束兜底：clientRequestId={} 批次 {}", requestId, existed.getId());
            return new CreateBatchResponse(existed.getId(), true,
                    "幂等命中（并发场景由数据库唯一约束兜底）", existed.getCaseCount());
        }

        List<TaskEntity> tasks = new ArrayList<>();
        int index = 1;
        for (String caseId : caseIds) {
            String taskId = "jt_" + batchId.substring(3) + "_" + String.format("%02d", index++);
            tasks.add(new TaskEntity(taskId, batchId, caseId, "", now));
        }
        taskRepository.saveAll(tasks);

        // 必须等事务提交后再派发：否则异步线程可能查到还没提交的任务行，出现"任务丢失"的假象
        TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
            @Override
            public void afterCommit() {
                dispatch(tasks);
            }
        });

        return new CreateBatchResponse(batchId, false, "已受理 " + tasks.size() + " 条用例任务", tasks.size());
    }

    private void dispatch(List<TaskEntity> tasks) {
        int rejected = 0;
        for (TaskEntity task : tasks) {
            try {
                taskRunner.run(task.getBatchId(), task.getId());
            } catch (RejectedExecutionException ex) {
                rejected++;
                taskRepository.compareAndSetStatusWithReason(
                        task.getId(), TaskStatus.PENDING, TaskStatus.FAILED,
                        "调度队列已满（并发上限 " + concurrency + "），任务未执行",
                        Instant.now());
            }
        }
        if (rejected > 0) {
            log.warn("批次 {} 有 {} 条任务因队列满被拒绝", tasks.get(0).getBatchId(), rejected);
        }
    }

    @Transactional(readOnly = true)
    public BatchView view(String batchId) {
        BatchEntity batch = batchRepository.findById(batchId)
                .orElseThrow(() -> new NoSuchElementException("批次不存在: " + batchId));
        List<TaskEntity> tasks = taskRepository.findByBatchIdOrderByCreatedAtAsc(batchId);

        int passed = 0;
        int failed = 0;
        int canceled = 0;
        int running = 0;
        int pending = 0;
        Instant lastFinished = null;
        for (TaskEntity task : tasks) {
            switch (task.getStatus()) {
                case PASSED -> passed++;
                case FAILED -> failed++;
                case CANCELED -> canceled++;
                case RUNNING -> running++;
                case PENDING -> pending++;
            }
            if (task.getFinishedAt() != null
                    && (lastFinished == null || task.getFinishedAt().isAfter(lastFinished))) {
                lastFinished = task.getFinishedAt();
            }
        }

        int total = tasks.size();
        int done = passed + failed + canceled;
        BatchStatus status = (pending + running) > 0
                ? BatchStatus.RUNNING
                : (total > 0 && done == total && passed + failed == 0 ? BatchStatus.CANCELED : BatchStatus.FINISHED);
        long elapsed = (lastFinished != null ? lastFinished : Instant.now()).toEpochMilli()
                - batch.getCreatedAt().toEpochMilli();

        return new BatchView(batchId, status.name(), total, passed, failed, canceled, running, pending,
                total == 0 ? 0.0 : round((double) done / total), Math.max(elapsed, 0), batch.getCreatedAt());
    }

    @Transactional(readOnly = true)
    public List<TaskView> tasks(String batchId) {
        if (!batchRepository.existsById(batchId)) {
            throw new NoSuchElementException("批次不存在: " + batchId);
        }
        return taskRepository.findByBatchIdOrderByCreatedAtAsc(batchId).stream()
                .map(t -> new TaskView(t.getId(), t.getCaseId(), t.getCaseTitle(), t.getStatus().name(),
                        t.getFailureKind(), t.getReason(), t.getDurationMs(), t.getAttempts(), t.getRunId()))
                .toList();
    }

    @Transactional
    public CancelResponse cancel(String batchId) {
        if (!batchRepository.existsById(batchId)) {
            throw new NoSuchElementException("批次不存在: " + batchId);
        }
        int canceled = taskRepository.cancelPending(batchId, TaskStatus.PENDING, TaskStatus.CANCELED,
                "调用方主动取消", Instant.now());
        String message = canceled > 0
                ? "已取消 " + canceled + " 条未开始的任务；执行中的任务不会被中断"
                : "没有可取消的任务（可能都已开始执行或已结束）";
        return new CancelResponse(batchId, canceled, message);
    }

    @Transactional(readOnly = true)
    public List<BatchView> recent(int limit) {
        return batchRepository.findAll(Sort.by(Sort.Direction.DESC, "createdAt")).stream()
                .limit(Math.max(1, Math.min(limit, 50)))
                .map(b -> view(b.getId()))
                .toList();
    }

    private static String normalizeRequestId(String raw) {
        if (raw == null) {
            return null;
        }
        String trimmed = raw.trim();
        return trimmed.isEmpty() ? null : trimmed;
    }

    private static double round(double value) {
        return Math.round(value * 1000.0) / 1000.0;
    }
}
