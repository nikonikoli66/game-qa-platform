"""测试环境管理：账号初始化、脏数据清理、服务可用性探测。

自动化跑不稳，八成不是脚本问题而是环境问题：
    · 上一个用例把钻石抽光了，下一个"单抽成功"必然失败；
    · 上一个用例把背包塞满了，下一个断言背包数量就错；
    · 服务没起来，所有用例集体失败但看起来像业务全崩。

所以每个用例执行前后都要做确定性清理，且清理动作本身要能自检。
"""
from __future__ import annotations

import time
from typing import Any

import requests

from app import config


def _url(path: str, base: str | None = None) -> str:
    return (base or config.GAME_API_BASE).rstrip("/") + path


def server_alive(base: str | None = None, timeout: float = 3.0) -> bool:
    try:
        resp = requests.get(_url("/api/health", base), timeout=timeout)
        return resp.status_code == 200 and (resp.json() or {}).get("code") == 0
    except (requests.RequestException, ValueError):
        return False


def wait_server(timeout: float = 30.0, base: str | None = None) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if server_alive(base):
            return True
        time.sleep(0.5)
    return False


def prepare_account(account: str, base: str | None = None) -> dict[str, Any]:
    """登录（不存在则创建）+ 重置到干净状态。返回执行日志。"""
    log: list[str] = []
    ok = True
    try:
        login = requests.post(_url("/api/login", base), json={"account": account}, timeout=10)
        log.append(f"login -> {login.status_code}")
        ok = ok and login.status_code == 200
        reset = requests.post(_url("/api/admin/reset", base), json={"account": account}, timeout=10)
        log.append(f"reset -> {reset.status_code} {reset.text[:120]}")
        ok = ok and reset.status_code == 200
    except requests.RequestException as exc:
        log.append(f"环境初始化失败: {exc}")
        ok = False
    return {"account": account, "ok": ok, "log": log}


def cleanup_account(account: str, base: str | None = None) -> dict[str, Any]:
    """用例执行后清理，避免污染后续用例。"""
    try:
        resp = requests.post(_url("/api/admin/reset", base), json={"account": account}, timeout=10)
        return {"ok": resp.status_code == 200, "detail": resp.text[:200]}
    except requests.RequestException as exc:
        return {"ok": False, "detail": str(exc)}


def set_reward_delay(seconds: float, base: str | None = None) -> bool:
    """数据采集等非断言场景把异步延迟调小，避免等待浪费。"""
    try:
        resp = requests.post(_url("/api/admin/config", base),
                             json={"reward_delay": seconds}, timeout=10)
        return resp.status_code == 200
    except requests.RequestException:
        return False


def account_for(case_id: str) -> str:
    """每个用例独立账号，天然隔离，互不影响。"""
    safe = "".join(ch for ch in case_id if ch.isalnum())[:16] or "auto"
    return f"qa_{safe}"
