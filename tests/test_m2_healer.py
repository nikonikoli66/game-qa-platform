"""自愈规则库单测。"""
from __future__ import annotations

from app.m2_automation import healer

BASE = """import json
import os

import requests


def _check(body, spec):
    assert body == spec


def test_case_01():
    _check({}, {})
"""


def test_typo_identifier_fixed() -> None:
    broken = BASE + "    _chek({}, {})\n"
    result = healer.heal(broken, "NameError: name '_chek' is not defined")
    assert result.applied
    assert result.source.startswith("rule:")
    assert "_chek" not in result.code


def test_missing_import_fixed() -> None:
    broken = BASE + "    time.sleep(1)\n"
    result = healer.heal(broken, "NameError: name 'time' is not defined")
    assert result.applied
    assert "import time" in result.code


def test_missing_import_not_duplicated() -> None:
    broken = BASE + "    time.sleep(1)\n"
    once = healer.heal(broken, "NameError: name 'time' is not defined")
    twice = healer.heal(once.code, "NameError: name 'time' is not defined")
    assert twice.code.count("import time") == 1


def test_unknown_error_not_auto_fixed() -> None:
    """离线模式下规则未命中就不修——宁可留给人看，也不要瞎改。"""
    result = healer.heal(BASE, "AssertionError: code 期望 0，实际 1003")
    assert not result.applied
    assert result.code == BASE
