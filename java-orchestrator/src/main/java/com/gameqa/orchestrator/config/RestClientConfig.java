package com.gameqa.orchestrator.config;

import java.time.Duration;

import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.web.client.RestClient;

/**
 * 调用 Python 执行器的 HTTP 客户端。
 *
 * 用 RestClient（同步、轻量）而不是 WebClient：
 * 调用方本身就是线程池里的工作线程，天然的"一请求一线程"，引入响应式只会增加复杂度。
 */
@Configuration
public class RestClientConfig {

    @Bean
    @Qualifier("workerRestClient")
    public RestClient workerRestClient(RestClient.Builder builder, OrchestratorProperties props) {
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(Duration.ofMillis(props.connectTimeoutMs()));
        factory.setReadTimeout(Duration.ofMillis(props.readTimeoutMs()));
        return builder
                .baseUrl(props.workerBaseUrl())
                .requestFactory(factory)
                .build();
    }
}
