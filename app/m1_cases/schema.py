"""用例的数据结构归一化与指纹计算（生成器、校验器、渲染器共用）。"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from typing import Any

from app.api_catalog import normalize_action

CATEGORIES = ("正向", "反向", "边界", "异常", "性能", "安全")
PRIORITIES = ("P0", "P1", "P2")
RISK_TAGS = (
    "保底计数与概率边界", "异步奖励到账", "背包溢出", "并发与幂等", "资源扣减一致性",
    "状态机流转", "超时与SLA", "参数校验", "难度曲线与卡点", "数据一致性",
)
VAGUE_WORDS = ("正常", "符合预期", "无异常", "OK", "ok", "成功即可", "不报错")


def _as_str(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ("" if v is None else str(v))


def normalize_step(step: Any) -> dict[str, Any]:
    if isinstance(step, str):
        return {"action": step, "payload": {}, "expect": {}, "snapshot": [], "wait_async": False}
    step = step if isinstance(step, dict) else {}
    payload = step.get("payload")
    if not isinstance(payload, dict):
        payload = {}
    expect = step.get("expect")
    if not isinstance(expect, dict):
        expect = {}
    snapshot = step.get("snapshot")
    if not isinstance(snapshot, list):
        snapshot = []
    return {
        "action": normalize_action(_as_str(step.get("action"))),
        "payload": payload,
        "expect": expect,
        "snapshot": [s for s in snapshot if isinstance(s, str)],
        "wait_async": bool(step.get("wait_async")),
    }


def normalize_case(raw: dict[str, Any]) -> dict[str, Any]:
    """把 LLM 产出的任意不规整数据，压成下游可消费的稳定结构。"""
    steps = [normalize_step(s) for s in (raw.get("steps") or [])]
    api_refs = sorted({s["action"] for s in steps if s["action"]})
    tags = raw.get("risk_tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in re.split(r"[,，;；]", tags) if t.strip()]
    return {
        "title": _as_str(raw.get("title"))[:60],
        "category": _as_str(raw.get("category")),
        "priority": _as_str(raw.get("priority")) or "P1",
        "precondition": _as_str(raw.get("precondition")),
        "steps": steps,
        "expected": _as_str(raw.get("expected")),
        "risk_tags": [t for t in tags if isinstance(t, str)],
        "api_refs": api_refs,
        "source": _as_str(raw.get("source")) or "ai",
    }


def case_fingerprint(case: dict[str, Any]) -> str:
    """用「接口序列 + 关键参数」作为指纹，用于识别重复用例。"""
    sig = []
    for s in case.get("steps", []):
        payload = {k: v for k, v in (s.get("payload") or {}).items() if not str(v).startswith("$")}
        sig.append([s.get("action", ""), json.dumps(payload, ensure_ascii=False, sort_keys=True)])
    raw = json.dumps(sig, ensure_ascii=False, sort_keys=True)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def step_signature_set(case: dict[str, Any]) -> Counter:
    """步骤签名多重集（不是集合）。

    用集合会漏判：幂等用例 = [进关, 结算, 轮询, 领奖, 再领奖]，
    与「正常领奖」用例的集合完全相同（重复的领奖被集合去重了），
    会被误判为重复用例。用 Counter 保留次数才准确。
    """
    out: Counter = Counter()
    for s in case.get("steps", []):
        payload = {k: v for k, v in (s.get("payload") or {}).items() if not str(v).startswith("$")}
        out[f"{s.get('action','')}|{json.dumps(payload, ensure_ascii=False, sort_keys=True)}"] += 1
    return out


def jaccard(a: Counter, b: Counter) -> float:
    """多重集 Jaccard 相似度。"""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = sum((a & b).values())
    union = sum((a | b).values())
    return inter / union if union else 0.0
