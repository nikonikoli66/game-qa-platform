"""声明式用例 → pytest 脚本的确定性渲染器。

这是整个方案最关键的架构决策：
    ❌ 让大模型直接写 pytest 代码 —— 语法错、import 漏、断言写反，自愈循环等于在赌概率；
    ✅ 让大模型只产出「声明式用例 JSON」，代码由本渲染器用固定模板拼装。

结果：AI 的不可控范围被压在"数据层"（参数、期望值），代码层 100% 确定、可评审、可回归。
断言逻辑收敛到模板里的 `_check` DSL，用例只需要填 `{"data.items": "len:10"}` 这种声明。
"""
from __future__ import annotations

import json
import re
from typing import Any

from app import config

HELPER_SOURCE = '''
# ---------------------------------------------------------------- 框架层（自动生成，勿改）
BASE = os.environ.get("GAME_API_BASE", "http://127.0.0.1:8000").rstrip("/")
ACCOUNT = os.environ.get("QA_ACCOUNT", "qa_auto")
HTTP_TIMEOUT = float(os.environ.get("QA_HTTP_TIMEOUT", "10"))
ASYNC_SLA = float(os.environ.get("ASYNC_REWARD_SLA", "8"))

_VARS = {}
_TOKEN = {"value": ""}


def _resolve(obj, path):
    """支持 data.items[0].rarity 与 data.items.0.rarity 两种写法（模型两种都会写）。"""
    cur = obj
    for part in path.split("."):
        if part.endswith("]") and "[" in part:
            name, _, idx = part[:-1].partition("[")
            if name:
                cur = cur[name]
            cur = cur[int(idx)]
        elif isinstance(cur, list) and part.isdigit():
            cur = cur[int(part)]
        else:
            cur = cur[part]
    return cur


def _compare(actual, expected, path, snap):
    if isinstance(expected, str):
        if expected == "not_empty":
            assert actual, "%s 期望非空，实际为空" % path
            return
        if ":" in expected:
            op, arg = expected.split(":", 1)
            if op == "len":
                assert len(actual) == int(arg), "%s 长度期望 %s，实际 %s" % (path, arg, len(actual))
                return
            if op == "delta":
                key = path.split(".")[-1]
                assert key in snap, "%s 使用 delta 断言但缺少快照字段 %s" % (path, key)
                diff = round(float(actual) - float(snap[key]), 4)
                assert abs(diff - float(arg)) < 1e-6, "%s 期望变化 %s，实际变化 %s" % (path, arg, diff)
                return
            if op in ("ge", "le", "gt", "lt"):
                a, b = float(actual), float(arg)
                ok = {"ge": a >= b, "le": a <= b, "gt": a > b, "lt": a < b}[op]
                assert ok, "%s 期望 %s:%s，实际 %s" % (path, expected, arg, actual)
                return
            if op == "ne":
                assert str(actual) != arg, "%s 期望不等于 %s" % (path, arg)
                return
    assert actual == expected, "%s 期望 %r，实际 %r" % (path, expected, actual)


def _check(body, spec, snap=None):
    snap = snap or {}
    for path, expected in spec.items():
        _check_one(body, path, expected, snap)


def _check_one(body, path, expected, snap):
    try:
        actual = _resolve(body, path)
    except (KeyError, IndexError, TypeError) as exc:
        # 把"响应里到底有什么"打出来，否则 KeyError 只有一行、根本没法排查
        raise AssertionError(
            "断言路径 %s 在响应中不存在（%s）；响应实际内容：%s"
            % (path, type(exc).__name__, json.dumps(body, ensure_ascii=False)[:400])
        )
    _compare(actual, expected, path, snap)


def _call(method, path, payload=None):
    url = BASE + path
    headers = {"Content-Type": "application/json"}
    if _TOKEN["value"]:
        headers["Authorization"] = "Bearer " + _TOKEN["value"]
    return requests.request(method, url, json=payload, headers=headers, timeout=HTTP_TIMEOUT)


def _capture(body):
    data = body.get("data") if isinstance(body, dict) else None
    if isinstance(data, dict):
        for key in ("reward_id", "battle_id", "token"):
            if data.get(key):
                _VARS[key] = data[key]


def _login():
    resp = _call("POST", "/api/login", {"account": ACCOUNT})
    body = resp.json()
    assert body.get("code") == 0, "登录失败: %s" % body
    _TOKEN["value"] = body["data"]["token"]
    _VARS["account"] = ACCOUNT
    return body


def _snapshot(fields):
    if not fields:
        return {}
    body = _call("GET", "/api/bag").json()
    data = body.get("data") or {}
    return {f: data.get(f) for f in fields}


def _wait_until(fetch, spec, timeout=None, interval=0.4):
    """异步轮询断言：适配 MQ/定时任务延迟到账，解决"同步断言必然误报"。"""
    timeout = float(timeout or ASYNC_SLA * 2)
    deadline = time.time() + timeout
    last_err = None
    last_body = None
    while time.time() < deadline:
        last_body = fetch()
        try:
            _check(last_body, spec)
            return last_body
        except AssertionError as exc:
            last_err = exc
        time.sleep(interval)
    raise AssertionError(
        "异步断言在 %.1fs 内未满足（疑似 SLA 超时）: %s | 最后一次响应: %s"
        % (timeout, last_err, json.dumps(last_body, ensure_ascii=False))
    )


def _post(path, payload):
    return _call("POST", path, payload)
'''


