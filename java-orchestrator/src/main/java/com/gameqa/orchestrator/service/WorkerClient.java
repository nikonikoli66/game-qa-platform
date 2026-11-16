package com.gameqa.orchestrator.service;

import java.util.Map;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

import com.gameqa.orchestrator.dto.TaskResult;

/**
 * Python 执行器的 HTTP 客户端。
 *
 * 契约：POST /api/worker/cases/{caseId}/execute  {"batchId": "..."}
 * 返回的 batchId 会带进 Python 侧的执行记录里，所以 Java 侧调度的批次
 * 在 Streamlit 看板的「版本验证」里也能看到，两条链路的数据是打通的。
 */
@Component
public class WorkerClient {

    private static final Logger log = LoggerFactory.getLogger(WorkerClient.class);

    private final RestClient restClient;

    public WorkerClient(@Qualifier("workerRestClient") RestClient restClient) {
        this.restClient = restClient;
    }

    public TaskResult execute(String batchId, String caseId) {
        return restClient.post()
                .uri("/api/worker/cases/{caseId}/execute", caseId)
                .contentType(MediaType.APPLICATION_JSON)
                .body(Map.of("batchId", batchId))
                .retrieve()
                .body(TaskResult.class);
    }

    public boolean ping() {
        try {
            Map<?, ?> body = restClient.get().uri("/api/worker/health").retrieve().body(Map.class);
            return body != null && "ok".equals(body.get("status"));
        } catch (RuntimeException ex) {
            log.debug("执行器健康检查失败: {}", ex.getMessage());
            return false;
        }
    }
}
