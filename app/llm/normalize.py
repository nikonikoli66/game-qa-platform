"""大模型输出的类型归一化。

真实接入后立刻踩到的坑：同样是「重现步骤」这个字段，
离线模板返回的是字符串，而 qwen-plus 返回的是 JSON 数组 —— 直接写库就炸：
    sqlite3.ProgrammingError: Error binding parameter 8: type 'list' is not supported

结论：**永远不要相信模型给的字段类型**，提示词里写了 string 也不算数。
所有 LLM 输出在入库 / 展示之前，必须先过一遍这里。
"""
from __future__ import annotations

import json
from typing import Any


def as_text(value: Any) -> str:
    """把任意模型输出压成可读字符串，尽量保留信息而不是直接 str() 出 Python 字面量。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        # {"step": 1, "action": "..."} → "step=1 action=..."
        return " ".join(f"{k}={as_text(v)}" for k, v in value.items() if as_text(v))
    if isinstance(value, (list, tuple)):
        lines: list[str] = []
        for index, item in enumerate(value, 1):
            text = as_text(item)
            if not text:
                continue
            # 模型常常已经自带 "1. " 前缀，别重复加
            lines.append(text if text[:1].isdigit() else f"{index}. {text}")
        return "\n".join(lines)
    return str(value)


def as_short_text(value: Any, limit: int | None = None) -> str:
    text = as_text(value)
    if limit and len(text) > limit:
        return text[:limit] + "…"
    return text


def as_json_safe(value: Any) -> Any:
    """把 dict/list 里的非 JSON 类型也一并转成字符串，便于存进 TEXT 列。"""
    if isinstance(value, dict):
        return {str(k): as_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [as_json_safe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def dumps(value: Any) -> str:
    return json.dumps(as_json_safe(value), ensure_ascii=False)
