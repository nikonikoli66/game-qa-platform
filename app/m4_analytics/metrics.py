"""关卡体验指标计算（SQL 聚合 + 难度断崖检测算法）。

思路：把"这关太难"这种主观判断，拆成三个可量化指标：
    1. 通关率 clear_rate —— 卡不卡；
    2. 失败平均进度 avg_progress_at_fail —— 卡在哪（进度 80% 失败 vs 20% 失败，体验完全不同）；
    3. 相邻关卡通关率跃迁 drop —— 难度是不是断层式上升。
第 3 条是这个模块的"算法点"：单纯看通关率最低的关卡会误判（后期关卡天然更难），
看"跃迁"才能定位"策划数值抄错"的位置。
"""
from __future__ import annotations

from typing import Any

from app.db import get_db

CLIFF_DROP_THRESHOLD = 0.15   # 通关率相对上一关下降超过 15 个百分点 → 判定难度断崖
HARD_STAGE_CLEAR_RATE = 0.45  # 通关率低于 45% 视为高卡点


def stage_metrics(batch_id: str) -> list[dict[str, Any]]:
    rows = get_db().query(
        """
        SELECT stage_id,
               COUNT(*)                                              AS sessions,
               SUM(CASE WHEN result = 'clear' THEN 1 ELSE 0 END)     AS clears,
               AVG(duration_ms)                                      AS avg_duration_ms,
               AVG(deaths)                                           AS avg_deaths,
               AVG(CASE WHEN result <> 'clear' THEN progress_at_fail END) AS avg_progress_at_fail,
               AVG(items_used)                                       AS avg_items_used
        FROM stage_sessions
        WHERE batch_id = ?
        GROUP BY stage_id
        ORDER BY stage_id
        """,
        (batch_id,),
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        sessions = int(r["sessions"] or 1)
        clears = int(r["clears"] or 0)
        out.append({
            "stage_id": int(r["stage_id"]),
            "sessions": sessions,
            "clears": clears,
            "clear_rate": round(clears / sessions, 4),
            "avg_duration_s": round(float(r["avg_duration_ms"] or 0) / 1000, 1),
            "avg_deaths": round(float(r["avg_deaths"] or 0), 2),
            "avg_progress_at_fail": round(float(r["avg_progress_at_fail"] or 0), 3),
            "avg_items_used": round(float(r["avg_items_used"] or 0), 2),
        })
    return out


def detect_cliffs(stages: list[dict[str, Any]],
                  threshold: float = CLIFF_DROP_THRESHOLD) -> list[dict[str, Any]]:
    """相邻关卡通关率跃迁检测。"""
    cliffs: list[dict[str, Any]] = []
    for prev, cur in zip(stages, stages[1:]):
        drop = prev["clear_rate"] - cur["clear_rate"]
        if drop >= threshold:
            cliffs.append({
                "from_stage": prev["stage_id"],
                "to_stage": cur["stage_id"],
                "from_rate": prev["clear_rate"],
                "to_rate": cur["clear_rate"],
                "drop": round(drop, 4),
                "avg_deaths": cur["avg_deaths"],
                "avg_progress_at_fail": cur["avg_progress_at_fail"],
            })
    return cliffs


def progress_histogram(batch_id: str) -> list[dict[str, Any]]:
    """失败进度分布：0.0-0.1 ... 0.9-1.0，定位"卡在哪个阶段"。"""
    rows = get_db().query(
        """
        SELECT CAST(progress_at_fail * 10 AS INT) AS bucket, COUNT(*) AS cnt
        FROM stage_sessions
        WHERE batch_id = ? AND result <> 'clear' AND progress_at_fail > 0
        GROUP BY bucket
        ORDER BY bucket
        """,
        (batch_id,),
    )
    return [{"bucket": int(r["bucket"]), "label": f"{int(r['bucket']) * 10}-{int(r['bucket']) * 10 + 10}%",
             "count": int(r["cnt"])} for r in rows]


def item_usage(batch_id: str, total_players: int) -> list[dict[str, Any]]:
    rows = get_db().query(
        """
        SELECT item_id, SUM(cnt) AS total, COUNT(DISTINCT player_id) AS users
        FROM item_usage
        WHERE batch_id = ?
        GROUP BY item_id
        ORDER BY total DESC
        """,
        (batch_id,),
    )
    base = max(1, total_players)
    return [{
        "item_id": r["item_id"],
        "total_used": int(r["total"] or 0),
        "users": int(r["users"] or 0),
        "usage_rate": round(int(r["users"] or 0) / base, 4),
    } for r in rows]


def build_report(batch_id: str, total_players: int | None = None) -> dict[str, Any]:
    stages = stage_metrics(batch_id)
    if not stages:
        return {"batch_id": batch_id, "stages": [], "cliffs": [], "items": [], "empty": True}

    players = total_players or int(get_db().scalar(
        "SELECT COUNT(DISTINCT player_id) FROM stage_sessions WHERE batch_id=?", (batch_id,), 0
    ))
    total_sessions = sum(s["sessions"] for s in stages)
    total_clears = sum(s["clears"] for s in stages)
    cliffs = detect_cliffs(stages)
    items = item_usage(batch_id, players)
    hist = progress_histogram(batch_id)

    hardest = sorted(stages, key=lambda s: s["clear_rate"])[:3]
    low_usage = [i for i in items if i["usage_rate"] < 0.25]

    return {
        "batch_id": batch_id,
        "players": players,
        "total_sessions": total_sessions,
        "overall_clear_rate": round(total_clears / total_sessions, 4) if total_sessions else 0.0,
        "stages": stages,
        "cliffs": cliffs,
        "items": items,
        "item_low_usage": low_usage,
        "progress_hist": hist,
        "hard_stages": hardest,
        "hard_stage_count": sum(1 for s in stages if s["clear_rate"] < HARD_STAGE_CLEAR_RATE),
        "empty": False,
    }
