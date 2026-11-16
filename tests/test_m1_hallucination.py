"""幻觉拦截能力回归测试。

把"注入脏用例 → 必须被对应规则拦下"固化成测试：
以后有人改动校验规则，这里会立刻报警，避免质检能力悄悄退化。
"""
from __future__ import annotations

from app.m1_cases import hallucination


def test_all_injected_hallucinations_caught() -> None:
    summary = hallucination.summary()
    missed = [r["注入的幻觉"] for r in summary["rows"] if not r["caught"]]
    assert not missed, f"以下幻觉未被拦截：{missed}"
    assert summary["all_caught"] is True
    assert summary["rate"] == 100.0


def test_fixtures_cover_each_rule() -> None:
    expected = {r["期望命中"] for r in hallucination.self_check()}
    assert {"R2", "R3", "R4", "R5", "R8"} <= expected
