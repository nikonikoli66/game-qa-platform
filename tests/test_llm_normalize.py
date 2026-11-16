"""大模型输出归一化的单测。

这些用例全部来自真实踩坑：qwen-plus 把 repro_steps 返回成数组，
导致 sqlite3 绑参失败、整批任务中断。所以这里把"类型不可信"钉死。
"""
from __future__ import annotations

from app.llm.normalize import as_json_safe, as_short_text, as_text


def test_string_passthrough() -> None:
    assert as_text("  hello  ") == "hello"


def test_none_and_scalar() -> None:
    assert as_text(None) == ""
    assert as_text(3) == "3"
    assert as_text(True) == "是"


def test_list_of_strings_gets_numbered() -> None:
    text = as_text(["调用登录接口", "调用抽卡接口", "断言 code=0"])
    assert text.splitlines() == ["1. 调用登录接口", "2. 调用抽卡接口", "3. 断言 code=0"]


def test_list_keep_existing_numbering() -> None:
    """模型常常自带 "1. " 前缀，别加成 "1. 1. xxx"。"""
    text = as_text(["1. 登录", "2. 抽卡"])
    assert text == "1. 登录\n2. 抽卡"


def test_dict_becomes_kv_text() -> None:
    assert as_text({"step": 1, "action": "抽卡"}) == "step=1 action=抽卡"


def test_nested_list_of_dicts() -> None:
    text = as_text([{"action": "登录", "params": {"account": "qa"}}])
    assert "action=登录" in text and "account=qa" in text


def test_short_text_truncates() -> None:
    assert as_short_text("S2-需要立即修复", 2) == "S2…"
    assert as_short_text("S2", 8) == "S2"       # 未超长时不加省略号


def test_json_safe_deep() -> None:
    payload = {"a": [1, {"b": object()}], "c": ("x",)}
    safe = as_json_safe(payload)
    assert isinstance(safe["a"][1]["b"], str)
    assert isinstance(safe["c"], list)


def test_repository_guards_against_non_string(tmp_path=None) -> None:
    """数据层兜底：即使上游没归一化，也不该让 list 击穿 SQL 绑参。"""
    from app import repository

    defect_id = repository.save_defect(
        run_id="run_unit",
        case_id="case_unit",
        defect={
            "title": ["标题被模型返回成了数组"],
            "category": "BUSINESS_BUG",
            "severity": "S2",
            "description": "d",
            "repro_steps": ["1. 登录", "2. 抽卡"],
            "root_cause": "r",
            "evidence": "{}",
        },
    )
    assert defect_id.startswith("bug_")
    row = next(d for d in repository.list_defects(50) if d["id"] == defect_id)
    assert isinstance(row["title"], str)
    assert isinstance(row["repro_steps"], str)
    assert "抽卡" in row["repro_steps"]
