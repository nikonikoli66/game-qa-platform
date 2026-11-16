"""用例校验器单测 —— 每条规则都必须有测试覆盖。"""
from __future__ import annotations

from app.m1_cases import validator
from app.m1_cases.schema import normalize_case


def _case(**overrides):
    base = {
        "title": "单抽扣钻正确",
        "category": "正向",
        "priority": "P0",
        "precondition": "账号已登录且钻石充足",
        "steps": [{
            "action": "POST /api/gacha/draw",
            "payload": {"pool_id": "pool_standard", "times": 1},
            "expect": {"code": 0, "data.items": "len:1"},
        }],
        "expected": "code=0 且 data.items 长度为 1",
        "risk_tags": ["资源扣减一致性"],
    }
    base.update(overrides)
    return normalize_case(base)


def test_valid_case_passes() -> None:
    result = validator.validate_case(_case())
    assert result.ok, [i.message for i in result.issues]
    assert result.score == 100


def test_hallucinated_endpoint_rejected() -> None:
    """核心场景：AI 编了一个不存在的接口。"""
    case = _case(steps=[{
        "action": "POST /api/gacha/draw_ten",
        "payload": {"pool_id": "pool_standard"},
        "expect": {"code": 0, "data.items": "len:10"},
    }])
    result = validator.validate_case(case)
    assert not result.ok
    assert any("R2" in i.rule and "幻觉" in i.message for i in result.issues)


def test_unknown_field_rejected() -> None:
    case = _case(steps=[{
        "action": "POST /api/gacha/draw",
        "payload": {"poolId": "pool_standard", "times": 1},
        "expect": {"code": 0, "data.items": "len:1"},
    }])
    result = validator.validate_case(case)
    assert not result.ok
    assert any("R3" in i.rule and "未定义字段" in i.message for i in result.issues)


def test_param_out_of_range_rejected_for_positive_case() -> None:
    case = _case(steps=[{
        "action": "POST /api/gacha/draw",
        "payload": {"pool_id": "pool_standard", "times": 100},
        "expect": {"code": 0, "data.items": "len:1"},
    }])
    result = validator.validate_case(case)
    assert not result.ok
    assert any("超过上限" in i.message for i in result.issues)


def test_reverse_case_may_exceed_range() -> None:
    """反向用例本来就是故意传越界参数，不能被参数区间规则误杀。"""
    case = _case(
        category="反向",
        steps=[{
            "action": "POST /api/gacha/draw",
            "payload": {"pool_id": "pool_standard", "times": 99},
            "expect": {"code": 1001},
        }],
        expected="返回 code=1001 参数非法，不产生道具",
    )
    result = validator.validate_case(case)
    assert result.ok, [i.message for i in result.issues]


def test_weak_assertion_rejected() -> None:
    """正向用例只断言 code，没有业务断言 → 不合格。"""
    case = _case(steps=[{
        "action": "POST /api/gacha/draw",
        "payload": {"pool_id": "pool_standard", "times": 1},
        "expect": {"code": 0},
    }])
    result = validator.validate_case(case)
    assert not result.ok
    assert any("缺少业务字段断言" in i.message for i in result.issues)


def test_reverse_case_without_error_code_rejected() -> None:
    case = _case(
        category="反向",
        steps=[{
            "action": "POST /api/gacha/draw",
            "payload": {"pool_id": "pool_standard", "times": 1},
            "expect": {"code": 0, "data.items": "not_empty"},
        }],
    )
    result = validator.validate_case(case)
    assert not result.ok
    assert any("R4" in i.rule for i in result.issues)


def test_boundary_case_with_precise_assert_passes() -> None:
    """边界用例断言的是边界值（保底必出 SSR），不是错误码，应通过。"""
    case = _case(
        category="边界",
        steps=[{
            "action": "POST /api/gacha/draw",
            "payload": {"pool_id": "pool_standard", "times": 1},
            "expect": {"code": 0, "data.items[0].rarity": "SSR"},
        }],
        expected="保底计数达上限时必出 SSR",
    )
    result = validator.validate_case(case)
    assert result.ok, [i.message for i in result.issues]


def test_vague_expected_rejected() -> None:
    result = validator.validate_case(_case(expected="功能正常"))
    assert not result.ok
    assert any("空泛" in i.message for i in result.issues)


def test_batch_dedup() -> None:
    a = _case(title="用例A")
    b = _case(title="用例B")  # 步骤完全相同
    report = validator.validate_batch([a, b])
    assert len(report.valid) == 1
    assert len(report.rejected) == 1
    assert any("重复用例" in msg for msgs in report.reject_reasons.values() for msg in msgs)


def test_batch_dedup_keeps_idempotent_variant() -> None:
    """幂等用例 = 正常流程 + 多一次领奖，不能因为"步骤是超集"被判重复。"""
    base_steps = [
        {"action": "POST /api/reward/claim", "payload": {"reward_id": "$reward_id"},
         "expect": {"code": 0, "data.granted": "not_empty"}},
    ]
    a = _case(title="正常领奖", steps=base_steps)
    b = _case(title="重复领奖幂等", category="异常", steps=base_steps + [{
        "action": "POST /api/reward/claim", "payload": {"reward_id": "$reward_id"},
        "expect": {"code": 1005},
    }])
    report = validator.validate_batch([a, b])
    assert len(report.valid) == 2, [d["issues"] for d in report.details]


