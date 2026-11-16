"""失败分类器单测 —— 性能上最关键的模块，规则错了整个缺陷看板都是错的。"""
from __future__ import annotations

from app.m3_defect.classifier import FailureKind, classify


def test_pass() -> None:
    assert classify("passed", exit_code=0).kind is FailureKind.PASS


def test_name_error_is_script_problem() -> None:
    log = "E   NameError: name '_chek' is not defined"
    assert classify(log, exit_code=1).kind is FailureKind.SCRIPT_ERROR


def test_syntax_error_is_script_problem() -> None:
    assert classify("SyntaxError: invalid syntax", 1).kind is FailureKind.SCRIPT_ERROR


def test_async_timeout() -> None:
    log = "AssertionError: 异步断言在 16.0s 内未满足（疑似 SLA 超时）: ..."
    assert classify(log, 1).kind is FailureKind.ASYNC_TIMEOUT


def test_server_error() -> None:
    assert classify('AssertionError: HTTP 502: bad gateway', 1).kind is FailureKind.SERVER_ERROR


def test_login_failure_is_env() -> None:
    assert classify("AssertionError: 登录失败: {...}", 1).kind is FailureKind.ENV_ISSUE


def test_expected_success_but_resource_lack_is_env() -> None:
    log = "AssertionError: code 期望 0，实际 1003"
    cls = classify(log, 1)
    assert cls.kind is FailureKind.ENV_ISSUE
    assert cls.detail["actual_code"] == 1003


def test_expected_success_but_param_invalid_is_test_data() -> None:
    log = "AssertionError: code 期望 0，实际 1001"
    assert classify(log, 1).kind is FailureKind.TEST_DATA_ISSUE


def test_expected_blocked_but_succeeded_is_business_bug() -> None:
    """幂等用例期望 1005，实际返回 0 → 幂等保护缺失，这是真 bug，不能自愈。"""
    log = "AssertionError: code 期望 1005，实际 0"
    cls = classify(log, 1)
    assert cls.kind is FailureKind.BUSINESS_BUG


def test_business_field_mismatch_is_business_bug() -> None:
    log = "AssertionError: data.items[0].rarity 期望 'SSR'，实际 'R'"
    cls = classify(log, 1)
    assert cls.kind is FailureKind.BUSINESS_BUG
    assert cls.detail["path"] == "data.items[0].rarity"


def test_timeout_is_env() -> None:
    assert classify("", 1, timed_out=True).kind is FailureKind.ENV_ISSUE


def test_unknown_assertion_needs_human() -> None:
    assert classify("AssertionError: something odd", 1).kind is FailureKind.UNKNOWN
