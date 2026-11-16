"""自动化执行引擎：渲染 → 预检 → 沙箱执行 → 解析 → 自愈 → 归类。

工程上只做三件事，但每件都踩过坑：
    1. 每条用例独立账号 + 执行前后清理 —— 否则用例之间互相污染，失败率虚高；
    2. 子进程执行 + 超时强杀 —— 脚本卡死不能拖垮整个批次；
    3. 用 JUnit XML 取结构化结果而不是 grep 日志 —— 日志文案会变，XML 不会。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app import config, repository
from app.m2_automation import env_manager, healer, precheck, renderer
from app.m3_defect.classifier import FailureKind, classify


@dataclass
class RunResult:
    case_id: str
    case_title: str
    status: str = "failed"
    failure_kind: str = FailureKind.UNKNOWN.value
    reason: str = ""
    duration_ms: int = 0
    attempts: int = 0
    script_path: str = ""
    log: str = ""
    heal_notes: list[str] = field(default_factory=list)
    precheck: dict[str, Any] = field(default_factory=dict)
    account: str = ""
    run_id: str = ""

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "case_title": self.case_title,
            "status": self.status,
            "failure_kind": self.failure_kind,
            "reason": self.reason,
            "duration_ms": self.duration_ms,
            "attempts": self.attempts,
            "script_path": self.script_path,
            "heal_notes": self.heal_notes,
            "precheck": self.precheck,
            "account": self.account,
            "run_id": self.run_id,
        }


def _subprocess_env(account: str) -> dict[str, str]:
    env = os.environ.copy()
    env["QA_ACCOUNT"] = account
    env["GAME_API_BASE"] = config.GAME_API_BASE
    env["ASYNC_REWARD_SLA"] = str(config.ASYNC_REWARD_SLA)
    env["QA_HTTP_TIMEOUT"] = str(config.HTTP_TIMEOUT)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _parse_junit(xml_path: Path) -> tuple[bool, str]:
    """返回 (是否通过, 失败详情)。"""
    if not xml_path.exists():
        return False, "未生成 JUnit 报告，pytest 可能未正常启动"
    try:
        root = ET.parse(str(xml_path)).getroot()
    except ET.ParseError as exc:
        return False, f"JUnit 报告解析失败: {exc}"
    total = 0
    failures: list[str] = []
    for tc in root.iter("testcase"):
        total += 1
        node = tc.find("failure")
        if node is None:
            node = tc.find("error")
        if node is not None:
            message = node.get("message") or ""
            text = (node.text or "").strip()
            failures.append(f"{tc.get('name')}: {message}\n{text}")
    if total == 0:
        return False, "用例未被收集（脚本可能没有 test_ 函数或有语法问题）"
    if failures:
        return False, "\n".join(failures)
    return True, ""


def _run_pytest(script_path: Path, account: str, tag: str) -> tuple[int, str, bool, str]:
    xml_path = config.ARTIFACTS_DIR / f"junit_{tag}.xml"
    if xml_path.exists():
        xml_path.unlink()
    cmd = [
        sys.executable, "-m", "pytest", str(script_path),
        "-q", "--tb=short", "-p", "no:cacheprovider",
        f"--junitxml={xml_path}",
    ]
    started = time.time()
    timed_out = False
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(config.SCRIPTS_DIR),
            env=_subprocess_env(account),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=config.SCRIPT_TIMEOUT,
        )
        out, err, code = proc.stdout or "", proc.stderr or "", proc.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        out = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        err = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        code = -1
    elapsed = int((time.time() - started) * 1000)

    passed, junit_detail = _parse_junit(xml_path)
    log = f"$ {' '.join(cmd)}\n\n[stdout]\n{out}\n[stderr]\n{err}\n[junit]\n{junit_detail}"
    if timed_out:
        log += f"\n[超时] 执行超过 {config.SCRIPT_TIMEOUT}s 被强制终止"
    return (0 if passed else code, log, timed_out, junit_detail)


def execute(case: dict[str, Any], case_id: str, index: int = 1,
            batch_id: str = "", account: str | None = None,
            persist: bool = True, tag: str = "") -> RunResult:
    account = account or env_manager.account_for(case_id)
    result = RunResult(case_id=case_id, case_title=case.get("title", ""), account=account)
    started = time.time()

    # 1) 环境准备（账号初始化 + 清脏数据）
    env_manager.prepare_account(account)

    # 2) 渲染脚本
    script_path = Path(renderer.write_script(case, case_id, index, tag))
    result.script_path = str(script_path)
    code = script_path.read_text(encoding="utf-8")

    # 3) 静态预检（不通过先尝试自愈一次，仍然不过就不允许执行）
    pc = precheck.precheck(code, filename=script_path.name)
    result.precheck = pc.as_dict()
    if not pc.ok:
        healed = healer.heal(code, "\n".join(pc.errors))
        if healed.applied and precheck.precheck(healed.code, script_path.name).ok:
            code = healed.code
            script_path.write_text(code, encoding="utf-8")
            result.heal_notes.append(f"预检未通过并由{healed.source}修复：{healed.note}")
            pc = precheck.precheck(code, filename=script_path.name)
            result.precheck = pc.as_dict()
    if not pc.ok:
        result.status = "failed"
        result.failure_kind = FailureKind.SCRIPT_ERROR.value
        result.reason = "静态预检未通过，已阻止执行：" + pc.labeled()
        result.log = "预检错误:\n" + "\n".join(pc.errors)
        result.duration_ms = int((time.time() - started) * 1000)
        return _finish(result, case_id, batch_id, persist)

    # 4) 执行 + 自愈闭环
    kind: FailureKind
    reason = ""
    log = ""
    for attempt in range(1, config.MAX_HEAL_ROUNDS + 2):
        result.attempts = attempt
        code_exit, log, timed_out, junit_detail = _run_pytest(script_path, account,
                                                              f"{case_id}_{attempt}")
        result.log = log
        cls = classify(log if not junit_detail else log + "\n" + junit_detail,
                       exit_code=code_exit, timed_out=timed_out)
        kind, reason = cls.kind, cls.reason
        if cls.kind is FailureKind.PASS:
            result.status = "passed"
            result.failure_kind = FailureKind.PASS.value
            result.reason = "全部断言通过"
            break

        # 只有「脚本自身的问题」才允许自愈；业务缺陷绝不放过
        if cls.kind is FailureKind.SCRIPT_ERROR and attempt <= config.MAX_HEAL_ROUNDS:
            healed = healer.heal(code, log + "\n" + junit_detail)
            if healed.applied:
                code = healed.code
                script_path.write_text(code, encoding="utf-8")
                check = precheck.precheck(code, filename=script_path.name)
                result.heal_notes.append(
                    f"第 {attempt} 次失败→{healed.source} 修复：{healed.note}"
                    f"（修复后预检{'通过' if check.ok else '仍未通过'}）"
                )
                if check.ok:
                    continue
                break
            result.heal_notes.append(f"第 {attempt} 次失败：{healed.note}")
        break

    if result.status != "passed":
        result.status = "failed"
        result.failure_kind = kind.value
        result.reason = reason

    result.duration_ms = int((time.time() - started) * 1000)

    # 5) 后置清理
    env_manager.cleanup_account(account)
    return _finish(result, case_id, batch_id, persist)


def _finish(result: RunResult, case_id: str, batch_id: str, persist: bool) -> RunResult:
    if not persist:
        return result
    # 每次执行都存一份脚本快照：出问题时能精确复现"当时跑的是哪版脚本"
    code = ""
    if result.script_path and Path(result.script_path).exists():
        code = Path(result.script_path).read_text(encoding="utf-8")
    script_id = repository.save_script(
        case_id, result.script_path, code, precheck=result.precheck
    )
    run_id = repository.create_run(batch_id or new_batch_id(), case_id,
                                   result.case_title, script_id)
    repository.finish_run(
        run_id, result.status, result.failure_kind, result.duration_ms,
        result.attempts, (result.log or "")[-4000:],
        "；".join(result.heal_notes),
    )
    result.run_id = run_id
    return result


def new_batch_id() -> str:
    return "batch_" + datetime.now().strftime("%Y%m%d_%H%M%S")
