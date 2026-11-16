package com.gameqa.orchestrator.domain;

import java.time.Instant;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import jakarta.persistence.UniqueConstraint;

/**
 * 批次：一次提交包含多条用例任务。
 *
 * clientRequestId 上加了唯一约束 —— 这是幂等的兜底：
 * 即便应用层判重被并发绕过，数据库也会挡住第二次插入。
 */
@Entity
@Table(
        name = "qa_batch",
        uniqueConstraints = @UniqueConstraint(name = "uk_batch_client_request", columnNames = "client_request_id")
)
public class BatchEntity {

    @Id
    @Column(length = 40, nullable = false)
    private String id;

    @Column(name = "client_request_id", length = 80)
    private String clientRequestId;

    @Column(name = "case_count", nullable = false)
    private int caseCount;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    protected BatchEntity() {
        // JPA 需要无参构造
    }

    public BatchEntity(String id, String clientRequestId, int caseCount, Instant createdAt) {
        this.id = id;
        this.clientRequestId = clientRequestId;
        this.caseCount = caseCount;
        this.createdAt = createdAt;
    }

    public String getId() {
        return id;
    }

    public String getClientRequestId() {
        return clientRequestId;
    }

    public int getCaseCount() {
        return caseCount;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }
}