def _render_value(value: Any) -> str:
    """把用例里的参数值渲染成 Python 表达式，支持 $变量 引用。"""
    if isinstance(value, str) and value.startswith("$"):
        name = value[1:]
        if name == "account":
            return "ACCOUNT"
        return f'_VARS.get("{name}")'
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "True" if value else "False"
    if value is None:
        return "None"
    return repr(value)


def _render_payload(payload: dict[str, Any] | None) -> str:
    if not payload:
        return "{}"
    inner = ", ".join(f'"{k}": {_render_value(v)}' for k, v in payload.items())
    return "{" + inner + "}"


def _render_path(path: str) -> str:
    if "{" not in path:
        return json.dumps(path, ensure_ascii=False)
    elems: list[str] = []
    pos = 0
    for m in re.finditer(r"\{(\w+)\}", path):
        if m.start() > pos:
            elems.append(json.dumps(path[pos:m.start()], ensure_ascii=False))
        elems.append(f'str(_VARS.get("{m.group(1)}", ""))')
        pos = m.end()
    if pos < len(path):
        elems.append(json.dumps(path[pos:], ensure_ascii=False))
    return " + ".join(elems)


def _render_expect(expect: dict[str, Any] | None) -> str:
    if not expect:
        return "{}"
    inner = ", ".join(f'{json.dumps(k, ensure_ascii=False)}: {_render_value(v)}'
                      for k, v in expect.items())
    return "{" + inner + "}"


def render_script(case: dict[str, Any], case_id: str, index: int = 1) -> str:
    title = case.get("title", "未命名用例")
    lines: list[str] = [
        '"""自动生成脚本 —— 请勿手工修改，改用例后重新生成。',
        "",
        f"用例ID: {case_id}",
        f"用例: {title}",
        f"分类: {case.get('category', '')} / 优先级 {case.get('priority', '')}",
        f"期望: {case.get('expected', '')}",
        '"""',
        "import json",
        "import os",
        "import time",
        "",
        "import requests",
        HELPER_SOURCE.rstrip(),
        "",
        "",
        f"def test_case_{index:02d}():",
        f'    """{title}"""',
        "    _login()",
    ]

    for step_no, step in enumerate(case.get("steps", []), 1):
        method, _, path = step["action"].partition(" ")
        path_expr = _render_path(path or "/")
        payload_expr = _render_payload(step.get("payload"))
        expect_expr = _render_expect(step.get("expect"))
        snap = step.get("snapshot") or []

        lines.append("")
        lines.append(f"    # ---- step {step_no}: {step['action']}")
        if snap:
            lines.append(f"    snap = _snapshot({json.dumps(snap, ensure_ascii=False)})")

        if step.get("wait_async"):
            lines.append("    body = _wait_until(")
            lines.append(f'        lambda: _call("{method}", {path_expr}, {payload_expr}).json(),')
            lines.append(f"        {expect_expr},")
            lines.append("    )")
            lines.append("    _capture(body)")
            continue

        lines.append(f'    resp = _call("{method}", {path_expr}, {payload_expr})')
        lines.append(
            '    assert resp.status_code == 200, "HTTP %s: %s" % (resp.status_code, resp.text[:500])'
        )
        lines.append("    body = resp.json()")
        lines.append("    _capture(body)")
        if snap:
            lines.append(f"    _check(body, {expect_expr}, snap)")
        else:
            lines.append(f"    _check(body, {expect_expr})")

    lines.append("")
    return "\n".join(lines)


def script_filename(case_id: str, index: int = 1, tag: str = "") -> str:
    safe = re.sub(r"[^A-Za-z0-9_]", "", case_id)[:20] or "case"
    suffix = f"_{re.sub(r'[^A-Za-z0-9_]', '', tag)[-8:]}" if tag else ""
    return f"test_{index:02d}_{safe}{suffix}.py"


def write_script(case: dict[str, Any], case_id: str, index: int = 1, tag: str = "") -> str:
    """tag 用于并发场景：同一条用例被不同批次同时执行时，避免写同一个脚本文件互相覆盖。"""
    path = config.SCRIPTS_DIR / script_filename(case_id, index, tag)
    path.write_text(render_script(case, case_id, index), encoding="utf-8")
    return str(path)
