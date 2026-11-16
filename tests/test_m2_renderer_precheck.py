"""渲染器与预检单测：生成的脚本必须能编译、能通过预检、不越界。"""
from __future__ import annotations

import ast

from app.api_catalog import lookup
from app.m1_cases.schema import normalize_case
from app.m2_automation import precheck, renderer


ASYNC_CASE = normalize_case({
    "title": "通关后异步奖励到账",
    "category": "正向",
    "priority": "P0",
    "precondition": "账号体力充足",
    "steps": [
        {"action": "POST /api/stage/enter", "payload": {"stage_id": 1},
         "expect": {"code": 0, "data.battle_id": "not_empty"}},
        {"action": "POST /api/stage/settle",
         "payload": {"battle_id": "$battle_id", "win": True},
         "expect": {"code": 0, "data.reward_id": "not_empty"}},
        {"action": "GET /api/reward/{reward_id}", "payload": {"reward_id": "$reward_id"},
         "expect": {"code": 0, "data.status": "claimable"}, "wait_async": True},
    ],
    "expected": "轮询在 SLA 内返回 claimable",
    "risk_tags": ["异步奖励到账"],
})


def test_rendered_script_compiles() -> None:
    code = renderer.render_script(ASYNC_CASE, "case_test_1", index=1)
    ast.parse(code)  # 语法必须合法
    assert "def test_case_01" in code
    assert "assert " in code


def test_render_passes_precheck() -> None:
    code = renderer.render_script(ASYNC_CASE, "case_test_1", index=1)
    result = precheck.precheck(code)
    assert result.ok, result.errors
    names = {c["name"]: c["ok"] for c in result.checks}
    assert names["语法检查"] and names["危险调用拦截"] and names["用例结构"]


def test_variable_reference_rendered() -> None:
    code = renderer.render_script(ASYNC_CASE, "case_test_2", index=2)
    assert '_VARS.get("battle_id")' in code
    assert '"/api/reward/" + str(_VARS.get("reward_id", ""))' in code
    assert "_wait_until(" in code


def test_snapshot_rendered_for_delta_assert() -> None:
    case = normalize_case({
        "title": "扣钻精确",
        "category": "正向",
        "priority": "P0",
        "precondition": "钻石充足",
        "steps": [{
            "action": "POST /api/gacha/draw",
            "payload": {"pool_id": "pool_standard", "times": 1},
            "expect": {"code": 0, "data.gems": "delta:-160"},
            "snapshot": ["gems"],
        }],
        "expected": "钻石减少 160",
        "risk_tags": ["资源扣减一致性"],
    })
    code = renderer.render_script(case, "case_test_3")
    assert 'snap = _snapshot(["gems"])' in code
    assert "_check(body," in code and "snap)" in code


def test_precheck_rejects_syntax_error() -> None:
    result = precheck.precheck("def test_x(:\n    pass\n")
    assert not result.ok
    assert "语法错误" in result.labeled()


def test_precheck_rejects_dangerous_call() -> None:
    code = "import os\n\ndef test_x():\n    os.system('rm -rf /')\n    assert True\n"
    result = precheck.precheck(code)
    assert not result.ok
    assert "危险调用" in result.labeled()


def test_precheck_rejects_forbidden_module() -> None:
    code = "import subprocess\n\ndef test_x():\n    assert True\n"
    result = precheck.precheck(code)
    assert not result.ok
    assert "禁止的模块" in result.labeled()


def test_precheck_rejects_missing_assert() -> None:
    code = "def test_x():\n    x = 1\n"
    result = precheck.precheck(code)
    assert not result.ok
    assert any("assert" in e for e in result.errors)


def test_generated_scripts_only_use_catalog_endpoints() -> None:
    """渲染出的 action 必须全部落在契约目录里（防模板写错接口）。"""
    for step in ASYNC_CASE["steps"]:
        assert lookup(step["action"]) is not None, step["action"]


def test_resolve_supports_both_path_styles() -> None:
    """模型既会写 items[0].rarity 也会写 items.0.rarity，两种都要能解析。"""
    code = renderer.render_script(ASYNC_CASE, "case_test_4", index=4)
    namespace: dict = {}
    exec(compile(code, "<generated>", "exec"), namespace)  # noqa: S102 - 只定义函数，无副作用
    resolve = namespace["_resolve"]
    body = {"data": {"items": [{"rarity": "SSR"}, {"rarity": "R"}]}}
    assert resolve(body, "data.items[0].rarity") == "SSR"
    assert resolve(body, "data.items.0.rarity") == "SSR"
    assert resolve(body, "data.items.1.rarity") == "R"
