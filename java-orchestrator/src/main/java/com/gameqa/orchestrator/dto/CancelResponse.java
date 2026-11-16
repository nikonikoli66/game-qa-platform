package com.gameqa.orchestrator.dto;

public record CancelResponse(
        String batchId,
        int canceled,
        String message
) {
}
