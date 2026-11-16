"""极简数据库适配层。

支持 SQLite（默认，零配置）与 MySQL（部署时切换），业务代码统一用 `?` 占位符，
由本层自动转换为 MySQL 的 `%s`。
这样做的好处：本地不装任何数据库也能跑，生产想上 MySQL 只改一行配置。
"""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence
from urllib.parse import urlparse

from app import config

# 这些都是"等一下就好"的瞬时错误，重试即可；其它异常直接抛
_RETRYABLE = (
    "database is locked",
    "database table is locked",
    "attempt to write a readonly database",
    "disk i/o error",
)


class Database:
    def __init__(self, url: str | None = None) -> None:
        self.url = url or config.DB_URL
        self.dialect = "mysql" if self.url.startswith("mysql") else "sqlite"
        self._write_lock = threading.Lock()
        self._sqlite_path = ""
        if self.dialect == "sqlite":
            raw = self.url.split("sqlite:///", 1)[-1]
            p = Path(raw)
            if not p.is_absolute():
                p = config.ROOT_DIR / p
            p.parent.mkdir(parents=True, exist_ok=True)
            self._sqlite_path = str(p)
            self.url = f"sqlite:///{p.as_posix()}"
            self._enable_wal()

    # ------------------------------------------------------------------ 连接
    def _enable_wal(self) -> None:
        """WAL 是数据库级持久设置，只需设一次。

        踩过的坑：最初在**每个连接**上都执行 `PRAGMA journal_mode=WAL`，
        这个 pragma 需要独占锁，并发调用时和别的连接互相打架，
        表现为偶发的 `attempt to write a readonly database`（Java 编排并发 3 路时命中）。
        改成初始化时设一次 + 连接只设 busy_timeout，问题消失。
        """
        try:
            conn = sqlite3.connect(self._sqlite_path, timeout=10)
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
            finally:
                conn.close()
        except sqlite3.Error:
            # 设不上就退化为默认日志模式，靠 busy_timeout + 重试兜底
            pass

    def _retry(self, action: Callable[[], Any], attempts: int = 5) -> Any:
        last: Exception | None = None
        for i in range(attempts):
            try:
                return action()
            except sqlite3.OperationalError as exc:
                if not any(key in str(exc).lower() for key in _RETRYABLE):
                    raise
                last = exc
                time.sleep(0.04 * (2 ** i))
        raise last if last else RuntimeError("数据库操作失败")

    @contextmanager
    def _cursor(self) -> Iterator[Any]:
        if self.dialect == "sqlite":
            conn = sqlite3.connect(self._sqlite_path, timeout=15)
            conn.row_factory = sqlite3.Row
            # busy_timeout：等待锁而不是立刻报 database is locked（连接级，开销极小）
            conn.execute("PRAGMA busy_timeout=15000")
        else:  # pragma: no cover - 需要真实 MySQL 环境
            import pymysql

            u = urlparse(self.url.replace("mysql://", "mysql+pymysql://", 1))
            conn = pymysql.connect(
                host=u.hostname or "127.0.0.1",
                port=u.port or 3306,
                user=u.username or "root",
                password=u.password or "",
                database=(u.path or "/game_qa").lstrip("/"),
                charset="utf8mb4",
                autocommit=True,
            )
        try:
            yield conn.cursor()
            conn.commit()
        finally:
            conn.close()

    def _sql(self, sql: str) -> str:
        return sql if self.dialect == "sqlite" else sql.replace("?", "%s")

    # ------------------------------------------------------------------ 查询
    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        def run() -> tuple[list[Any], list[str]]:
            with self._cursor() as cur:
                cur.execute(self._sql(sql), tuple(params))
                rows = cur.fetchall()
                cols = [d[0] for d in (cur.description or [])]
            return rows, cols

        rows, cols = self._retry(run)
        if self.dialect == "sqlite":
            return [dict(r) for r in rows]
        return [dict(zip(cols, r)) for r in rows]  # pragma: no cover

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params: Sequence[Any] = (), default: Any = None) -> Any:
        row = self.query_one(sql, params)
        if not row:
            return default
        value = next(iter(row.values()))
        return default if value is None else value

    # ------------------------------------------------------------------ 写入
    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        def run() -> int:
            with self._write_lock:
                with self._cursor() as cur:
                    cur.execute(self._sql(sql), tuple(params))
                    return cur.rowcount

        return self._retry(run)

    def executemany(self, sql: str, seq: Sequence[Sequence[Any]]) -> int:
        if not seq:
            return 0

        def run() -> int:
            with self._write_lock:
                with self._cursor() as cur:
                    cur.executemany(self._sql(sql), [tuple(p) for p in seq])
                    return cur.rowcount

        return self._retry(run)

    # ------------------------------------------------------------------ 建表
    def init_schema(self) -> None:
        t = {
            "ID": "TEXT PRIMARY KEY" if self.dialect == "sqlite" else "VARCHAR(64) PRIMARY KEY",
            "TEXT": "TEXT" if self.dialect == "sqlite" else "TEXT",
            "LONGTEXT": "TEXT" if self.dialect == "sqlite" else "LONGTEXT",
            "INT": "INTEGER" if self.dialect == "sqlite" else "INT",
            "REAL": "REAL" if self.dialect == "sqlite" else "DOUBLE",
            "TS": "TEXT",
        }
        ddl = [
            """CREATE TABLE IF NOT EXISTS requirements (
                id {ID}, name {TEXT}, domain {TEXT}, content {LONGTEXT},
                created_at {TS})""",
            """CREATE TABLE IF NOT EXISTS cases (
                id {ID}, req_id {TEXT}, title {TEXT}, category {TEXT}, priority {TEXT},
                precondition {TEXT}, steps_json {LONGTEXT}, expected {TEXT},
                risk_tags {TEXT}, api_refs {TEXT}, status {TEXT}, score {REAL},
                source {TEXT}, fingerprint {TEXT}, reject_reasons {TEXT}, created_at {TS})""",
            """CREATE TABLE IF NOT EXISTS scripts (
                id {ID}, case_id {TEXT}, path {TEXT}, code {LONGTEXT}, version {INT},
                precheck_json {TEXT}, created_at {TS})""",
            """CREATE TABLE IF NOT EXISTS runs (
                id {ID}, batch_id {TEXT}, case_id {TEXT}, script_id {TEXT}, case_title {TEXT},
                status {TEXT}, failure_kind {TEXT}, duration_ms {INT}, attempts {INT},
                log_excerpt {LONGTEXT}, heal_note {TEXT}, started_at {TS}, finished_at {TS})""",
            """CREATE TABLE IF NOT EXISTS defects (
                id {ID}, run_id {TEXT}, case_id {TEXT}, title {TEXT}, category {TEXT},
                severity {TEXT}, description {LONGTEXT}, repro_steps {LONGTEXT},
                root_cause {LONGTEXT}, evidence {LONGTEXT}, status {TEXT},
                occurrences {INT}, created_at {TS})""",
            """CREATE TABLE IF NOT EXISTS stage_sessions (
                id {ID}, batch_id {TEXT}, player_id {TEXT}, stage_id {INT},
                result {TEXT}, duration_ms {INT}, deaths {INT}, progress_at_fail {REAL},
                skill_casts {INT}, items_used {INT}, ts {TS})""",
            """CREATE TABLE IF NOT EXISTS item_usage (
                id {ID}, batch_id {TEXT}, player_id {TEXT}, stage_id {INT},
                item_id {TEXT}, cnt {INT}, ts {TS})""",
            """CREATE TABLE IF NOT EXISTS meta (
                k {ID}, v {LONGTEXT})""",
        ]
        for stmt in ddl:
            self.execute(stmt.format(**t))
        # 轻量迁移：老库补列（演示项目不做完整 migration 框架）
        try:
            self.execute("ALTER TABLE defects ADD COLUMN occurrences INTEGER")
        except Exception:  # noqa: BLE001 - 列已存在即忽略
            pass


_db: Database | None = None


def get_db() -> Database:
    global _db
    if _db is None:
        _db = Database()
        _db.init_schema()
    return _db


def reset_db_singleton() -> None:
    """测试用：切换数据库后重置单例。"""
    global _db
    _db = None
