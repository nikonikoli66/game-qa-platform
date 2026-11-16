"""从数据到"产品改进建议"。

纯指标没人看，策划要的是"改哪一关、改成什么、凭什么"。
所以这里强制每条建议都绑定数据证据（关卡号 + 指标数值），
AI 只负责把结构化结论转成人话，不允许它自由发挥编数字。
"""
from __future__ import annotations

from typing import Any

from app import config
from app.llm import offline, prompts
from app.llm.client import get_llm
from app.llm.normalize import as_short_text, as_text


def _metrics_block(report: dict[str, Any]) -> str:
    lines = [
        f"总样本：{report.get('total_sessions', 0)} 场会话 / {report.get('players', 0)} 个测试账号",
        f"整体通关率：{report.get('overall_clear_rate', 0) * 100:.1f}%",
        "",
        "| 关卡 | 场次 | 通关率 | 平均耗时(s) | 平均死亡 | 失败平均进度 |",
        "|---|---|---|---|---|---|",
    ]
    for s in report.get("stages", []):
        lines.append(
            f"| {s['stage_id']} | {s['sessions']} | {s['clear_rate'] * 100:.1f}% | "
            f"{s['avg_duration_s']} | {s['avg_deaths']} | {s['avg_progress_at_fail'] * 100:.0f}% |"
        )
    if report.get("cliffs"):
        lines.append("")
        lines.append("检测到的难度断崖：")
        for c in report["cliffs"]:
            lines.append(
                f"- 第 {c['from_stage']} 关 → 第 {c['to_stage']} 关，通关率 "
                f"{c['from_rate'] * 100:.1f}% → {c['to_rate'] * 100:.1f}%（下降 {c['drop'] * 100:.1f}pp）"
            )
    if report.get("items"):
        lines.append("")
        lines.append("道具使用率：")
        for i in report["items"]:
            lines.append(f"- {i['item_id']}：使用率 {i['usage_rate'] * 100:.1f}%，累计使用 {i['total_used']} 次")
    return "\n".join(lines)


def _normalize_advice(data: dict[str, Any], source: str) -> dict[str, Any]:
    """把模型输出压成 UI 能安全渲染的结构。

    真实模型的坑：findings / suggestions 里的元素可能是纯字符串而不是对象，
    UI 上直接 .get() 会崩；字段值也可能是数组。所以这里一律铺平。
    """
    findings: list[dict[str, Any]] = []
    for item in data.get("findings") or []:
        if isinstance(item, dict):
            findings.append({
                "stage": as_text(item.get("stage")),
                "metric": as_text(item.get("metric")),
                "value": as_text(item.get("value")),
                "impact": as_text(item.get("impact")),
            })
        else:
            findings.append({"stage": "-", "metric": "-", "value": as_text(item), "impact": ""})

    suggestions: list[dict[str, Any]] = []
    for item in data.get("suggestions") or []:
        if isinstance(item, dict):
            suggestions.append({
                "priority": as_short_text(item.get("priority"), 4) or "P1",
                "content": as_text(item.get("content")),
                "evidence": as_text(item.get("evidence")),
            })
        else:
            suggestions.append({"priority": "P1", "content": as_text(item), "evidence": ""})

    return {
        "summary": as_text(data.get("summary")),
        "findings": findings,
        "suggestions": suggestions,
        "source": source,
    }


def advise(report: dict[str, Any]) -> dict[str, Any]:
    if report.get("empty"):
        return {"summary": "暂无采集数据，请先在模块 4 执行一次采集。",
                "findings": [], "suggestions": [], "source": "none"}

    llm = get_llm()
    if llm.enabled:
        data = llm.chat_json(prompts.SYSTEM_STAGE_ADVISOR,
                             prompts.stage_advice_prompt(_metrics_block(report)))
        if data and (data.get("summary") or data.get("suggestions")):
            return _normalize_advice(data, f"llm:{config.LLM_PROVIDER}")

    result = offline.stage_advice(report)
    result["source"] = "offline"
    return result


def markdown_report(report: dict[str, Any], advice: dict[str, Any]) -> str:
    """生成可直接贴进周报的体验分析报告。"""
    parts = [
        "# 玩法体验数据分析报告",
        "",
        f"> 数据来源：测试环境实测采集（{report.get('players', 0)} 个测试账号，"
        f"{report.get('total_sessions', 0)} 场关卡会话）",
        "",
        "## 一、整体结论",
        "",
        advice.get("summary", ""),
        "",
        "## 二、关键发现",
        "",
    ]
    for f in advice.get("findings", []):
        parts.append(f"- 第 {f.get('stage')} 关 · {f.get('metric')} = {f.get('value')} — {f.get('impact', '')}")
    parts += ["", "## 三、优化建议", ""]
    for s in advice.get("suggestions", []):
        parts.append(f"- **[{s.get('priority', 'P1')}]** {s.get('content', '')}")
        if s.get("evidence"):
            parts.append(f"  - 数据证据：`{s['evidence']}`")
    if report.get("cliffs"):
        parts += ["", "## 四、难度断崖明细", "", "| 区间 | 变化 | 降幅 |", "|---|---|---|"]
        for c in report["cliffs"]:
            parts.append(
                f"| {c['from_stage']} → {c['to_stage']} | "
                f"{c['from_rate'] * 100:.1f}% → {c['to_rate'] * 100:.1f}% | "
                f"{c['drop'] * 100:.1f}pp |"
            )
    return "\n".join(parts)
