"""pytest 公共配置。

注意：环境变量必须在 import app.* 之前设置好，因为 config 在导入时就固化配置。
测试统一使用独立 SQLite 文件，绝不碰演示库。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["LLM_PROVIDER"] = "offline"
os.environ["LLM_API_KEY"] = ""
os.environ["QA_DB_URL"] = f"sqlite:///{(ROOT / 'data' / 'runtime' / 'test_qa.db').as_posix()}"

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _clean_db() -> None:
    from app import repository

    repository.clear_telemetry()
    repository.clear_runs()
    yield
