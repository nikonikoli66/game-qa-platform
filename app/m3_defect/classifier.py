"""失败分类器 —— 把"红了"拆成"到底是谁的锅"。

为什么必须规则化而不是丢给大模型？
    1. 可解释：每个结论都能指到具体日志特征，开发问"凭什么说是环境问题"能答上来；
    2. 可回归：规则能写单测，模型换个版本结论可能就漂了；
    3. 可操作：不同类别对应完全不同的下一步动作（提单 / 清数据 / 修脚本）；
    4. 成本：每条失败都调一次模型，跑 300 条用例就是 300 次调用。

分类的判定顺序是有讲究的：脚本级异常 → 异步超时 → 服务端 5xx → 登录/环境 →
断言内容（按错误码语义细分）→ 兜底人工。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class FailureKind(str, Enum):
    PASS = "PASS"
    SCRIPT_ERROR = "SCRIPT_ERROR"
    BUSINESS_BUG = "BUSINESS_BUG"
    SERVER_ERROR = "SERVER_ERROR"
    ENV_ISSUE = "ENV_ISSUE"
    ASYNC_TIMEOUT = "ASYNC_TIMEOUT"
    TEST_DATA_ISSUE = "TEST_DATA_ISSUE"
    UNKNOWN = "UNKNOWN"


KIND_LABEL: dict[str, str] = {
    "PASS": "通过",
    "SCRIPT_ERROR": "脚本问题",
    "BUSINESS_BUG": "业务缺陷",
    "SERVER_ERROR": "服务端异常",
    "ENV_ISSUE": "环境/脏数据",
    "ASYNC_TIMEOUT": "异步超时",
    "TEST_DATA_ISSUE": "用例数据问题",
    "UNKNOWN": "待人工确认",
}

KIND_ACTION: dict[str, str] = {
    "PASS": "无需处理",
    "SCRIPT_ERROR": "走自愈修复脚本，不生成缺陷单",
    "BUSINESS_BUG": "生成缺陷单提交开发",
    "SERVER_ERROR": "查服务端错误日志与堆栈，按 P0 跟进",
    "ENV_ISSUE": "清理账号/补前置数据后重跑，不计入产品质量",
    "ASYNC_TIMEOUT": "查 MQ 消费堆积与重试策略",
    "TEST_DATA_ISSUE": "回到用例层修正断言路径与参数",
    "UNKNOWN": "人工看日志确认",
}

# 错误码语义：断言期望与实际的差异方向，决定责任归属
CODE_TO_KIND: dict[int, str] = {
    1001: FailureKind.TEST_DATA_ISSUE.value,   # 参数非法 → 用例参数写错
    1002: FailureKind.ENV_ISSUE.value,         # 登录态失效 → 环境
    1003: FailureKind.ENV_ISSUE.value,         # 资源不足 → 前置数据没造出来
    1004: FailureKind.ENV_ISSUE.value,         # 对象不存在 → 配置/数据缺失
}

SCRIPT_ERROR_PATTERNS = (
    r"SyntaxError", r"IndentationError", r"TabError",
    r"NameError", r"AttributeError", r"ModuleNotFoundError", r"ImportError",
    r"UnboundLocalError", r"TypeError: .*not subscriptable", r"TypeError: .*not callable",
)
# 同时匹配「期望 X，实际 Y」与「期望变化 X，实际变化 Y」两种断言文案
ASSERT_RE = re.compile(r"([\w\.\[\]]+)\s*(?:长度)?\s*期望(?:变化)?\s*(.*?)，实际(?:变化)?\s*([^\n]*)")


@dataclass
class Classification:
    kind: FailureKind
    reason: str
    confidence: str = "中"
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return KIND_LABEL.get(self.kind.value, self.kind.value)

    @property
    def action(self) -> str:
        return KIND_ACTION.get(self.kind.value, "")

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "label": self.label,
            "reason": self.reason,
            "confidence": self.confidence,
            "action": self.action,
            "detail": self.detail,
        }


def _parse_assertion(log: str) -> tuple[str, str, str] | None:
    matches = ASSERT_RE.findall(log)
    if not matches:
        return None
    path, expected, actual = matches[-1]
    return path.strip(), expected.strip(), actual.strip()


def classify(log: str, exit_code: int = 1, timed_out: bool = False) -> Classification:
    log = log or ""
    if exit_code == 0 and not timed_out:
        return Classification(FailureKind.PASS, "pytest 退出码 0，无失败用例", "高")

    if timed_out:
        return Classification(
            FailureKind.ENV_ISSUE,
            "用例执行超时被强制终止，通常是轮询等待或网络卡住，先视为环境问题",
            "低",
        )

    for pattern in SCRIPT_ERROR_PATTERNS:
        if re.search(pattern, log):
            return Classification(
                FailureKind.SCRIPT_ERROR,
                f"日志命中脚本级异常特征 /{pattern}/，属于测试脚本自身报错",
                "高",
                {"pattern": pattern},
            )

    if "异步断言在" in log or "SLA 超时" in log or "PollTimeout" in log:
        return Classification(
            FailureKind.ASYNC_TIMEOUT,
            "异步断言在截止时间内未满足，奖励/消息未按 SLA 到账",
            "高",
        )

    if re.search(r"HTTP 5\d\d", log):
        code = re.search(r"HTTP (5\d\d)", log)
        return Classification(
            FailureKind.SERVER_ERROR,
            f"接口返回 HTTP {code.group(1) if code else '5xx'}，服务端异常",
            "高",
        )

    if "登录失败" in log:
        return Classification(FailureKind.ENV_ISSUE, "登录失败，登录态/测试账号不可用", "高")

    if "断言路径" in log and "在响应中不存在" in log:
        return Classification(
            FailureKind.TEST_DATA_ISSUE,
            "用例断言了响应里不存在的字段路径，属于用例数据问题（接口契约没对齐）",
            "高",
        )

    parsed = _parse_assertion(log)
    if parsed:
        path, expected, actual = parsed
        detail = {"path": path, "expected": expected, "actual": actual}
        if path == "code":
            try:
                exp_code = int(re.sub(r"[^\d-]", "", expected) or "0")
            except ValueError:
                exp_code = 0
            try:
                act_code = int(re.sub(r"[^\d-]", "", actual) or "0")
            except ValueError:
                act_code = 0
            detail.update({"expected_code": exp_code, "actual_code": act_code})

            # 先看「实际返回的错误码」是否属于环境/数据类：1002 登录态、1003 资源、1004 对象不存在。
            # 这类码说明现场根本没准备好（比如用例把 battle_id 写死了 → 1004），
            # 不论期望值是多少，都不该判成产品缺陷。
            if exp_code != act_code and act_code in CODE_TO_KIND:
                reasons = {1001: "参数非法", 1002: "登录态失效", 1003: "资源不足", 1004: "对象不存在"}
                return Classification(
                    FailureKind(CODE_TO_KIND[act_code]),
                    f"实际返回 {act_code}（{reasons.get(act_code, '')}），"
                    f"期望 {exp_code}。该错误码说明前置数据/依赖对象没准备好，优先按环境与数据问题处理",
                    "中",
                    detail,
                )
            if exp_code == 0 and act_code != 0:
                return Classification(
                    FailureKind.BUSINESS_BUG,
                    f"期望 code=0 但返回 {act_code}，且非已知环境类错误码，判定为业务缺陷",
                    "高", detail,
                )
            if exp_code != 0 and act_code == 0:
                if exp_code == 1003:
                    return Classification(
                        FailureKind.ENV_ISSUE,
                        "用例期望资源不足(1003)，实际资源充足，说明 GM 指令未生效或前置数据没清干净",
                        "中", detail,
                    )
                return Classification(
                    FailureKind.BUSINESS_BUG,
                    f"用例期望被拦截（code={exp_code}）但实际返回 0，"
                    f"说明该分支的校验/幂等保护缺失，属业务缺陷",
                    "高", detail,
                )
            return Classification(FailureKind.BUSINESS_BUG,
                                  f"错误码期望 {exp_code} 实际 {act_code}", "中", detail)

        # 断言"瞬时状态"失败：异步链路已经流转过去了，属于用例时序设计问题，不是产品缺陷
        if path == "data.status" and expected.strip("'\"") == "pending" \
                and actual.strip("'\"") == "claimable":
            return Classification(
                FailureKind.TEST_DATA_ISSUE,
                "断言瞬时状态 pending 失败（实际已流转到 claimable）：异步链路应该轮询断言，"
                "而不是赌某个瞬间，属于用例时序设计问题",
                "高", detail,
            )

        return Classification(
            FailureKind.BUSINESS_BUG,
            f"业务字段 {path} 期望 {expected} 实际 {actual}，接口行为与需求不符",
            "高", detail,
        )

    if "KeyError" in log:
        return Classification(
            FailureKind.TEST_DATA_ISSUE,
            "断言路径在响应中不存在（KeyError），可能是用例写了不存在的字段路径；"
            "若确认字段应由接口返回，则升级为业务缺陷",
            "低",
        )

    if "AssertionError" in log:
        return Classification(FailureKind.UNKNOWN, "存在断言失败但无法自动归因，需人工看日志", "低")

    return Classification(FailureKind.UNKNOWN, "未匹配到已知失败特征，需人工确认", "低")
