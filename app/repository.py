"""数据访问层（Repository）：所有 SQL 都收敛在这里，上层不写裸 SQL。"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Iterable, Sequence

from app.db import get_db


def new_id(prefix: str = "") -> str:
    raw = uuid.uuid4().hex[:12]
    return f"{prefix}{raw}" if prefix else raw


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _loads(text: Any, default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


# ============================================================ 需求
def save_requirement(name: str, content: str, domain: str = "") -> str:
    rid = new_id("req_")
    get_db().execute(
        "INSERT INTO requirements (id, name, domain, content, created_at) VALUES (?,?,?,?,?)",
        (rid, name, domain, content, now()),
    )
    return rid


def list_requirements() -> list[dict[str, Any]]:
    return get_db().query("SELECT * FROM requirements ORDER BY created_at DESC")


def get_requirement(rid: str) -> dict[str, Any] | None:
    return get_db().query_one("SELECT * FROM requirements WHERE id=?", (rid,))


# ============================================================ 用例
def save_case(case: dict[str, Any], req_id: str = "") -> str:
    cid = case.get("id") or new_id("case_")
    get_db().execute(
        """INSERT INTO cases (id, req_id, title, category, priority, precondition,
            steps_json, expected, risk_tags, api_refs, status, score, source,
            fingerprint, reject_reasons, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            cid,
            req_id or case.get("req_id", ""),
            case.get("title", ""),
            case.get("category", ""),
            case.get("priority", ""),
            case.get("precondition", ""),
            _dumps(case.get("steps", [])),
            case.get("expected", ""),
            _dumps(case.get("risk_tags", [])),
            _dumps(case.get("api_refs", [])),
            case.get("status", "valid"),
            float(case.get("score", 0) or 0),
            case.get("source", "ai"),
            case.get("fingerprint", ""),
            _dumps(case.get("reject_reasons", [])),
            now(),
        ),
    )
    return cid


def bulk_save_cases(cases: Iterable[dict[str, Any]], req_id: str = "") -> int:
    n = 0
    for c in cases:
        save_case(c, req_id)
        n += 1
    return n


