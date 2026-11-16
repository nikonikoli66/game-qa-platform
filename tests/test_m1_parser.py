"""需求解析器单测：领域识别、配置数值抽取、要点提取。"""
from __future__ import annotations

from app.m1_cases import parser


GACHA_TEXT = """# 卡池需求
1. 单抽消耗 160 钻石，一次最多 10 次。
2. 保底 90 抽必出 SSR，出 SSR 后保底计数归零。
3. 背包上限 200 格，满时走邮件补发。
"""

STAGE_TEXT = """# 关卡需求
1. 进入关卡消耗体力 10 点。
2. 战斗结算后奖励通过消息队列异步发放。
"""


def test_detect_gacha_domain() -> None:
    domain, hits = parser.detect_domain(GACHA_TEXT)
    assert domain == "卡池抽卡"
    assert "保底" in hits


def test_detect_stage_domain() -> None:
    domain, hits = parser.detect_domain(STAGE_TEXT)
    assert domain == "关卡战斗"
    assert "结算" in hits or "关卡" in hits


def test_extract_constraints() -> None:
    cons = parser.extract_constraints(GACHA_TEXT)
    assert cons["cost_gems"] == 160
    assert cons["max_times"] == 10
    assert cons["pity"] == 90
    assert cons["bag_cap"] == 200


def test_extract_constraints_fallback() -> None:
    """文档没写数值时要给默认值，保证后续用例仍可执行。"""
    cons = parser.extract_constraints("抽卡系统改版，暂时没有数值")
    assert cons["cost_gems"] == parser.DEFAULTS["cost_gems"]
    assert cons["pity"] == parser.DEFAULTS["pity"]


def test_key_points_and_risks() -> None:
    profile = parser.analyze("卡池", GACHA_TEXT)
    assert len(profile.key_points) >= 3
    assert "保底计数与概率边界" in profile.risk_hints
    assert "背包溢出" in profile.risk_hints


def test_stage_risk_contains_async() -> None:
    profile = parser.analyze("关卡", STAGE_TEXT)
    assert "异步奖励到账" in profile.risk_hints
