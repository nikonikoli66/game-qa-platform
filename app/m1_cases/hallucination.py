"""幻觉拦截自检夹具。

离线规则引擎产出的用例天然干净，肉眼看不到"质检"到底有没有用。
所以这里内置一批**典型的 AI 生成错误**，跑一遍校验器，用来证明：
每一条脏用例都被对应的规则拦下来了，而不是"看起来能跑就放行"。

这个自检在 UI 上是一个按钮，在测试里是一条用例，可直接复现。
"""
from __future__ import annotations

from typing import Any

from app.m1_cases import validator
from app.m1_cases.schema import normalize_case

_GOOD = {
    "title": "单抽成功扣钻",
    "category": "正向",
    "priority": "P0",
    "precondition": "账号已登录且钻石充足",
    "steps": [{
        "action": "POST /api/gacha/draw",
        "payload": {"pool_id": "pool_standard", "times": 1},
        "expect": {"code": 0, "data.items": "len:1"},
    }],
    "expected": "code=0，data.items 长度=1",
    "risk_tags": ["资源扣减一致性"],
}

# (名称, 期望命中的规则前缀, 用例)
FIXTURES: list[tuple[str, str, dict[str, Any]]] = [
    (
        "虚构了不存在的接口",
        "R2",
        {
            **_GOOD,
            "title": "十连抽（AI 编的接口）",
            "steps": [{
                "action": "POST /api/gacha/draw_ten",
                "payload": {"pool_id": "pool_standard"},
                "expect": {"code": 0, "data.items": "len:10"},
            }],
        },
    ),
    (
        "字段名用了驼峰（契约是下划线）",
        "R3",
        {**_GOOD, "title": "抽卡（字段拼错）",
         "steps": [{"action": "POST /api/gacha/draw",
                    "payload": {"poolId": "pool_standard", "times": 1},
                    "expect": {"code": 0, "data.items": "len:1"}}]},
    ),
    (
        "参数越界（一次性抽 100 次）",
        "R3",
        {**_GOOD, "title": "百连抽",
         "steps": [{"action": "POST /api/gacha/draw",
                    "payload": {"pool_id": "pool_standard", "times": 100},
                    "expect": {"code": 0, "data.items": "len:100"}}]},
    ),
    (
        "正向用例只断言了 code，等于没验业务",
        "R4",
        {**_GOOD, "title": "抽卡（弱断言）",
         "steps": [{"action": "POST /api/gacha/draw",
                    "payload": {"pool_id": "pool_standard", "times": 1},
                    "expect": {"code": 0}}]},
    ),
    (
        "反向用例没有断言错误码",
        "R4",
        {**_GOOD, "title": "非法次数（无异常断言）", "category": "反向",
         "steps": [{"action": "POST /api/gacha/draw",
                    "payload": {"pool_id": "pool_standard", "times": 1},
                    "expect": {"code": 0, "data.items": "not_empty"}}],
         "expected": "处理正确"},
    ),
    (
        "期望结果写成空话，无法验收",
        "R5",
        {**_GOOD, "title": "抽卡（期望不可观测）", "expected": "功能正常"},
    ),
]


def self_check() -> list[dict[str, Any]]:
    """对每条脏用例跑校验器，返回是否按预期命中规则。"""
    rows: list[dict[str, Any]] = []
    for name, expected_rule, raw in FIXTURES:
        result = validator.validate_case(normalize_case(raw))
        hit_rules = [i.rule for i in result.issues if i.level == "error"]
        caught = any(r.startswith(expected_rule) for r in hit_rules)
        rows.append({
            "注入的幻觉": name,
            "期望命中": expected_rule,
            "实际命中": "、".join(hit_rules) or "未拦截",
            "结果": "✅ 已拦截" if caught else "❌ 漏过",
            "详情": result.issues[0].message if result.issues else "",
            "caught": caught,
        })

    # 重复用例单独校验：需要两条一样的
    good = normalize_case(_GOOD)
    dup = normalize_case({**_GOOD, "title": "单抽成功扣钻（重复）"})
    report = validator.validate_batch([good, dup])
    dup_caught = len(report.valid) == 1 and report.rejected
    rows.append({
        "注入的幻觉": "换个标题的重复用例",
        "期望命中": "R8",
        "实际命中": "R8-去重" if dup_caught else "未拦截",
        "结果": "✅ 已拦截" if dup_caught else "❌ 漏过",
        "详情": (list(report.reject_reasons.values())[0][0] if report.reject_reasons else ""),
        "caught": dup_caught,
    })
    return rows


def summary() -> dict[str, Any]:
    rows = self_check()
    caught = sum(1 for r in rows if r["caught"])
    return {
        "total": len(rows),
        "caught": caught,
        "rate": round(caught / len(rows) * 100, 1) if rows else 0.0,
        "rows": rows,
        "all_caught": caught == len(rows),
    }
