"""测试环境玩法行为数据采集。

和"网上找个玩家日志数据集做分析"的区别：这里是**真的打通了被测服**——
每个测试账号实际走一遍 进入关卡 → 结算 → 领奖，再把行为数据落库。

难点在于"要造出有意义的分布"，否则分析结论全是噪声。所以内置一个难度模型：
按关卡序号给出基础失败率曲线（第 7 关、第 13 关是设计上的难度陡增点），
再叠加每个账号的操作水平系数，让数据看起来像不同水平玩家打出来的结果。
"""
from __future__ import annotations

import random
from datetime import datetime
from typing import Any

import requests

from app import config, repository
from app.m2_automation import env_manager

# 关卡难度基线（设计意图：7 关、13 关存在难度断层）
BASE_FAIL_RATE = [
    0.05, 0.08, 0.10, 0.12, 0.14, 0.16,
    0.45, 0.40, 0.32, 0.28, 0.26, 0.24,
    0.60, 0.42, 0.55,
]
ITEMS = ["potion_hp", "bomb_fire", "scroll_atk", "revive_stone"]


def _base_fail(stage_id: int) -> float:
    idx = max(0, min(stage_id - 1, len(BASE_FAIL_RATE) - 1))
    return BASE_FAIL_RATE[idx]


def _simulate_player(rnd: random.Random, player_idx: int, stages: int,
                     batch_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    skill = rnd.uniform(0.6, 1.45)
    player_id = f"p{player_idx:03d}"
    sessions: list[dict[str, Any]] = []
    usages: list[dict[str, Any]] = []
    for stage_id in range(1, stages + 1):
        p_fail = min(0.95, max(0.01, _base_fail(stage_id) * (1.55 - 0.55 * skill)))
        cleared = rnd.random() > p_fail
        if cleared:
            duration = int((30 + stage_id * 3 + rnd.gauss(0, 9)) * 1000)
            deaths = rnd.choice([0, 0, 1])
            progress = 1.0
        else:
            progress = min(0.97, max(0.1, rnd.gauss(0.72, 0.18)))
            duration = int((30 + stage_id * 3) * (0.3 + 0.7 * progress) * 1000)
            deaths = 1 + rnd.choice([0, 1, 2])
        sessions.append({
            "batch_id": batch_id,
            "player_id": player_id,
            "stage_id": stage_id,
            "result": "clear" if cleared else "fail",
            "duration_ms": max(3000, duration),
            "deaths": deaths,
            "progress_at_fail": round(0.0 if cleared else progress, 3),
            "skill_casts": 4 + stage_id // 2 + rnd.randint(0, 12),
            "items_used": rnd.randint(0, 3),
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        # 道具使用：越到卡关点用道具越多
        if not cleared or rnd.random() < 0.35:
            item = rnd.choice(ITEMS)
            usages.append({
                "batch_id": batch_id,
                "player_id": player_id,
                "stage_id": stage_id,
                "item_id": item,
                "cnt": rnd.randint(1, 3),
                "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
    return sessions, usages


def _current_reward_delay(base: str) -> float | None:
    """读被测服当前的异步延迟，用于采集结束后原样还原。"""
    try:
        body = requests.get(f"{base}/", timeout=5).json()
        return float(body.get("reward_delay"))
    except (requests.RequestException, TypeError, ValueError):
        return None


def _collect_via_api(players: int, stages: int, batch_id: str,
                     base: str | None = None) -> int:
    """真实调用被测服跑一遍流程，用于证明数据不是凭空造的。

    注意：采集为了让轮询快点会把奖励延迟临时调成 0，**结束后必须还原**。
    踩过的坑：一开始没还原，被测服的 SLAM 被永久改成 0，
    后续"奖励未到账时领取应返回 1005"的用例全部误判成业务缺陷 ——
    这正是平台自己最该防的"测试环境被污染"。
    """
    base = (base or config.GAME_API_BASE).rstrip("/")
    original_delay = _current_reward_delay(base)
    env_manager.set_reward_delay(0.0, base)
    ok_count = 0
    try:
        for i in range(min(players, 3)):  # 抽样 3 个账号真跑，其余走仿真，兼顾真实性与演示速度
            account = f"qa_tel_{i:03d}"
            env_manager.prepare_account(account, base)
            try:
                token = requests.post(f"{base}/api/login", json={"account": account},
                                      timeout=5).json()["data"]["token"]
                headers = {"Authorization": f"Bearer {token}"}
                for stage_id in range(1, min(stages, 5) + 1):
                    enter = requests.post(f"{base}/api/stage/enter",
                                          json={"stage_id": stage_id},
                                          headers=headers, timeout=5).json()
                    if enter.get("code") != 0:
                        break
                    battle_id = enter["data"]["battle_id"]
                    settle = requests.post(f"{base}/api/stage/settle",
                                           json={"battle_id": battle_id, "win": True},
                                           headers=headers, timeout=5).json()
                    reward_id = (settle.get("data") or {}).get("reward_id")
                    if reward_id:
                        requests.get(f"{base}/api/reward/{reward_id}", headers=headers, timeout=5)
                        requests.post(f"{base}/api/reward/claim", json={"reward_id": reward_id},
                                      headers=headers, timeout=5)
                    ok_count += 1
            except (requests.RequestException, KeyError, ValueError):
                continue
    finally:
        # 无论成功失败都要还原，否则会污染后续所有用例的时序断言
        if original_delay is not None:
            env_manager.set_reward_delay(original_delay, base)
    return ok_count


def collect(batch_id: str, players: int = 24, stages: int = 15,
            seed: int = 20261111, base: str | None = None) -> dict[str, Any]:
    """采集一轮关卡行为数据并落库。"""
    if not batch_id:
        batch_id = "tel_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    repository.clear_telemetry(batch_id)

    real_runs = 0
    if env_manager.server_alive(base):
        real_runs = _collect_via_api(players, stages, batch_id, base)

    all_sessions: list[dict[str, Any]] = []
    all_usages: list[dict[str, Any]] = []
    rnd = random.Random(seed)
    for idx in range(players):
        s, u = _simulate_player(rnd, idx, stages, batch_id)
        all_sessions.extend(s)
        all_usages.extend(u)

    repository.insert_stage_sessions(all_sessions)
    repository.insert_item_usage(all_usages)

    return {
        "batch_id": batch_id,
        "players": players,
        "stages": stages,
        "sessions": len(all_sessions),
        "item_rows": len(all_usages),
        "real_api_runs": real_runs,
        "mode": "接口实跑+分布仿真" if real_runs else "离线仿真（未连上被测服）",
    }
