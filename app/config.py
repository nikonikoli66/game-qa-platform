"""全局配置。

设计要点：所有可变项都从环境变量读取，且每一项都有可用默认值，
保证「clone 下来直接 python run_demo.py 就能跑」，不依赖任何本地配置。
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent


def _load_env_file(path: Path) -> None:
    """极简 .env 解析，避免为一个功能引入 python-dotenv 依赖。"""
    if not path.exists():
        return
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


_load_env_file(ROOT_DIR / ".env")

# ---------------- 目录 ----------------
DATA_DIR = ROOT_DIR / "data"
REQUIREMENT_DIR = DATA_DIR / "requirements"
RUNTIME_DIR = DATA_DIR / "runtime"
SCRIPTS_DIR = RUNTIME_DIR / "scripts"
ARTIFACTS_DIR = RUNTIME_DIR / "artifacts"
for _d in (DATA_DIR, REQUIREMENT_DIR, RUNTIME_DIR, SCRIPTS_DIR, ARTIFACTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---------------- 存储 ----------------
_default_sqlite = DATA_DIR / "app.db"
DB_URL = os.environ.get("QA_DB_URL", f"sqlite:///{_default_sqlite.as_posix()}")

# ---------------- 被测服 ----------------
GAME_API_BASE = os.environ.get("GAME_API_BASE", "http://127.0.0.1:8000").rstrip("/")
MOCK_PORT = int(os.environ.get("MOCK_PORT", "8000"))
MOCK_REWARD_DELAY = float(os.environ.get("MOCK_REWARD_DELAY", "1.5"))
MOCK_BUGS = [
    x.strip()
    for x in os.environ.get("MOCK_BUGS", "gacha_pity_off_by_one,reward_double_claim").split(",")
    if x.strip()
]

# ---------------- 大模型 ----------------
LLM_PROVIDER = (os.environ.get("LLM_PROVIDER") or "offline").strip().lower()
LLM_API_KEY = (os.environ.get("LLM_API_KEY") or "").strip()
LLM_BASE_URL = (os.environ.get("LLM_BASE_URL") or "https://api.deepseek.com/v1").rstrip("/")
LLM_MODEL = os.environ.get("LLM_MODEL") or "deepseek-chat"
LLM_TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "60"))
LLM_MAX_RETRIES = int(os.environ.get("LLM_MAX_RETRIES", "2"))
LLM_TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", "0.2"))
# 推理模型（deepseek-v4 系列）会先输出 reasoning_content 再输出正文，
# max_tokens 给小了会出现"推理烧完预算、正文为空"，所以默认给足
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "8192"))

# ---------------- 执行 ----------------
ASYNC_REWARD_SLA = float(os.environ.get("ASYNC_REWARD_SLA", "8"))
HTTP_TIMEOUT = float(os.environ.get("QA_HTTP_TIMEOUT", "10"))
SCRIPT_TIMEOUT = int(os.environ.get("SCRIPT_TIMEOUT", "180"))
MAX_HEAL_ROUNDS = int(os.environ.get("MAX_HEAL_ROUNDS", "2"))

# ---------------- 质量门禁 ----------------
CASE_DEDUP_THRESHOLD = float(os.environ.get("CASE_DEDUP_THRESHOLD", "0.85"))
MIN_CASE_SCORE = float(os.environ.get("MIN_CASE_SCORE", "75"))


def llm_enabled() -> bool:
    """是否启用真实大模型。为 False 时所有 AI 能力降级到离线规则引擎。"""
    return LLM_PROVIDER != "offline" and bool(LLM_API_KEY)


def llm_mode_label() -> str:
    return f"{LLM_PROVIDER}:{LLM_MODEL}" if llm_enabled() else "offline 规则引擎"
