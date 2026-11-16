package com.gameqa.orchestrator.web;

import java.time.Instant;
import java.util.List;
import java.util.Map;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import com.gameqa.orchestrator.config.OrchestratorProperties;
import com.gameqa.orchestrator.dto.BatchView;
import com.gameqa.orchestrator.dto.CancelResponse;
import com.gameqa.orchestrator.dto.CreateBatchRequest;
import com.gameqa.orchestrator.dto.CreateBatchResponse;
import com.gameqa.orchestrator.dto.TaskView;
import com.gameqa.orchestrator.service.BatchService;
import com.gameqa.orchestrator.service.WorkerClient;

import jakarta.validation.Valid;

/**
 * 编排服务对外接口。
 *
 * 提交返回 202 Accepted 而不是 200 —— 语义上"已受理、异步执行"，
 * 调用方拿 batchId 去轮询进度，而不是把 HTTP 连接挂在那里等全部跑完。
 */
@RestController
@RequestMapping("/api/v1")
public class BatchController {

    private final BatchService batchService;
    private final WorkerClient workerClient;
    private final OrchestratorProperties properties;

    public BatchController(BatchService batchService, WorkerClient workerClient,
                           OrchestratorProperties properties) {
        this.batchService = batchService;
        this.workerClient = workerClient;
        this.properties = properties;
    }

    @PostMapping("/batches")
    public ResponseEntity<CreateBatchResponse> create(@Valid @RequestBody CreateBatchRequest request) {
        return ResponseEntity.accepted().body(batchService.create(request));
    }

    @GetMapping("/batches/{batchId}")
    public BatchView view(@PathVariable String batchId) {
        return batchService.view(batchId);
    }

    @GetMapping("/batches/{batchId}/tasks")
    public List<TaskView> tasks(@PathVariable String batchId) {
        return batchService.tasks(batchId);
    }

    @PostMapping("/batches/{batchId}/cancel")
    public CancelResponse cancel(@PathVariable String batchId) {
        return batchService.cancel(batchId);
    }

    @GetMapping("/batches")
    public List<BatchView> recent(@RequestParam(defaultValue = "10") int limit) {
        return batchService.recent(limit);
    }

    @GetMapping("/health")
    public Map<String, Object> health() {
        boolean workerUp = workerClient.ping();
        return Map.of(
                "status", workerUp ? "ok" : "degraded",
                "worker", properties.workerBaseUrl(),
                "workerUp", workerUp,
                "concurrency", properties.concurrency(),
                "time", Instant.now().toString()
        );
    }
}