def _row_to_case(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["steps"] = _loads(row.get("steps_json"), [])
    out["risk_tags"] = _loads(row.get("risk_tags"), [])
    out["api_refs"] = _loads(row.get("api_refs"), [])
    out["reject_reasons"] = _loads(row.get("reject_reasons"), [])
    return out


def list_cases(req_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM cases WHERE 1=1"
    params: list[Any] = []
    if req_id:
        sql += " AND req_id=?"
        params.append(req_id)
    if status:
        sql += " AND status=?"
        params.append(status)
    sql += " ORDER BY created_at DESC, score DESC"
    return [_row_to_case(r) for r in get_db().query(sql, params)]


def get_case(case_id: str) -> dict[str, Any] | None:
    row = get_db().query_one("SELECT * FROM cases WHERE id=?", (case_id,))
    return _row_to_case(row) if row else None


def update_case(case_id: str, **fields: Any) -> None:
    allowed = {"title", "category", "priority", "precondition", "expected", "status", "score"}
    sets, params = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            params.append(v)
    if "steps" in fields:
        sets.append("steps_json=?")
        params.append(_dumps(fields["steps"]))
    if not sets:
        return
    params.append(case_id)
    get_db().execute(f"UPDATE cases SET {', '.join(sets)} WHERE id=?", params)


def count_cases(status: str | None = None) -> int:
    if status:
        return int(get_db().scalar("SELECT COUNT(*) FROM cases WHERE status=?", (status,), 0))
    return int(get_db().scalar("SELECT COUNT(*) FROM cases", (), 0))


def clear_cases() -> None:
    get_db().execute("DELETE FROM cases")
    get_db().execute("DELETE FROM requirements")


def clear_runs() -> None:
    get_db().execute("DELETE FROM runs")
    get_db().execute("DELETE FROM defects")


def latest_telemetry_batch() -> str:
    return str(get_meta("last_telemetry_batch", "") or "")


# ============================================================ 脚本
def save_script(case_id: str, path: str, code: str, version: int = 1,
                precheck: dict[str, Any] | None = None) -> str:
    sid = new_id("scr_")
    get_db().execute(
        """INSERT INTO scripts (id, case_id, path, code, version, precheck_json, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (sid, case_id, path, code, version, _dumps(precheck or {}), now()),
    )
    return sid


def latest_script(case_id: str) -> dict[str, Any] | None:
    return get_db().query_one(
        "SELECT * FROM scripts WHERE case_id=? ORDER BY version DESC, created_at DESC", (case_id,)
    )


# ============================================================ 执行记录
def create_run(batch_id: str, case_id: str, case_title: str, script_id: str) -> str:
    rid = new_id("run_")
    get_db().execute(
        """INSERT INTO runs (id, batch_id, case_id, script_id, case_title, status,
            failure_kind, duration_ms, attempts, log_excerpt, heal_note, started_at, finished_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (rid, batch_id, case_id, script_id, case_title, "running", "", 0, 0, "", "", now(), ""),
    )
    return rid


def finish_run(run_id: str, status: str, failure_kind: str, duration_ms: int,
               attempts: int, log_excerpt: str, heal_note: str = "") -> None:
    get_db().execute(
        """UPDATE runs SET status=?, failure_kind=?, duration_ms=?, attempts=?,
           log_excerpt=?, heal_note=?, finished_at=? WHERE id=?""",
        (status, failure_kind, duration_ms, attempts, log_excerpt, heal_note, now(), run_id),
    )


def list_runs(batch_id: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    if batch_id:
        return get_db().query(
            "SELECT * FROM runs WHERE batch_id=? ORDER BY started_at DESC LIMIT ?",
            (batch_id, limit),
        )
    return get_db().query("SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,))


def distinct_batches(limit: int = 20) -> list[str]:
    rows = get_db().query(
        "SELECT batch_id, MAX(started_at) AS t FROM runs GROUP BY batch_id ORDER BY t DESC LIMIT ?",
        (limit,),
    )
    return [r["batch_id"] for r in rows]


def previous_batch(batch_id: str) -> str | None:
    batches = distinct_batches(30)
    if batch_id not in batches:
        return None
    idx = batches.index(batch_id)
    return batches[idx + 1] if idx + 1 < len(batches) else None


# ============================================================ 缺陷
def _text(value: Any) -> str:
    """防御性转换：TEXT 列只接受字符串。

    真实接入大模型后踩过：模型把 repro_steps 返回成 JSON 数组，
    直接绑参会让 sqlite3 抛 ProgrammingError 并中断整批任务。
    分析层已做归一化，这里是最后一道网 —— 数据层不该被上游的脏类型击穿。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "\n".join(str(item) for item in value)
    return str(value)


def save_defect(run_id: str, case_id: str, defect: dict[str, Any]) -> str:
    did = new_id("bug_")
    get_db().execute(
        """INSERT INTO defects (id, run_id, case_id, title, category, severity,
            description, repro_steps, root_cause, evidence, status, occurrences, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            did, run_id, case_id,
            _text(defect.get("title")), _text(defect.get("category")),
            _text(defect.get("severity")),
            _text(defect.get("description")), _text(defect.get("repro_steps")),
            _text(defect.get("root_cause")), _text(defect.get("evidence")),
            _text(defect.get("status")) or "open", int(defect.get("occurrences") or 1), now(),
        ),
    )
    return did


def find_open_defect(case_id: str, category: str) -> dict[str, Any] | None:
    """同一用例 + 同一失败分类视为同根因，用于避免每跑一次就重复开单。"""
    if not case_id:
        return None
    return get_db().query_one(
        """SELECT * FROM defects WHERE case_id=? AND category=? AND status='open'
           ORDER BY created_at DESC LIMIT 1""",
        (case_id, category),
    )


def list_defects(limit: int = 200) -> list[dict[str, Any]]:
    return get_db().query("SELECT * FROM defects ORDER BY created_at DESC LIMIT ?", (limit,))


# ============================================================ 埋点数据
def insert_stage_sessions(rows: Sequence[dict[str, Any]]) -> int:
    params = [
        (
            new_id("ss_"), r["batch_id"], r["player_id"], int(r["stage_id"]), r["result"],
            int(r["duration_ms"]), int(r["deaths"]), float(r["progress_at_fail"]),
            int(r["skill_casts"]), int(r["items_used"]), r.get("ts") or now(),
        )
        for r in rows
    ]
    return get_db().executemany(
        """INSERT INTO stage_sessions (id, batch_id, player_id, stage_id, result,
            duration_ms, deaths, progress_at_fail, skill_casts, items_used, ts)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        params,
    )


def insert_item_usage(rows: Sequence[dict[str, Any]]) -> int:
    params = [
        (new_id("iu_"), r["batch_id"], r["player_id"], int(r["stage_id"]),
         r["item_id"], int(r["cnt"]), r.get("ts") or now())
        for r in rows
    ]
    return get_db().executemany(
        """INSERT INTO item_usage (id, batch_id, player_id, stage_id, item_id, cnt, ts)
           VALUES (?,?,?,?,?,?,?)""",
        params,
    )


def clear_telemetry(batch_id: str | None = None) -> None:
    if batch_id:
        get_db().execute("DELETE FROM stage_sessions WHERE batch_id=?", (batch_id,))
        get_db().execute("DELETE FROM item_usage WHERE batch_id=?", (batch_id,))
    else:
        get_db().execute("DELETE FROM stage_sessions")
        get_db().execute("DELETE FROM item_usage")


# ============================================================ meta
def set_meta(k: str, v: Any) -> None:
    db = get_db()
    if db.query_one("SELECT k FROM meta WHERE k=?", (k,)):
        db.execute("UPDATE meta SET v=? WHERE k=?", (_dumps(v), k))
    else:
        db.execute("INSERT INTO meta (k, v) VALUES (?,?)", (k, _dumps(v)))


def get_meta(k: str, default: Any = None) -> Any:
    row = get_db().query_one("SELECT v FROM meta WHERE k=?", (k,))
    return _loads(row["v"], default) if row else default
