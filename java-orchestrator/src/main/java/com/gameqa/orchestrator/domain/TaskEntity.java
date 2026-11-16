package com.gameqa.orchestrator.domain;

import java.time.Instant;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Index;
import jakarta.persistence.Table;
import jakarta.persistence.Version;

/**
 * 单条用例任务。
 *
 * 说明：状态迁移统一走 Repository 的 compareAndSetStatus（DB 级 CAS，带 status 条件的 UPDATE），
 * 而不是"读-改-写"，这样即使两个线程同时抢同一条任务，也只有一个能成功。
 * @Version 作为二次保护，防止其它走实体保存路径的更新互相覆盖。
 */
@Entity
@Table(name = "qa_task", indexes = @Index(name = "idx_task_batch", columnList = "batch_id"))
public class TaskEntity {

    @Id
    @Column(length = 40, nullable = false)
    private String id;

    @Column(name = "batch_id", length = 40, nullable = false)
    private String batchId;

    @Column(name = "case_id", length = 64, nullable = false)
    private String caseId;

    @Column(name = "case_title", length = 200)
    private String caseTitle;

    @Enumerated(EnumType.STRING)
    @Column(length = 16, nullable = false)
    private TaskStatus status;

    @Column(name = "failure_kind", length = 32)
    private String failureKind;

    @Column(name = "reason", length = 500)
    private String reason;

    @Column(name = "duration_ms")
    private long durationMs;

    @Column(name = "attempts")
    private int attempts;

    @Column(name = "run_id", length = 40)
    private String runId;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    @Column(name = "started_at")
    private Instant startedAt;

    @Column(name = "finished_at")
    private Instant finishedAt;

    @Version
    private long version;

    protected TaskEntity() {
    }

    public TaskEntity(String id, String batchId, String caseId, String caseTitle, Instant createdAt) {
        this.id = id;
        this.batchId = batchId;
        this.caseId = caseId;
        this.caseTitle = caseTitle;
        this.status = TaskStatus.PENDING;
        this.createdAt = createdAt;
    }

    public void applyResult(TaskStatus next, String failureKind, String reason,
                            long durationMs, int attempts, String runId, Instant finishedAt) {
        this.status = next;
        this.failureKind = failureKind;
        this.reason = reason;
        this.durationMs = durationMs;
        this.attempts = attempts;
        this.runId = runId;
        this.finishedAt = finishedAt;
    }

    public void setCaseTitle(String caseTitle) {
        this.caseTitle = caseTitle;
    }

    public String getId() {
        return id;
    }

    public String getBatchId() {
        return batchId;
    }

    public String getCaseId() {
        return caseId;
    }

    public String getCaseTitle() {
        return caseTitle;
    }

    public TaskStatus getStatus() {
        return status;
    }

    public String getFailureKind() {
        return failureKind;
    }

    public String getReason() {
        return reason;
    }

    public long getDurationMs() {
        return durationMs;
    }

    public int getAttempts() {
        return attempts;
    }

    public String getRunId() {
        return runId;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }

    public Instant getStartedAt() {
        return startedAt;
    }

    public Instant getFinishedAt() {
        return finishedAt;
    }
}