def test_coverage_report() -> None:
    report = validator.validate_batch([_case()])
    assert report.coverage["by_category"].get("正向") == 1
    assert "正向" not in report.coverage["missing_categories"]


# ---------------------------------------------------------------- R9：动态 ID 变量引用
def test_hardcoded_dynamic_id_rejected() -> None:
    """真实大模型的典型错误：编造 battle_id，导致执行时必然"对象不存在"。"""
    case = _case(steps=[
        {"action": "POST /api/stage/enter", "payload": {"stage_id": 1},
         "expect": {"code": 0, "data.battle_id": "not_empty"}},
        {"action": "POST /api/stage/settle",
         "payload": {"battle_id": "BATTLE_5_123456", "win": True},
         "expect": {"code": 0, "data.reward_id": "not_empty"}},
    ])
    result = validator.validate_case(case)
    assert not result.ok
    assert any("R9" in i.rule and "应改用 $battle_id" in i.message for i in result.issues)


def test_dynamic_id_without_producer_rejected() -> None:
    """没有前置 enter 步骤却直接结算 —— 正向用例永远跑不过，必须在用例层拦下。"""
    case = _case(steps=[{
        "action": "POST /api/stage/settle",
        "payload": {"battle_id": "BTC_001", "win": True},
        "expect": {"code": 0, "data.reward_id": "not_empty"},
    }])
    result = validator.validate_case(case)
    assert not result.ok
    assert any("R9" in i.rule for i in result.issues)


def test_negative_case_may_use_literal_id() -> None:
    """刻意测试"不存在的战斗"时，字面量 ID 是合理的，不能误杀。"""
    case = _case(
        category="反向",
        steps=[{"action": "POST /api/stage/settle",
                "payload": {"battle_id": "not_exist_bt", "win": True},
                "expect": {"code": 1004}}],
        expected="返回 code=1004 对象不存在",
    )
    result = validator.validate_case(case)
    assert result.ok, [i.message for i in result.issues]


def test_variable_ref_passes() -> None:
    case = _case(steps=[
        {"action": "POST /api/stage/enter", "payload": {"stage_id": 1},
         "expect": {"code": 0, "data.battle_id": "not_empty"}},
        {"action": "POST /api/stage/settle",
         "payload": {"battle_id": "$battle_id", "win": True},
         "expect": {"code": 0, "data.reward_id": "not_empty"}},
    ])
    assert validator.validate_case(case).ok


# ---------------------------------------------------------------- R10：delta 与 snapshot 同步骤
def test_delta_without_snapshot_rejected() -> None:
    case = _case(steps=[{
        "action": "POST /api/gacha/draw",
        "payload": {"pool_id": "pool_standard", "times": 1},
        "expect": {"code": 0, "data.gems": "delta:-160"},
    }])
    result = validator.validate_case(case)
    assert not result.ok
    assert any("R10" in i.rule for i in result.issues)


def test_delta_with_snapshot_passes() -> None:
    case = _case(steps=[{
        "action": "POST /api/gacha/draw",
        "payload": {"pool_id": "pool_standard", "times": 1},
        "expect": {"code": 0, "data.gems": "delta:-160"},
        "snapshot": ["gems"],
    }])
    assert validator.validate_case(case).ok


def test_snapshot_without_delta_is_warning_only() -> None:
    case = _case(steps=[{
        "action": "POST /api/gacha/draw",
        "payload": {"pool_id": "pool_standard", "times": 1},
        "expect": {"code": 0, "data.items": "len:1"},
        "snapshot": ["gems"],
    }])
    result = validator.validate_case(case)
    assert result.ok
    assert any("R10" in i.rule and i.level == "warn" for i in result.issues)


# ---------------------------------------------------------------- R11：断言字段必须在响应契约里
def test_unknown_response_field_rejected() -> None:
    """真实模型的典型错误：编了响应里不存在的字段（bag_used / mail_pending）。"""
    case = _case(steps=[
        {"action": "GET /api/bag", "payload": {},
         "expect": {"code": 0, "data.bag_used": "lt:200"}},
        {"action": "POST /api/gacha/draw",
         "payload": {"pool_id": "pool_standard", "times": 1},
         "expect": {"code": 0, "data.items": "len:1"}},
    ])
    result = validator.validate_case(case)
    assert not result.ok
    assert any("R11" in i.rule and "data.bag_used" in i.message for i in result.issues)


def test_valid_response_field_passes() -> None:
    case = _case(steps=[
        {"action": "GET /api/bag", "payload": {},
         "expect": {"code": 0, "data.gems": "ge:160", "data.used": "lt:200"}},
        {"action": "POST /api/gacha/draw",
         "payload": {"pool_id": "pool_standard", "times": 1},
         "expect": {"code": 0, "data.items": "len:1"}},
    ])
    assert validator.validate_case(case).ok


def test_wrong_endpoint_field_rejected() -> None:
    """结算接口只返回 reward_id/status/win，断言 data.items 是错的（物品要去奖励单查）。"""
    case = _case(steps=[
        {"action": "POST /api/stage/enter", "payload": {"stage_id": 1},
         "expect": {"code": 0, "data.battle_id": "not_empty"}},
        {"action": "POST /api/stage/settle",
         "payload": {"battle_id": "$battle_id", "win": False},
         "expect": {"code": 0, "data.items": "len:0"}},
    ])
    result = validator.validate_case(case)
    assert not result.ok
    assert any("R11" in i.rule for i in result.issues)
