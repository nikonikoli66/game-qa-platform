package com.gameqa.orchestrator.repo;

import java.time.Instant;
import java.util.List;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.transaction.annotation.Transactional;

import com.gameqa.orchestrator.domain.TaskEntity;
import com.gameqa.orchestrator.domain.TaskStatus;

public interface TaskRepository extends JpaRepository<TaskEntity, String> {

    List<TaskEntity> findByBatchIdOrderByCreatedAtAsc(String batchId);

    long countByBatchIdAndStatus(String batchId, TaskStatus status);

    /**
     * 数据库级 CAS：只有当前状态等于 expected 时才会更新成功。
     * 返回 1 = 抢到，返回 0 = 已被别的线程处理或已被取消。
     * 这是"同一任务不会被跑两遍"的真正保证，比应用层加锁更可靠（多实例部署也成立）。
     */
    @Modifying(clearAutomatically = true, flushAutomatically = true)
    @Transactional
    @Query("""
            update TaskEntity t
               set t.status = :next, t.startedAt = :now
             where t.id = :id and t.status = :expected
            """)
    int compareAndSetStatus(@Param("id") String id,
                            @Param("expected") TaskStatus expected,
                            @Param("next") TaskStatus next,
                            @Param("now") Instant now);

    @Modifying(clearAutomatically = true, flushAutomatically = true)
    @Transactional
    @Query("""
            update TaskEntity t
               set t.status = :next, t.reason = :reason, t.finishedAt = :now
             where t.id = :id and t.status = :expected
            """)
    int compareAndSetStatusWithReason(@Param("id") String id,
                                      @Param("expected") TaskStatus expected,
                                      @Param("next") TaskStatus next,
                                      @Param("reason") String reason,
                                      @Param("now") Instant now);

    /**
     * 批量取消：只作用于 PENDING。已在跑的任务不打断（打断会把执行器状态搞脏），
     * 这是刻意的取舍，接口会明确告知。
     */
    @Modifying(clearAutomatically = true, flushAutomatically = true)
    @Transactional
    @Query("""
            update TaskEntity t
               set t.status = :next, t.reason = :reason, t.finishedAt = :now
             where t.batchId = :batchId and t.status = :expected
            """)
    int cancelPending(@Param("batchId") String batchId,
                      @Param("expected") TaskStatus expected,
                      @Param("next") TaskStatus next,
                      @Param("reason") String reason,
                      @Param("now") Instant now);
}
