"""可复用的异步轮询工具。

两个使用场景：
    1. 生成的 pytest 脚本内部（见 renderer 的 _wait_until）；
    2. 平台自己需要等异步结果时（数据采集、批量执行脚本编排）。

核心思想：把「固定 sleep」换成「按条件轮询 + 截止时间」。
固定 sleep 要么等太短（误报），要么等太久（执行时间翻倍），轮询两头都占。
"""
from __future__ import annotations

import time
from typing import Any, Callable

import requests


class PollTimeout(TimeoutError):
    """轮询超时。单独定义，便于上层把它归类为 ASYNC_TIMEOUT 而不是脚本错误。"""


def wait_for(
    fetch: Callable[[], Any],
    predicate: Callable[[Any], bool],
    timeout: float = 8.0,
    interval: float = 0.3,
    desc: str = "条件",
) -> Any:
    deadline = time.time() + timeout
    last: Any = None
    attempts = 0
    while time.time() < deadline:
        attempts += 1
        last = fetch()
        if predicate(last):
            return last
        time.sleep(interval)
    raise PollTimeout(f"{desc}在 {timeout:.1f}s 内未满足（轮询 {attempts} 次），最后结果: {last}")


def wait_reward_claimable(base: str, token: str, reward_id: str,
                          timeout: float = 8.0) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"}

    def fetch() -> dict[str, Any]:
        resp = requests.get(f"{base.rstrip('/')}/api/reward/{reward_id}",
                            headers=headers, timeout=10)
        return resp.json()

    body = wait_for(
        fetch,
        lambda b: (b.get("data") or {}).get("status") == "claimable",
        timeout=timeout,
        desc=f"奖励 {reward_id} 到账",
    )
    return body
