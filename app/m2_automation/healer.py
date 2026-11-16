"""脚本自愈（AI 修脚本 + 确定性规则优先）。

这里有一个 QA 视角的关键判断，也是这个模块最容易被追问的设计点：

    自愈只允许修「脚本自身的错」，绝不允许修「业务 bug」的现场。

如果让 AI 看到"断言失败"就去改断言，那平台永远全绿、缺陷全被掩盖——这是把 AI
接进测试流程时最容易犯的错。所以失败必须先经分类器判定为 SCRIPT_ERROR，
才允许进入自愈；业务缺陷一律出缺陷单。

修复策略同样是"规则优先、LLM 兜底"：
    规则库覆盖高频错误（漏 import、函数名打错、变量名笔误），确定性、可审计、零成本；
    规则未命中再交给大模型做语义修复，且修复后必须重新过一次静态预检。
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Any, Callable

from app import config
from app.llm import prompts
from app.llm.client import get_llm

IMPORTABLE = {"json", "os", "time", "re", "requests", "pytest", "math", "random",
              "datetime", "typing", "collections"}
KNOWN_SYMBOLS = [
    "_check", "_call", "_login", "_snapshot", "_capture", "_resolve", "_compare",
    "_wait_until", "_render_payload", "requests", "json", "time", "os",
]


@dataclass
class HealResult:
    applied: bool
    code: str
    source: str = "none"      # rule:<name> | llm | none
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"applied": self.applied, "source": self.source, "note": self.note}


# ------------------------------------------------------------------ 规则库

def _ensure_import(code: str, module: str) -> str:
    if re.search(rf"^\s*import\s+{module}\b", code, re.M):
        return code
    lines = code.splitlines()
    last_import = 0
    for i, line in enumerate(lines):
        if line.startswith("import ") or line.startswith("from "):
            last_import = i
    lines.insert(last_import + 1, f"import {module}")
    return "\n".join(lines)


def _rule_missing_import(code: str, error: str) -> tuple[str, str] | None:
    m = re.search(r"NameError: name '(\w+)' is not defined", error)
    if not m:
        return None
    name = m.group(1)
    if name in IMPORTABLE:
        return _ensure_import(code, name), f"补齐缺失的 import {name}"
    m2 = re.search(r"ModuleNotFoundError: No module named '([\w.]+)'", error)
    if m2:
        root = m2.group(1).split(".")[0]
        if root in IMPORTABLE:
            return _ensure_import(code, root), f"补齐缺失的 import {root}"
    return None


def _rule_typo_identifier(code: str, error: str) -> tuple[str, str] | None:
    """NameError 的标识符与脚本内已知符号高度相似 → 判定为笔误并替换。"""
    m = re.search(r"NameError: name '(\w+)' is not defined", error)
    if not m:
        return None
    bad = m.group(1)
    if bad in IMPORTABLE:
        return None
    defined = set(re.findall(r"def\s+(\w+)", code)) | set(
        re.findall(r"^\s*(\w+)\s*=\s*", code, re.M)
    )
    candidates = sorted((defined | set(KNOWN_SYMBOLS)) - {bad})  # 必须排除自身，否则会"修"成自己
    match = difflib.get_close_matches(bad, candidates, n=1, cutoff=0.8)
    if not match:
        return None
    good = match[0]
    fixed = re.sub(rf"\b{re.escape(bad)}\b", good, code)
    if fixed == code:
        return None
    return fixed, f"将疑似笔误标识符 {bad} 修正为 {good}"


def _rule_module_missing(code: str, error: str) -> tuple[str, str] | None:
    m = re.search(r"ModuleNotFoundError: No module named '([\w.]+)'", error)
    if not m:
        return None
    root = m.group(1).split(".")[0]
    if root in IMPORTABLE:
        return _ensure_import(code, root), f"补齐缺失的 import {root}"
    return None


RULES: list[tuple[str, Callable[[str, str], tuple[str, str] | None]]] = [
    ("missing_import", _rule_missing_import),
    ("module_missing", _rule_module_missing),
    ("typo_identifier", _rule_typo_identifier),
]


def heal(code: str, error: str) -> HealResult:
    for name, rule in RULES:
        try:
            fixed = rule(code, error)
        except (re.error, TypeError, ValueError):
            fixed = None
        if fixed:
            new_code, note = fixed
            if new_code != code:
                return HealResult(True, new_code, f"rule:{name}", note)

    llm = get_llm()
    if not llm.enabled:
        return HealResult(False, code, "none", "规则库未命中，且当前为离线模式，无法语义修复")

    text = llm.text(prompts.SYSTEM_SCRIPT_HEALER,
                    f"## 报错\n```\n{error[:2000]}\n```\n\n## 脚本\n```python\n{code}\n```")
    if not text:
        return HealResult(False, code, "none", "大模型修复失败（网络或配额问题）")
    cleaned = re.sub(r"^```(?:python)?\s*|\s*```$", "", text.strip(), flags=re.S)
    if "def test_" not in cleaned:
        return HealResult(False, code, "none", "大模型返回内容不像完整脚本，已丢弃")
    return HealResult(True, cleaned, "llm", "大模型语义修复")
