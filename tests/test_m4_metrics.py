"""体验指标计算单测：难度断崖检测 + SQL 聚合。"""
from __future__ import annotations

import uuid

from app import repository
from app.m4_analytics import metrics


def _seed(batch_id: str) -> None:
    rows = []
    # 第 1 关：10 场全通
    for _ in range(10):
        rows.append({"batch_id": batch_id, "player_id": "p1", "stage_id": 1,
                     "result": "clear", "duration_ms": 30000, "deaths": 0,
                     "progress_at_fail": 0.0, "skill_casts": 6, "items_used": 0})
    # 第 2 关：10 场只过 2 场（断崖）
    for i in range(10):
        cleared = i < 2
        rows.append({"batch_id": batch_id, "player_id": "p1", "stage_id": 2,
                     "result": "clear" if cleared else "fail",
                     "duration_ms": 40000, "deaths": 0 if cleared else 2,
                     "progress_at_fail": 0.0 if cleared else 0.8,
                     "skill_casts": 8, "items_used": 2})
    # 第 3 关：和第 2 关差不多，不算断崖
    for i in range(10):
        cleared = i < 2
        rows.append({"batch_id": batch_id, "player_id": "p2", "stage_id": 3,
                     "result": "clear" if cleared else "fail",
                     "duration_ms": 42000, "deaths": 0 if cleared else 2,
                     "progress_at_fail": 0.0 if cleared else 0.75,
                     "skill_casts": 9, "items_used": 2})
    repository.insert_stage_sessions(rows)
    repository.insert_item_usage([
        {"batch_id": batch_id, "player_id": "p1", "stage_id": 2,
         "item_id": "potion_hp", "cnt": 3},
        {"batch_id": batch_id, "player_id": "p2", "stage_id": 2,
         "item_id": "potion_hp", "cnt": 1},
    ])


def test_stage_metrics_aggregation() -> None:
    batch_id = "t_" + uuid.uuid4().hex[:8]
    _seed(batch_id)
    stages = metrics.stage_metrics(batch_id)
    assert [s["stage_id"] for s in stages] == [1, 2, 3]
    assert stages[0]["clear_rate"] == 1.0
    assert stages[1]["clear_rate"] == 0.2
    assert stages[1]["avg_deaths"] > 0
    assert 0.7 < stages[1]["avg_progress_at_fail"] < 0.9


def test_cliff_detection() -> None:
    batch_id = "t_" + uuid.uuid4().hex[:8]
    _seed(batch_id)
    report = metrics.build_report(batch_id)
    cliffs = report["cliffs"]
    assert len(cliffs) == 1
    assert cliffs[0]["from_stage"] == 1 and cliffs[0]["to_stage"] == 2
    assert abs(cliffs[0]["drop"] - 0.8) < 1e-6


def test_report_overall_and_items() -> None:
    batch_id = "t_" + uuid.uuid4().hex[:8]
    _seed(batch_id)
    report = metrics.build_report(batch_id)
    assert report["total_sessions"] == 30
    assert 0 < report["overall_clear_rate"] < 1
    assert report["items"][0]["item_id"] == "potion_hp"
    assert report["hard_stage_count"] >= 2


def test_empty_batch() -> None:
    report = metrics.build_report("not_exist_batch")
    assert report["empty"] is True


def test_progress_histogram() -> None:
    batch_id = "t_" + uuid.uuid4().hex[:8]
    _seed(batch_id)
    hist = metrics.progress_histogram(batch_id)
    assert hist and all(0 <= h["bucket"] <= 9 for h in hist)
