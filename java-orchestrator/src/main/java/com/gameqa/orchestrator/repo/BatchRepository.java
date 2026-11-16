package com.gameqa.orchestrator.repo;

import java.util.Optional;

import org.springframework.data.jpa.repository.JpaRepository;

import com.gameqa.orchestrator.domain.BatchEntity;

public interface BatchRepository extends JpaRepository<BatchEntity, String> {

    Optional<BatchEntity> findByClientRequestId(String clientRequestId);
}
