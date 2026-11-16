package com.gameqa.orchestrator;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;
import org.springframework.scheduling.annotation.EnableAsync;

/**
 * 测试任务编排服务。
 *
 * 职责边界（为什么这个模块用 Java）：
 *   并发调度（线程池 + 队列）、任务状态机、幂等提交、可取消 —— 这些都是强类型、
 *   事务、并发原语密集的场景，Spring 生态在这里比 Python 单机脚本更合适；
 *   而"用例生成 / 脚本执行 / 数据分析"留在 Python（AI 与数据生态在那儿）。
 *
 * 两侧通过 HTTP 契约通信，Python 单栈仍可独立运行，本服务是可选加速层。
 */
@SpringBootApplication
@EnableAsync
@ConfigurationPropertiesScan
public class OrchestratorApplication {

    public static void main(String[] args) {
        SpringApplication.run(OrchestratorApplication.class, args);
    }
}
