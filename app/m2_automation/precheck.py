"""脚本静态安全预检（执行前最后一道闸门）。

即便脚本是模板渲染的，也一定要过这一关——因为一旦允许接入 AI 补写的代码片段，
就等于把任意代码执行权交给了模型。这里做三件事：
    1. 语法检查（ast.parse + compile），把语法错误挡在启动子进程之前（快、便宜）；
    2. 危险操作拦截：subprocess / os.system / eval / exec / 文件写入 / 网络外联；
    3. 结构检查：必须有 test_ 函数、必须有断言，避免"跑过了但什么都没验"。
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any

FORBIDDEN_CALLS = {
    "eval", "exec", "compile", "__import__", "open", "input",
    "os.system", "os.popen", "os.remove", "os.rmdir", "os.makedirs",
    "subprocess.run", "subprocess.Popen", "subprocess.call", "subprocess.check_output",
    "shutil.rmtree", "socket.socket", "pickle.loads",
}
FORBIDDEN_MODULES = {
    "subprocess", "shutil", "socket", "pickle", "ctypes", "importlib", "multiprocessing",
}
ALLOWED_IMPORTS = {
    "json", "os", "time", "re", "requests", "pytest", "math", "random",
    "datetime", "typing", "collections",
}
ALLOWED_HOSTS = ("127.0.0.1", "localhost")


@dataclass
class PrecheckResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "warnings": self.warnings,
            "checks": self.checks,
        }

    def labeled(self) -> str:
        return "；".join(self.errors) if self.errors else "通过"


def _full_name(node: ast.AST) -> str:
    parts = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return ".".join(reversed(parts))


def precheck(code: str, filename: str = "<script>") -> PrecheckResult:
    res = PrecheckResult(ok=True)

    # 1) 语法
    try:
        tree = ast.parse(code, filename=filename)
        compile(code, filename, "exec")
        res.checks.append({"name": "语法检查", "ok": True, "detail": "ast + compile 通过"})
    except SyntaxError as exc:
        res.ok = False
        res.errors.append(f"语法错误：第 {exc.lineno} 行 {exc.msg}")
        res.checks.append({"name": "语法检查", "ok": False, "detail": str(exc)})
        return res

    # 2) import 白名单 + 危险模块
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module.split(".")[0])
    bad_imports = sorted({m for m in imported if m in FORBIDDEN_MODULES})
    unknown_imports = sorted({m for m in imported if m not in ALLOWED_IMPORTS})
    if bad_imports:
        res.ok = False
        res.errors.append(f"引用了禁止的模块 {bad_imports}")
    if unknown_imports:
        res.warnings.append(f"引用了白名单外模块 {unknown_imports}，请确认无副作用")
    res.checks.append({
        "name": "依赖白名单", "ok": not bad_imports,
        "detail": f"imports={sorted(set(imported))}",
    })

    # 3) 危险调用
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _full_name(node.func)
            if name in FORBIDDEN_CALLS:
                hits.append(name)
    if hits:
        res.ok = False
        res.errors.append(f"包含危险调用 {sorted(set(hits))}，已阻止执行")
    res.checks.append({"name": "危险调用拦截", "ok": not hits,
                       "detail": f"命中={sorted(set(hits)) or '无'}"})

    # 4) 结构：必须有 test 函数 + 断言
    test_funcs = [n.name for n in tree.body
                  if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]
    assert_count = sum(1 for n in ast.walk(tree) if isinstance(n, ast.Assert))
    if not test_funcs:
        res.ok = False
        res.errors.append("未找到 test_ 开头的测试函数，pytest 不会执行任何用例")
    if assert_count == 0:
        res.ok = False
        res.errors.append("脚本中没有任何 assert，跑通也不能证明功能正确")
    res.checks.append({
        "name": "用例结构", "ok": bool(test_funcs) and assert_count > 0,
        "detail": f"test 函数 {len(test_funcs)} 个，assert {assert_count} 处",
    })

    # 5) 硬编码外网地址（防止脚本偷偷打真实环境）
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            if value.startswith(("http://", "https://")) and not any(
                host in value for host in ALLOWED_HOSTS
            ) and "{" not in value:
                res.warnings.append(f"发现疑似硬编码外部地址 {value!r}，请确认不是误打线上")

    return res


def precheck_file(path: str) -> PrecheckResult:
    from pathlib import Path

    try:
        code = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        return PrecheckResult(ok=False, errors=[f"脚本读取失败: {exc}"])
    return precheck(code, filename=path)
