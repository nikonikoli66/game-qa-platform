"""模块 1 主编排：需求 → 用例 → 质检 → 入库。

AI 与规则的关系在这里体现得最清楚：
    1. 先本地解析需求（结构化、确定性）；
    2. 再让 LLM 做语义活（理解玩法、设计场景）；
    3. 质检不过 → 把「哪条规则、为什么不过」回灌给模型重写一次（feedback loop）；
    4. 仍然不过 → 保留但不入库，绝不把脏数据丢给下游执行层。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app import config, repository
from app.llm import offline, prompts
from app.llm.client import get_llm
from app.m1_cases import risk as risk_mod
from app.m1_cases import validator
from app.m1_cases.parser import RequirementProfile, analyze
from app.m1_cases.schema import normalize_case


@dataclass
class GenerationOutcome:
    req_id: str
    profile: RequirementProfile
    report: validator.BatchReport
    engine: str
    repair_rounds: int = 0
    risk_matrix: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def cases(self) -> list[dict[str, Any]]:
        return self.report.valid

    def summary(self) -> dict[str, Any]:
        return {
            "req_id": self.req_id,
            "engine": self.engine,
            "domain": self.profile.domain,
            "constraints": self.profile.constraints,
            "stats": self.report.as_dict(),
            "repair_rounds": self.repair_rounds,
            "notes": self.notes,
        }


def _extract_raw_cases(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("cases", "items", "test_cases", "result"):
            value = data.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
        if "title" in data and "steps" in data:
            return [data]
    return []


def _from_llm(profile: RequirementProfile, count: int) -> tuple[list[dict[str, Any]], str]:
    llm = get_llm()
    if not llm.enabled:
        return [], "offline"
    data = llm.chat_json(prompts.SYSTEM_CASE_GENERATOR,
                         prompts.case_generation_prompt(profile, count))
    raw = _extract_raw_cases(data)
    if not raw:
        return [], "offline"
    return [normalize_case(c) for c in raw], f"llm:{config.LLM_PROVIDER}:{config.LLM_MODEL}"


def _repair_with_llm(profile: RequirementProfile, report: validator.BatchReport) -> list[dict[str, Any]]:
    llm = get_llm()
    if not llm.enabled or not report.rejected:
        return []
    reasons = {r["title"]: [i["message"] for i in r["issues"]] for r in report.details
               if r["status"] != "valid"}
    prompt = prompts.case_repair_prompt(report.rejected, reasons, profile)
    data = llm.chat_json(prompts.SYSTEM_CASE_REPAIR, prompt)
    return [normalize_case(c) for c in _extract_raw_cases(data)]


def generate(name: str, text: str, count: int = 10, persist: bool = True) -> GenerationOutcome:
    profile = analyze(name, text)
    raw_cases, engine = _from_llm(profile, count)
    notes: list[str] = []

    if not raw_cases:
        raw_cases = offline.generate_cases(profile, count)
        engine = "offline"
        if config.llm_enabled():
            notes.append("大模型调用失败，已自动降级到离线规则引擎生成用例")
        else:
            notes.append("当前为离线模式（未配置 LLM_API_KEY），使用内置规则引擎生成用例")

    report = validator.validate_batch(raw_cases)
    repair_rounds = 0

    # 幻觉率过高 → 带着质检意见让模型重写一次
    if engine.startswith("llm") and report.rejected and len(report.rejected) >= 2 and repair_rounds < 1:
        repair_rounds = 1
        fixed = _repair_with_llm(profile, report)
        if fixed:
            second = validator.validate_batch(fixed)
            if len(second.valid) > len(report.valid):
                notes.append(
                    f"质检反馈闭环：重写后有效用例 {len(report.valid)} → {len(second.valid)} 条"
                )
                report = second
            else:
                notes.append("质检反馈闭环：重写后质量未提升，保留首轮结果")

    risk_matrix = risk_mod.analyze_risks(profile, report.valid)

    req_id = ""
    if persist:
        req_id = repository.save_requirement(name, text, profile.domain)
        for case in report.valid:
            case["id"] = repository.save_case(case, req_id)
        for case in report.rejected:
            case["id"] = repository.save_case(case, req_id)

    return GenerationOutcome(
        req_id=req_id,
        profile=profile,
        report=report,
        engine=engine,
        repair_rounds=repair_rounds,
        risk_matrix=risk_matrix,
        notes=notes,
    )


def regenerate_from_text(name: str, text: str, count: int = 10) -> GenerationOutcome:
    return generate(name, text, count, persist=True)
