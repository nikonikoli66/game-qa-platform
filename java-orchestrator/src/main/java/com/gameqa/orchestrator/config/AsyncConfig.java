package com.gameqa.orchestrator.config;

import java.util.concurrent.ThreadPoolExecutor;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;

/**
 * 并发调度：用固定大小的线程池把「同时跑几条用例」变成可控参数。
 *
 * 两个刻意的选择：
 *   1. core == max：避免线程数动态伸缩导致负载不可预测（测试执行的耗时方差本来就大）；
 *   2. AbortPolicy 而不是 CallerRuns：队列满时让任务快速失败并留下原因，
 *      而不是把 Web 线程拖去跑用例（那会阻塞提交接口，故障会横向传染）。
 */
@Configuration
public class AsyncConfig {

    @Bean(name = "orchestratorExecutor")
    public ThreadPoolTaskExecutor orchestratorExecutor(OrchestratorProperties props) {
        ThreadPoolTaskExecutor executor = new ThreadPoolTaskExecutor();
        executor.setCorePoolSize(props.concurrency());
        executor.setMaxPoolSize(props.concurrency());
        executor.setQueueCapacity(props.maxQueueSize());
        executor.setThreadNamePrefix("orch-");
        executor.setRejectedExecutionHandler(new ThreadPoolExecutor.AbortPolicy());
        executor.setWaitForTasksToCompleteOnShutdown(true);
        executor.setAwaitTerminationSeconds(30);
        executor.initialize();
        return executor;
    }
}
