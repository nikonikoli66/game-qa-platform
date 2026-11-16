package com.gameqa.orchestrator.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * 编排服务配置。
 *
 * @param workerBaseUrl  Python 执行器地址（app/worker_api.py）
 * @param concurrency    并发执行上限，直接决定同时有多少条用例在跑
 * @param connectTimeoutMs 连接超时（毫秒）
 * @param readTimeoutMs    读取超时（毫秒）——单条用例含异步轮询，必须给足
 * @param maxQueueSize     排队上限，超过则拒绝并让任务快速失败，避免请求堆积
 */
@ConfigurationProperties(prefix = "orchestrator")
public record OrchestratorProperties(
        String workerBaseUrl,
        int concurrency,
        int connectTimeoutMs,
        int readTimeoutMs,
        int maxQueueSize
) {
}
