package com.gameqa.orchestrator.dto;

import java.util.List;

import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.Size;

/**
 * 提交批次。
 *
 * @param caseIds         要执行的用例 ID 列表（来自 Python 侧用例库）
 * @param clientRequestId 调用方生成的请求 ID，用于幂等：同一个 ID 重复提交只受理一次
 */
public record CreateBatchRequest(
        @NotEmpty(message = "caseIds 不能为空")
        @Size(max = 200, message = "单批次最多 200 条用例")
        List<String> caseIds,
        String clientRequestId
) {
}
