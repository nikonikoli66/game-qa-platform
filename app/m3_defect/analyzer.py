"""缺陷上下文采集 + AI 根因辅助分析。

"自动化跑挂了"对开发毫无价值；有价值的是：哪个接口、什么参数、第几次、
实际返回什么、期望什么、涉及哪个游戏机制、大概是谁的锅。

所以失败时先机械地采集现场（请求参数、响应报文、账号状态、时间戳、版本），
再交给大模型做"自然语言转译"，产出可以直接粘贴到 Jira/TAPD 的缺陷单。
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import requests

from app import config, repository
from app.llm import offline, prompts
from app.llm.client import get_llm
from app.llm.normalize import as_short_text, as_text
from app.m3_defect.classifier import KIND_LABEL, FailureKind


def collect_context(case: dict[str, Any], account: str, log: str,
                    base: str | None = None) -> str:
    """采集失败现场：账号当前状态 + 用例步骤 + 环境信息。"""
    base = (base or config.GAME_API_BASE).rstrip("/")
    ctx: dict[str, Any] = {
        "采集时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "测试环境": base,
        "测试账号": account,
        "用例分类": case.get("category", ""),
        "期望结果": case.get("expected", ""),
        "执行步骤": [
            {
                "序号": i,
                "接口": s.get("action"),
                "入参": s.get("payload", {}),
                "断言": s.get("expect", {}),
                "异步等待": bool(s.get("wait_async")),
            }
            for i, s in enumerate(case.get("steps", []), 1)
        ],
    }
    try:
        login = requests.post(f"{base}/api/login", json={"account": account}, timeout=5).json()
        token = (login.get("data") or {}).get("token", "")
        bag = requests.get(f"{base}/api/bag",
                           headers={"Authorization": f"Bearer {token}"}, timeout=5).json()
        ctx["账号现场"] = {"gems": (bag.get("data") or {}).get("gems"),
                        "stamina": (bag.get("data") or {}).get("stamina"),
                        "bag_used": (bag.get("data") or {}).get("used")}
    except (requests.RequestException, ValueError) as exc:
        ctx["账号现场"] = f"采集失败: {exc}"

    tail = "\n".join([ln for ln in (log or "").strip().splitlines() if ln.strip()][-40:])
    ctx["执行日志尾部"] = tail
    return json.dumps(ctx, ensure_ascii=False, indent=2)


def analyze(kind: FailureKind, case: dict[str, Any], log: str,
            evidence: str = "", account: str = "") -> dict[str, Any]:
    """返回缺陷单字段；大模型不可用时回落到离线模板。"""
    llm = get_llm()
    if llm.enabled:
        data = llm.chat_json(
            prompts.SYSTEM_DEFECT_ANALYST,
            prompts.defect_prompt(kind.value, case, log, evidence),
        )
        if data and data.get("title"):
            # 真实模型的字段类型不可信（repro_steps 可能返回数组），统一归一化后再入库
            return {
                "title": as_text(data.get("title")) or case.get("title", ""),
                "severity": as_short_text(data.get("severity"), 8) or "S3",
                "severity_reason": as_text(data.get("severity_reason")),
                "description": as_text(data.get("description")),
                "repro_steps": as_text(data.get("repro_steps")),
                "root_cause": as_text(data.get("root_cause")),
                "suggestion": as_text(data.get("suggestion")),
                "source": f"llm:{config.LLM_PROVIDER}",
            }

    result = offline.analyze_defect(kind.value, case, log, evidence, account)
    result["source"] = "offline"
    return result


def create_defect(run_id: str, case: dict[str, Any], kind: FailureKind,
                  log: str, account: str) -> dict[str, Any]:
    """采集 → 分析 → 入库，返回可直接展示的缺陷单。"""
    evidence = collect_context(case, account, log)
    data = analyze(kind, case, log, evidence, account)
    defect = {
        "title": f"[{KIND_LABEL.get(kind.value, kind.value)}] {data.get('title', case.get('title', ''))}",
        "category": kind.value,
        "severity": data.get("severity", "S3"),
        "description": data.get("description", ""),
        "repro_steps": data.get("repro_steps", ""),
        "root_cause": data.get("root_cause", ""),
        "evidence": evidence,
        "status": "open",
    }
    # 同一用例 + 同一分类 = 同一个根因，不重复开单（否则每跑一次缺陷池就翻一倍）
    existing = repository.find_open_defect(case.get("id", ""), kind.value)
    if existing:
        defect["id"] = existing["id"]
        defect["duplicated"] = True
        defect["occurrences"] = int(existing.get("occurrences") or 1) + 1
        repository.get_db().execute(
            "UPDATE defects SET occurrences=? WHERE id=?",
            (defect["occurrences"], existing["id"]),
        )
        return defect

    defect["id"] = repository.save_defect(run_id, case.get("id", ""), defect)
    defect["duplicated"] = False
    defect["suggestion"] = data.get("suggestion", "")
    defect["source"] = data.get("source", "offline")
    defect["severity_reason"] = data.get("severity_reason", "")
    return defect
