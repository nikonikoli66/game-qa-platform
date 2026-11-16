"""编排层：把五个模块串成一条可执行流水线。

为什么单独抽一层？
    UI（Streamlit）不应该知道"用例怎么执行、缺陷怎么生成"；
    CLI/定时任务也不应该复制一套逻辑。所有业务编排收敛到这里，
    UI 只是薄薄一层展示，命令行、定时回归、CI 都复用同一套入口。
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from app import config, repository
from app.m1_cases import generator
from app.m2_automation import env_manager, healer, precheck, renderer, runner
from app.m3_defect import analyzer
from app.m3_defect.classifier import KIND_LABEL, FailureKind, classify
from app.m4_analytics import advisor, collector, metrics

# 只有这几类失败才值得开缺陷单；脚本问题/环境问题提单只会被开发打回
DEFECT_KINDS = {
    FailureKind.BUSINESS_BUG,
    FailureKind.SERVER_ERROR,
    FailureKind.ASYNC_TIMEOUT,
}

PRD_FILES = [
    ("限定卡池「星海祈愿」", "gacha_prd.md"),
    ("主线关卡结算与异步发奖", "stage_prd.md"),
]


# ============================================================ 环境
def ensure_server(base: str | None = None) -> bool:
    return env_manager.server_alive(base)


# ============================================================ 用例准备
def seed_cases(force: bool = False, count: int = 12) -> dict[str, Any]:
    """从示例 PRD 生成整套用例。幂等：已存在则跳过，force 则重建。"""
    if repository.count_cases() > 0 and not force:
        return {
            "skipped": True,
            "total": repository.count_cases(),
            "valid": repository.count_cases("valid"),
        }
    if force:
        repository.clear_cases()
        repository.clear_runs()

    outcomes = []
    for name, filename in PRD_FILES:
        path = config.REQUIREMENT_DIR / filename
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        outcome = generator.generate(name, text, count=count, persist=True)
        outcomes.append(outcome)

    total = sum(o.report.total for o in outcomes)
    valid = sum(len(o.cases) for o in outcomes)
    return {
        "skipped": False,
        "total": total,
        "valid": valid,
        "requirements": [
            {
                "name": o.profile.name,
                "domain": o.profile.domain,
                "engine": o.engine,
                "stats": o.report.as_dict(),
                "risk_matrix": o.risk_matrix,
                "notes": o.notes,
            }
            for o in outcomes
        ],
    }


# ============================================================ 执行
def run_batch(case_ids: list[str] | None = None,
              progress: Callable[[int, int, str], None] | None = None) -> dict[str, Any]:
    batch_id = runner.new_batch_id()
    if case_ids:
        cases = [c for c in (repository.get_case(cid) for cid in case_ids) if c]
    else:
        cases = repository.list_cases(status="valid")

    results: list[runner.RunResult] = []
    defects: list[dict[str, Any]] = []

    for idx, case in enumerate(cases, 1):
        if progress:
            progress(idx, len(cases), case.get("title", ""))
        result = runner.execute(case, case["id"], index=idx, batch_id=batch_id)
        results.append(result)

        if not result.passed:
            kind = FailureKind(result.failure_kind)
            if kind in DEFECT_KINDS:
                defects.append(
                    analyzer.create_defect(result.run_id, case, kind, result.log, result.account)
                )

    passed = [r for r in results if r.passed]
    failed = [r for r in results if not r.passed]
    by_kind: dict[str, int] = {}
    for r in failed:
        by_kind[r.failure_kind] = by_kind.get(r.failure_kind, 0) + 1

    return {
        "batch_id": batch_id,
        "total": len(results),
        "passed": len(passed),
        "failed": len(failed),
        "pass_rate": round(len(passed) / len(results) * 100, 1) if results else 0.0,
        "by_kind": {KIND_LABEL.get(k, k): v for k, v in by_kind.items()},
        "defects": defects,
        "results": [r.as_dict() for r in results],
        "healed": [r.as_dict() for r in results if r.heal_notes],
        "regression": regression(batch_id),
    }


def regression(batch_id: str) -> dict[str, Any]:
    """版本验证：和上一个批次对比，看本次新增/修复了哪些失败。"""
    prev = repository.previous_batch(batch_id)
    if not prev:
        return {"available": False, "reason": "没有历史批次可对比"}
    cur = {r["case_id"]: r for r in repository.list_runs(batch_id)}
    old = {r["case_id"]: r for r in repository.list_runs(prev)}

    new_failures = [
        cid for cid, r in cur.items()
        if r["status"] != "passed" and cid in old and old[cid]["status"] == "passed"
    ]
    fixed = [
        cid for cid, r in cur.items()
        if r["status"] == "passed" and cid in old and old[cid]["status"] != "passed"
    ]
    persistent = [
        cid for cid, r in cur.items()
        if r["status"] != "passed" and cid in old and old[cid]["status"] != "passed"
    ]
    title = {r["case_id"]: r["case_title"] for r in repository.list_runs(batch_id)}
    return {
        "available": True,
        "previous_batch": prev,
        "new_failures": [{"case_id": c, "title": title.get(c, "")} for c in new_failures],
        "fixed": [{"case_id": c, "title": title.get(c, "")} for c in fixed],
        "persistent": [{"case_id": c, "title": title.get(c, "")} for c in persistent],
    }


# ============================================================ 体验数据
def collect_telemetry(players: int = 24, stages: int = 15) -> dict[str, Any]:
    batch_id = "tel_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    info = collector.collect(batch_id, players=players, stages=stages)
    repository.set_meta("last_telemetry_batch", batch_id)
    return info


def experience_report(batch_id: str | None = None) -> dict[str, Any]:
    batch_id = batch_id or repository.latest_telemetry_batch()
    if not batch_id:
        return {"empty": True, "batch_id": "", "advice": {"summary": "尚未采集行为数据"}}
    report = metrics.build_report(batch_id)
    advice = advisor.advise(report)
    return {**report, "advice": advice, "markdown": advisor.markdown_report(report, advice)}


# ============================================================ 自愈演示
def demo_self_heal() -> dict[str, Any]:
    """端到端演示：注入故障 → 执行失败 → 归类为脚本问题 → 规则修复 → 重跑通过。

    故障注入方式刻意选了两种最有代表性的：漏 import 和标识符笔误，
    这两种占了 AI 生成脚本报错的绝大多数。
    """
    cases = repository.list_cases(status="valid")
    if not cases:
        return {"ok": False, "error": "还没有用例，请先生成用例"}
    # 优先选一条带异步轮询的用例：这样"漏 import time"和"标识符笔误"两个故障都会真实触发
    case = next(
        (c for c in cases if any(s.get("wait_async") for s in c["steps"])),
        cases[0],
    )
    case_id = case["id"]
    account = env_manager.account_for(case_id + "_heal")
    env_manager.prepare_account(account)

    src = renderer.render_script(case, case_id, index=99)
    # 故障1：只改函数定义名，调用点保持原名 → 运行时 NameError（模拟 AI 改错函数名）
    broken = src.replace("def _check(body", "def _chek(body", 1)
    # 故障2：漏 import time → 第一个故障修好后才会暴露，形成两轮自愈
    broken = broken.replace("import time\n", "", 1)
    if broken == src:
        return {"ok": False, "error": "故障注入失败（脚本结构已变化），请检查 renderer 模板"}

    path = config.SCRIPTS_DIR / "test_99_selfheal_demo.py"
    path.write_text(broken, encoding="utf-8")

    steps: list[dict[str, Any]] = [{
        "step": "1. 注入故障",
        "detail": "把 `def _check` 改名为 `def _chek`（调用点不变）+ 删除 `import time`，"
                  "模拟 AI 生成/改脚本的两类高频错误",
        "ok": True,
    }]
    pc = precheck.precheck(broken, path.name)
    steps.append({
        "step": "2. 静态预检",
        "detail": f"语义错误预检抓不到（语法合法），需靠运行时暴露：{pc.labeled()}",
        "ok": pc.ok,
    })

    code = broken
    log = ""
    passed = False
    for attempt in range(1, 4):
        code_exit, log, timed_out, junit_detail = runner._run_pytest(path, account, f"heal_{attempt}")
        cls = classify(log + "\n" + junit_detail, exit_code=code_exit, timed_out=timed_out)
        steps.append({
            "step": f"3.{attempt} 执行并分类",
            "detail": f"结果：{cls.label} — {cls.reason}",
            "ok": cls.kind is FailureKind.PASS,
        })
        if cls.kind is FailureKind.PASS:
            passed = True
            break
        if cls.kind is not FailureKind.SCRIPT_ERROR:
            steps.append({"step": "4. 终止自愈", "detail": "非脚本问题，拒绝自愈（避免掩盖业务缺陷）",
                          "ok": False})
            break
        healed = healer.heal(code, log + "\n" + junit_detail)
        steps.append({
            "step": f"3.{attempt} 自愈",
            "detail": f"{healed.source} → {healed.note}" if healed.applied else healed.note,
            "ok": healed.applied,
        })
        if not healed.applied:
            break
        code = healed.code
        path.write_text(code, encoding="utf-8")

    final_pc = precheck.precheck(code, path.name)
    return {
        "ok": passed and final_pc.ok,
        "steps": steps,
        "final_pass": passed,
        "fixed_code_excerpt": "\n".join(code.splitlines()[:14]),
        "heal_count": sum(1 for s in steps if s["step"].endswith("自愈") and s["ok"]),
    }


# ============================================================ 一键全流程
def full_demo(progress: Callable[[int, int, str], None] | None = None) -> dict[str, Any]:
    started = datetime.now()
    server_ok = ensure_server()
    seeded = seed_cases(force=True)
    batch = run_batch(progress=progress)
    telemetry = collect_telemetry()
    exp = experience_report()
    elapsed = (datetime.now() - started).total_seconds()
    return {
        "server_ok": server_ok,
        "seeded": seeded,
        "batch": batch,
        "telemetry": telemetry,
        "experience": exp,
        "elapsed_s": round(elapsed, 1),
    }


def reset_all(keep_runtime_files: bool = False) -> None:
    repository.clear_cases()
    repository.clear_runs()
    repository.clear_telemetry()
    repository.set_meta("last_telemetry_batch", "")
    if not keep_runtime_files:
        for d in (config.SCRIPTS_DIR, config.ARTIFACTS_DIR):
            if Path(d).exists():
                shutil.rmtree(d, ignore_errors=True)
            Path(d).mkdir(parents=True, exist_ok=True)
