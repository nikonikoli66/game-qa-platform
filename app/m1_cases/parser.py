"""需求文档解析：把非结构化 PRD 变成结构化画像，喂给 AI 或离线引擎。

游戏测试的特殊性：需求里真正要测的东西往往藏在「配置数值」里（抽卡消耗、保底次数、
背包上限、体力阈值）。这些数值是边界用例的输入，所以解析器要顺手把它们抠出来。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# 领域识别关键词
DOMAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "卡池抽卡": ("卡池", "抽卡", "十连", "保底", "概率", "稀有度", "SSR", "招募", "祈愿"),
    "关卡战斗": ("关卡", "战斗", "通关", "副本", "BOSS", "结算", "死亡", "复活", "章节"),
    "背包道具": ("背包", "道具", "材料", "堆叠", "上限", "溢出"),
    "活动任务": ("活动", "任务", "签到", "排行榜", "限时"),
    "新手引导": ("新手", "引导", "教程", "首充"),
}

DEFAULTS = {
    "cost_gems": 160,
    "max_times": 10,
    "pity": 90,
    "stamina": 10,
    "bag_cap": 200,
    "reward_delay": 1.5,
}


@dataclass
class RequirementProfile:
    name: str
    domain: str
    text: str
    constraints: dict[str, int | float] = field(default_factory=dict)
    key_points: list[str] = field(default_factory=list)
    risk_hints: list[str] = field(default_factory=list)

    def as_prompt_block(self) -> str:
        cons = "，".join(f"{k}={v}" for k, v in self.constraints.items())
        points = "\n".join(f"- {p}" for p in self.key_points) or "- （未识别到编号要点）"
        return (
            f"模块名称：{self.name}\n"
            f"玩法领域：{self.domain}\n"
            f"配置数值：{cons}\n"
            f"需求要点：\n{points}"
        )


def detect_domain(text: str) -> tuple[str, list[str]]:
    scores: dict[str, int] = {}
    hits: dict[str, list[str]] = {}
    for domain, words in DOMAIN_KEYWORDS.items():
        matched = [w for w in words if w in text]
        if matched:
            scores[domain] = len(matched)
            hits[domain] = matched
    if not scores:
        return "通用", []
    best = max(scores, key=lambda k: scores[k])
    return best, hits[best]


def _grab(pattern: str, text: str, default: int) -> int:
    m = re.search(pattern, text)
    if not m:
        return default
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return default


def extract_constraints(text: str) -> dict[str, int | float]:
    """从 PRD 抽配置数值；抽不到则用游戏常见默认值，保证后续用例仍可执行。"""
    return {
        "cost_gems": _grab(r"(?:消耗|扣除|花费)\s*(\d+)\s*(?:钻石|宝石|水晶)", text, DEFAULTS["cost_gems"]),
        "max_times": _grab(r"(?:最多|上限|至多|一次最多)\s*(\d+)\s*次", text, DEFAULTS["max_times"]),
        "pity": _grab(r"保底\s*(?:次数|计数|机制)?\s*(?:为|是|=|：|:)?\s*(\d+)", text, DEFAULTS["pity"]),
        "stamina": _grab(r"体力\s*(?:消耗|需求|要求)?\s*(?:为|是|=|：|:)?\s*(\d+)", text, DEFAULTS["stamina"]),
        "bag_cap": _grab(r"背包\s*(?:上限|容量|格数)\s*(?:为|是|=|：|:)?\s*(\d+)", text, DEFAULTS["bag_cap"]),
    }


def extract_key_points(text: str) -> list[str]:
    """抽取编号要点（1. / 一、 / - 开头的行），作为需求要点清单。"""
    points: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if re.match(r"^(\d+[.、)]|[一二三四五六七八九十]+[、.]|[-*•])\s*", line):
            cleaned = re.sub(r"^(\d+[.、)]|[一二三四五六七八九十]+[、.]|[-*•])\s*", "", line)
            if len(cleaned) >= 6:
                points.append(cleaned)
    return points[:20]


RISK_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("保底", "概率", "抽卡", "卡池"), "保底计数与概率边界"),
    (("异步", "延迟", "邮件", "发放", "到账"), "异步奖励到账"),
    (("背包", "上限", "溢出", "堆叠"), "背包溢出"),
    (("重复", "幂等", "并发", "同时"), "并发与幂等"),
    (("消耗", "扣除", "钻石", "体力", "货币"), "资源扣减一致性"),
    (("结算", "状态", "领取", "流转"), "状态机流转"),
    (("限时", "超时", "倒计时", "SLA"), "超时与 SLA"),
)


def detect_risks(text: str, domain: str) -> list[str]:
    risks = [tag for words, tag in RISK_RULES if any(w in text for w in words)]
    if domain == "关卡战斗" and "难度" not in risks:
        risks.append("难度曲线与卡点")
    if not risks:
        risks = ["参数校验", "资源扣减一致性"]
    return risks


def analyze(name: str, text: str) -> RequirementProfile:
    domain, _ = detect_domain(text)
    return RequirementProfile(
        name=name or "未命名需求",
        domain=domain,
        text=text,
        constraints=extract_constraints(text),
        key_points=extract_key_points(text),
        risk_hints=detect_risks(text, domain),
    )
