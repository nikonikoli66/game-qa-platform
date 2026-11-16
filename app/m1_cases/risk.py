"""游戏特有风险点识别 —— 把"测试老手脑子里的 checklist"沉淀成可复用资产。

纯靠 AI 想风险点不可控（有时漏、有时编），所以这里用「领域 playbook + 关键词触发」
先给出风险清单，再让用例去覆盖它，最后产出风险覆盖矩阵：哪个风险没有用例兜底，
一眼就能看出来。这对新人接手老模块特别有用。
"""
from __future__ import annotations

from typing import Any

from app.m1_cases.parser import RequirementProfile

PLAYBOOK: dict[str, dict[str, Any]] = {
    "保底计数与概率边界": {
        "why": "保底计数差一 / 计数未重置 / 跨卡池串号，是抽卡系统最高频的线上事故",
        "how": "GM 把保底计数置为 limit-1，再抽一次断言必出 SSR；抽中后断言计数归零",
        "keywords": ("保底", "概率", "抽卡", "卡池", "稀有度"),
    },
    "异步奖励到账": {
        "why": "发奖走 MQ / 定时任务，存在延迟与丢失；同步断言必然误报",
        "how": "结算后轮询奖励状态直到 claimable，超时时间设为 SLA 的 2 倍",
        "keywords": ("异步", "邮件", "发放", "到账", "延迟"),
    },
    "背包溢出": {
        "why": "背包满时奖励被静默丢弃，玩家侧表现为「奖励不见了」",
        "how": "GM 填满背包后再产出道具，断言 mail_delivered=true 且道具数量守恒",
        "keywords": ("背包", "上限", "溢出", "堆叠"),
    },
    "并发与幂等": {
        "why": "重复点击、重试、网络抖动导致的重复领奖/重复扣费",
        "how": "同一奖励单连续领取两次，断言第二次返回状态冲突且不重复发奖",
        "keywords": ("重复", "幂等", "并发", "同时", "领取"),
    },
    "资源扣减一致性": {
        "why": "扣费与发货不在同一事务，失败时出现「扣了没给」或「给了没扣」",
        "how": "请求前后快照货币字段，用 delta 断言精确扣减；失败场景断言余额不变",
        "keywords": ("消耗", "扣除", "钻石", "体力", "货币"),
    },
    "状态机流转": {
        "why": "非法状态跳转（已结算再结算、未到账先领取）导致刷奖励",
        "how": "对同一单据重复触发状态迁移，断言返回状态冲突错误码",
        "keywords": ("结算", "状态", "领取", "流转"),
    },
    "超时与SLA": {
        "why": "异步链路偶发堆积，长时间未到账影响体验",
        "how": "以 SLA 为阈值做超时断言，并记录实际耗时分布",
        "keywords": ("限时", "超时", "倒计时", "SLA"),
    },
    "难度曲线与卡点": {
        "why": "关卡数值断层导致玩家流失，纯主观体验无法说服策划",
        "how": "采集通关率 / 死亡次数 / 卡点进度，做相邻关卡难度跃迁检测",
        "keywords": ("关卡", "难度", "战斗", "通关"),
    },
}


def analyze_risks(profile: RequirementProfile, cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """产出风险覆盖矩阵：风险点 → 是否有用例覆盖 → 建议补什么。"""
    matrix: list[dict[str, Any]] = []
    used_tags: dict[str, int] = {}
    for c in cases:
        for tag in c.get("risk_tags") or []:
            used_tags[tag] = used_tags.get(tag, 0) + 1

    for risk, meta in PLAYBOOK.items():
        triggered = any(k in profile.text for k in meta["keywords"])
        triggered = triggered or risk in profile.risk_hints
        if not triggered:
            continue
        covered = used_tags.get(risk, 0)
        matrix.append({
            "risk": risk,
            "why": meta["why"],
            "how": meta["how"],
            "covered_cases": covered,
            "covered": covered > 0,
            "level": "高" if not covered else ("中" if covered < 2 else "低"),
        })
    matrix.sort(key=lambda r: (r["covered"], r["risk"]))
    return matrix
