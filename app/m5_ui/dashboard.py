"""Streamlit 看板 —— QA 的统一操作入口。

原则：UI 只做展示与交互，所有业务逻辑都在 pipeline / 各模块里，
保证"页面能做的事，命令行和 CI 也能做"（不把逻辑写进 UI 是这类项目最容易烂的地方）。
"""
from __future__ import annotations

import json
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app import config, pipeline, repository
from app.api_catalog import catalog_table
from app.llm.client import usage_stats
from app.m1_cases import hallucination
from app.m2_automation import renderer
from app.m3_defect.classifier import KIND_LABEL

PRIMARY = "#2563eb"
DANGER = "#dc2626"
WARN = "#d97706"
OK = "#059669"
GRID = "#e5e7eb"

st.set_page_config(page_title="AI 游戏测试平台", page_icon="🎮", layout="wide")


# ------------------------------------------------------------------ 通用组件
def _stat_row(items: list[tuple[str, str, str]]) -> None:
    cols = st.columns(len(items))
    for col, (label, value, help_text) in zip(cols, items):
        col.metric(label, value, help=help_text)


def _layout(fig: go.Figure, height: int = 340) -> go.Figure:
    fig.update_layout(
        height=height,
        margin=dict(l=10, r=10, t=30, b=10),
        paper_bgcolor="white",
        plot_bgcolor="white",
        font=dict(size=12, color="#111827"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    )
    fig.update_xaxes(gridcolor=GRID, zeroline=False)
    fig.update_yaxes(gridcolor=GRID, zeroline=False)
    return fig


def _status_badge(kind: str) -> str:
    return {
        "PASS": "✅ 通过",
        "BUSINESS_BUG": "🐞 业务缺陷",
        "SCRIPT_ERROR": "🛠 脚本问题",
        "ENV_ISSUE": "🌫 环境/脏数据",
        "ASYNC_TIMEOUT": "⏳ 异步超时",
        "SERVER_ERROR": "💥 服务端异常",
        "TEST_DATA_ISSUE": "📝 用例数据问题",
        "UNKNOWN": "❓ 待确认",
    }.get(kind, kind)


# ------------------------------------------------------------------ 侧边栏
def sidebar() -> None:
    with st.sidebar:
        st.title("🎮 AI 测试平台")
        st.caption("AI 辅助游戏玩法自动化校验 & 体验分析")

        alive = pipeline.ensure_server()
        st.markdown(
            f"**被测服** `{config.GAME_API_BASE}` "
            + ("🟢 在线" if alive else "🔴 离线（请先启动 mock_server）")
        )
        st.markdown(f"**AI 引擎** `{config.llm_mode_label()}`")
        st.markdown(f"**存储** `{config.DB_URL.split('://')[0]}`")

        st.divider()
        st.markdown("**一键操作**")
        if st.button("🚀 一键跑通全流程", width="stretch",
                     help="生成用例 → 批量执行 → 采集体验数据 → 出报告"):
            progress = st.progress(0.0, text="准备中…")

            def cb(i: int, total: int, title: str) -> None:
                progress.progress(min(0.95, (i - 1) / max(total, 1)), text=f"[{i}/{total}] {title}")

            with st.spinner("全流程执行中，约 20~40 秒…"):
                st.session_state["full"] = pipeline.full_demo(progress=cb)
            progress.progress(1.0, text="完成")
            st.success("全流程完成，右侧各 Tab 已刷新")

        if st.button("♻️ 重置所有数据", width="stretch"):
            pipeline.reset_all()
            st.session_state.clear()
            st.warning("已重置")

        st.divider()
        stats = usage_stats()
        st.caption(
            f"模型调用 {stats['calls']} 次 / 失败 {stats['failures']} 次 / "
            f"tokens {stats['tokens']}"
        )
        if stats["last_error"]:
            st.caption(f"最后一次错误：{stats['last_error'][:80]}")
        with st.expander("接口契约（唯一事实源）"):
            st.dataframe(pd.DataFrame(catalog_table()), width="stretch", hide_index=True)


# ------------------------------------------------------------------ Tab1 用例中心
def tab_cases() -> None:
    st.subheader("① AI 需求解析 & 用例生成")
    st.caption(
        "AI 只产出**声明式用例 JSON**，再由契约校验器过滤幻觉；"
        "不合格的用例不入库、不生成脚本，并把原因回灌给模型重写。"
    )

    prd_options = {name: filename for name, filename in pipeline.PRD_FILES}
    col1, col2 = st.columns([1, 2])
    with col1:
        picked = st.selectbox("示例需求（可直接改）", list(prd_options.keys()))
        prd_path = config.REQUIREMENT_DIR / prd_options[picked]
        default_text = prd_path.read_text(encoding="utf-8") if prd_path.exists() else ""
        count = st.slider("期望用例数量", 4, 20, 12)
        gen_btn = st.button("✨ 生成并质检", type="primary", width="stretch")
    with col2:
        req_text = st.text_area("需求文档片段（PRD）", value=default_text, height=260,
                                key="prd_text")

    if gen_btn:
        with st.spinner("解析需求 → 生成用例 → 契约校验…"):
            from app.m1_cases import generator

            outcome = generator.generate(picked, req_text, count=count, persist=True)
            st.session_state["gen"] = outcome

    outcome = st.session_state.get("gen")
    if outcome is None:
        st.info("点击左侧「生成并质检」开始；或先去侧边栏点「一键跑通全流程」。")
        return

    stats = outcome.report.as_dict()
    _stat_row([
        ("用例总数", str(stats["total"]), "AI 原始产出条数"),
        ("质检通过", str(stats["valid"]), "进入用例库的条数"),
        ("幻觉/低质拦截", str(stats["rejected"]), "被规则挡在用例层的条数"),
        ("有效率", f"{stats['valid_rate']}%", "通过数 / 总数"),
        ("平均质量分", f"{stats['avg_score']}", "由 8 条规则加权扣分"),
    ])
    for note in outcome.notes:
        st.caption(f"· {note}")

    st.markdown("#### 用例清单")
    rows = []
    for d in outcome.report.details:
        rows.append({
            "状态": "✅ 通过" if d["status"] == "valid" else "⛔ 拦截",
            "分类": d["category"],
            "标题": d["title"],
            "质量分": d["score"],
            "问题": " / ".join(i["message"] for i in d["issues"])[:200],
        })
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True,
                 height=min(420, 40 + 32 * len(rows)))

    with st.expander("🔍 幻觉拦截详情（为什么被拦）", expanded=stats["rejected"] > 0):
        if not outcome.report.rejected:
            st.success("本轮没有用例被拦截。")
        for case in outcome.report.rejected:
            st.markdown(f"**{case.get('title')}**")
            for msg in outcome.report.reject_reasons.get(case.get("title"), []):
                st.markdown(f"- {msg}")

    st.markdown("#### 🛡 质检规则自检（注入 7 条典型 AI 错误用例）")
    st.caption("离线引擎产出的用例天然干净，看不出质检有没有用；这里注入脏数据当场验证拦截能力。")
    if st.button("运行拦截自检"):
        st.session_state["halluc"] = hallucination.summary()
    audit = st.session_state.get("halluc")
    if audit:
        if audit["all_caught"]:
            st.success(f"7/7 全部拦截（拦截率 {audit['rate']}%）：规则确实生效，不是摆设。")
        else:
            st.error(f"拦截率 {audit['caught']}/{audit['total']}，存在漏过项，需补规则。")
        st.dataframe(
            pd.DataFrame([{
                "注入的 AI 错误": r["注入的幻觉"],
                "期望命中规则": r["期望命中"],
                "实际命中": r["实际命中"],
                "结果": r["结果"],
                "拦截原因": r["详情"][:120],
            } for r in audit["rows"]]),
            width="stretch", hide_index=True,
        )

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("#### 风险覆盖矩阵")
        if outcome.risk_matrix:
            st.dataframe(
                pd.DataFrame([{
                    "风险点": r["risk"],
                    "覆盖用例数": r["covered_cases"],
                    "风险等级": r["level"],
                    "建议补法": r["how"],
                } for r in outcome.risk_matrix]),
                width="stretch", hide_index=True,
            )
        else:
            st.caption("当前需求未识别到显著风险点。")
    with col_b:
        st.markdown("#### 覆盖度")
        cov = outcome.report.coverage
        cat = cov.get("by_category", {})
        if cat:
            fig = go.Figure(go.Bar(
                x=list(cat.keys()), y=list(cat.values()),
                marker_color=PRIMARY, text=list(cat.values()), textposition="outside",
            ))
            st.plotly_chart(_layout(fig, 220), width="stretch")
        st.caption(
            f"覆盖接口 {len(cov.get('endpoints', []))} 个 / 风险标签 "
            f"{cov.get('risk_coverage', 0)} 个 / 缺失分类 {cov.get('missing_categories') or '无'}"
        )

    st.markdown("#### ✏️ 人工编辑（QA 可直接改 AI 生成的用例）")
    cases = repository.list_cases(status="valid")
    if not cases:
        return
    labels = {f"{c['title']}  ·  {c['category']}  ·  {c['score']:.0f}分": c for c in cases}
    picked_label = st.selectbox("选择用例", list(labels.keys()))
    case = labels[picked_label]
    e1, e2 = st.columns(2)
    with e1:
        title = st.text_input("标题", value=case["title"])
        precondition = st.text_area("前置条件", value=case.get("precondition", ""), height=80)
        expected = st.text_area("期望结果", value=case.get("expected", ""), height=80)
    with e2:
        steps_json = st.text_area("步骤（JSON，可直接改参数/断言）",
                                  value=json.dumps(case["steps"], ensure_ascii=False, indent=2),
                                  height=250)
    if st.button("💾 保存修改"):
        try:
            steps = json.loads(steps_json)
            from app.m1_cases import validator
            from app.m1_cases.schema import normalize_case

            draft = normalize_case({**case, "title": title, "precondition": precondition,
                                    "expected": expected, "steps": steps})
            result = validator.validate_case(draft)
            if not result.ok:
                st.error("修改后未通过质检：" + "；".join(i.message for i in result.issues))
            else:
                repository.update_case(case["id"], title=title, precondition=precondition,
                                       expected=expected, steps=draft["steps"],
                                       score=result.score)
                st.success(f"已保存（质检分 {result.score:.0f}）")
                st.rerun()
        except json.JSONDecodeError as exc:
            st.error(f"步骤 JSON 解析失败：{exc}")


# ------------------------------------------------------------------ Tab2 脚本与执行
def tab_scripts() -> None:
    st.subheader("② 自动化脚本渲染 & 执行")
    st.caption(
        "脚本由**确定性模板**渲染（AI 只填数据），执行前过 AST 安全预检，"
        "失败先做三分类——只有「脚本自身问题」才允许自愈，业务缺陷一律开单。"
    )
    cases = repository.list_cases(status="valid")
    if not cases:
        st.info("还没有可用用例，请先在「AI 用例中心」生成。")
        return

    table = pd.DataFrame([{
        "选择": False,
        "标题": c["title"],
        "分类": c["category"],
        "优先级": c["priority"],
        "步骤数": len(c["steps"]),
        "质量分": c["score"],
    } for c in cases])
    edited = st.data_editor(table, width="stretch", hide_index=True,
                            column_config={"选择": st.column_config.CheckboxColumn(width="small")},
                            key="case_picker")
    selected_titles = edited[edited["选择"]]["标题"].tolist()
    target = [c for c in cases if c["title"] in selected_titles] or cases

    c1, c2, c3 = st.columns([1, 1, 2])
    with c1:
        run_btn = st.button(f"▶️ 执行 {len(target)} 条", type="primary", width="stretch")
    with c2:
        preview_btn = st.button("👀 预览脚本", width="stretch")
    with c3:
        st.caption(f"将执行：{'、'.join(c['title'] for c in target[:3])}"
                   + ("…" if len(target) > 3 else ""))

    if preview_btn:
        pick = st.selectbox("预览哪条", [c["title"] for c in target], key="preview_pick")
        case = next(c for c in target if c["title"] == pick)
        st.code(renderer.render_script(case, case["id"], index=1), language="python")

    if run_btn:
        progress = st.progress(0.0, text="准备执行…")

        def cb(i: int, total: int, title: str) -> None:
            progress.progress(min(0.98, (i - 1) / max(total, 1)), text=f"[{i}/{total}] {title}")

        with st.spinner("执行中…"):
            result = pipeline.run_batch([c["id"] for c in target], progress=cb)
        progress.progress(1.0, text="完成")
        st.session_state["batch"] = result

    batch = st.session_state.get("batch")
    if not batch:
        st.info("选择用例后点击「执行」，或先在侧边栏跑全流程。")
        return

    _stat_row([
        ("执行条数", str(batch["total"]), "本次批次用例数"),
        ("通过", str(batch["passed"]), "全部断言通过"),
        ("失败", str(batch["failed"]), "含业务缺陷/环境/脚本问题"),
        ("通过率", f"{batch['pass_rate']}%", "通过 / 总数"),
        ("生成缺陷单", str(len(batch["defects"])), "仅业务缺陷/服务端异常会开单"),
    ])

    df = pd.DataFrame([{
        "状态": _status_badge(r["failure_kind"]),
        "用例": r["case_title"],
        "耗时(ms)": r["duration_ms"],
        "执行次数": r["attempts"],
        "失败分类": KIND_LABEL.get(r["failure_kind"], r["failure_kind"]),
        "判定依据": (r["reason"] or "")[:120],
    } for r in batch["results"]])
    st.dataframe(df, width="stretch", hide_index=True)

    healed = [r for r in batch["results"] if r["heal_notes"]]
    if healed:
        with st.expander(f"🛠 自愈记录（{len(healed)} 条）", expanded=False):
            for r in healed:
                st.markdown(f"**{r['case_title']}**：尝试 {r['attempts']} 次")
                for note in r["heal_notes"]:
                    st.markdown(f"- {note}")

    with st.expander("📜 查看执行日志"):
        pick = st.selectbox("用例", [r["case_title"] for r in batch["results"]], key="log_pick")
        row = next(r for r in batch["results"] if r["case_title"] == pick)
        run_row = repository.list_runs(batch["batch_id"])
        match = next((x for x in run_row if x["case_title"] == pick), None)
        st.code(match["log_excerpt"] if match else "无日志", language="text")

    st.divider()
    st.markdown("#### 🧪 自愈闭环演示")
    st.caption("注入两个典型脚本故障（标识符笔误 + 漏 import），验证「只修脚本、不掩盖缺陷」。")
    if st.button("运行自愈演示"):
        with st.spinner("注入故障 → 执行 → 归类 → 自愈 → 重跑…"):
            demo = pipeline.demo_self_heal()
        st.session_state["heal"] = demo
    demo = st.session_state.get("heal")
    if demo:
        for s in demo["steps"]:
            (st.success if s["ok"] else st.error)(f"{s['step']} → {s['detail']}")
        st.caption(f"共修复 {demo['heal_count']} 轮，最终结果："
                   f"{'通过 ✅' if demo['final_pass'] else '仍失败'}")


# ------------------------------------------------------------------ Tab3 缺陷看板
def tab_defects() -> None:
    st.subheader("③ 缺陷采集与根因辅助分析")
    st.caption("失败时自动采集请求参数、响应、账号状态与日志，再由 AI/模板产出可提交的缺陷单。")

    defects = repository.list_defects()
    if not defects:
        st.info("暂无缺陷。执行一批用例后，业务类失败会自动开单。")
        return

    col1, col2 = st.columns([1, 2])
    with col1:
        dist: dict[str, int] = {}
        for d in defects:
            dist[d["category"]] = dist.get(d["category"], 0) + 1
        fig = go.Figure(go.Bar(
            x=[KIND_LABEL.get(k, k) for k in dist],
            y=list(dist.values()),
            marker_color=[DANGER if k == "BUSINESS_BUG" else WARN for k in dist],
            text=list(dist.values()), textposition="outside",
        ))
        st.plotly_chart(_layout(fig, 260), width="stretch")
    with col2:
        sev: dict[str, int] = {}
        for d in defects:
            sev[d["severity"]] = sev.get(d["severity"], 0) + 1
        st.markdown("**缺陷列表**")
        st.dataframe(pd.DataFrame([{
            "严重级": d["severity"],
            "分类": KIND_LABEL.get(d["category"], d["category"]),
            "标题": d["title"][:60],
            "状态": d["status"],
        } for d in defects]), width="stretch", hide_index=True)

    st.markdown("#### 缺陷详情")
    labels = {f"{d['severity']} · {d['title'][:50]}": d for d in defects}
    picked = st.selectbox("选择缺陷", list(labels.keys()))
    d = labels[picked]
    m1, m2, m3 = st.columns(3)
    m1.metric("严重级", d["severity"])
    m2.metric("分类", KIND_LABEL.get(d["category"], d["category"]))
    m3.metric("状态", d["status"])

    st.markdown("**缺陷描述**")
    st.info(d["description"] or "（空）")
    st.markdown("**重现步骤**")
    st.code(d["repro_steps"] or "（空）", language="text")
    st.markdown("**根因猜想**")
    st.warning(d["root_cause"] or "（空）")
    with st.expander("📎 现场证据（自动采集）"):
        try:
            st.json(json.loads(d["evidence"]))
        except (json.JSONDecodeError, TypeError):
            st.code(d["evidence"] or "", language="text")

    st.divider()
    st.markdown("#### 版本验证：与上一批次对比")
    batches = repository.distinct_batches()
    if len(batches) < 2:
        st.caption("至少需要两个批次（跑两次用例）才能做回归对比。")
        return
    cur = batches[0]
    reg = pipeline.regression(cur)
    if not reg.get("available"):
        st.caption(reg.get("reason", ""))
        return
    st.caption(f"当前批次 `{cur}` ↔ 上一批次 `{reg['previous_batch']}`")
    c1, c2, c3 = st.columns(3)
    c1.metric("新增失败", len(reg["new_failures"]), delta=len(reg["new_failures"]), delta_color="inverse")
    c2.metric("已修复", len(reg["fixed"]))
    c3.metric("持续失败", len(reg["persistent"]))
    for label, items in (("🆕 新增失败", reg["new_failures"]),
                         ("✅ 本版修复", reg["fixed"]),
                         ("⚠️ 持续失败", reg["persistent"])):
        if items:
            st.markdown(f"**{label}**")
            for it in items:
                st.markdown(f"- {it['title']}")


# ------------------------------------------------------------------ Tab4 体验分析
def tab_experience() -> None:
    st.subheader("④ 玩家体验数据分析")
    st.caption("数据来自测试环境实测采集（进入关卡→结算→领奖），不是线上日志，也不是凭空造的随机数。")

    c1, c2 = st.columns([1, 3])
    with c1:
        players = st.slider("测试账号数", 8, 60, 24, step=4)
        if st.button("📥 采集数据", type="primary", width="stretch"):
            with st.spinner("采集关卡行为数据…"):
                info = pipeline.collect_telemetry(players=players)
                st.session_state["tel"] = info
                st.success(f"采集完成：{info['sessions']} 场会话（{info['mode']}）")
    with c2:
        info = st.session_state.get("tel")
        if info:
            st.caption(
                f"批次 {info['batch_id']} · {info['players']} 账号 · "
                f"{info['sessions']} 场会话 · 接口实跑 {info['real_api_runs']} 次 · {info['mode']}"
            )

    exp = pipeline.experience_report()
    if exp.get("empty"):
        st.info("还没有采集数据。点击左侧「采集数据」或侧边栏「一键跑通全流程」。")
        return

    _stat_row([
        ("样本会话", f"{exp['total_sessions']}", "关卡会话总数"),
        ("整体通关率", f"{exp['overall_clear_rate'] * 100:.1f}%", "所有关卡加权"),
        ("难度断崖", f"{len(exp['cliffs'])}", "相邻关卡通关率跌幅 ≥15pp"),
        ("高卡点关卡", f"{exp['hard_stage_count']}", "通关率 <45% 的关卡"),
    ])

    stages = pd.DataFrame(exp["stages"])
    fig = go.Figure()
    fig.add_bar(x=stages["stage_id"], y=(stages["clear_rate"] * 100).round(1),
                name="通关率(%)", marker_color=PRIMARY,
                text=(stages["clear_rate"] * 100).round(0), textposition="outside")
    fig.add_scatter(x=stages["stage_id"], y=stages["avg_deaths"], name="平均死亡次数",
                    yaxis="y2", mode="lines+markers", line=dict(color=WARN, width=2))
    fig.update_layout(yaxis=dict(title="通关率(%)", range=[0, 110]),
                      yaxis2=dict(title="平均死亡", overlaying="y", side="right", showgrid=False),
                      xaxis=dict(title="关卡", dtick=1))
    st.markdown("#### 难度曲线（通关率 vs 平均死亡）")
    st.plotly_chart(_layout(fig, 360), width="stretch")

    if exp["cliffs"]:
        st.markdown("#### ⚠️ 难度断崖")
        st.dataframe(pd.DataFrame([{
            "区间": f"{c['from_stage']} → {c['to_stage']}",
            "通关率变化": f"{c['from_rate'] * 100:.1f}% → {c['to_rate'] * 100:.1f}%",
            "降幅": f"{c['drop'] * 100:.1f}pp",
            "该关平均死亡": c["avg_deaths"],
            "失败平均进度": f"{c['avg_progress_at_fail'] * 100:.0f}%",
        } for c in exp["cliffs"]]), width="stretch", hide_index=True)

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("#### 失败卡点分布")
        hist = pd.DataFrame(exp["progress_hist"])
        if not hist.empty:
            f2 = go.Figure(go.Bar(x=hist["label"], y=hist["count"],
                                  marker_color=DANGER, text=hist["count"],
                                  textposition="outside"))
            f2.update_layout(xaxis_title="失败时的关卡进度", yaxis_title="失败次数")
            st.plotly_chart(_layout(f2, 300), width="stretch")
    with col2:
        st.markdown("#### 道具使用率")
        items = pd.DataFrame(exp["items"])
        if not items.empty:
            f3 = go.Figure(go.Bar(x=items["item_id"], y=(items["usage_rate"] * 100).round(1),
                                  marker_color=OK, text=(items["usage_rate"] * 100).round(0),
                                  textposition="outside"))
            f3.update_layout(yaxis_title="使用率(%)", xaxis_title="道具")
            st.plotly_chart(_layout(f3, 300), width="stretch")

    st.markdown("#### 📝 体验优化建议（每条都绑定数据证据）")
    st.caption(f"生成方式：{exp['advice'].get('source')}")
    st.success(exp["advice"].get("summary", ""))
    for s in exp["advice"].get("suggestions", []):
        st.markdown(f"**[{s.get('priority', 'P1')}]** {s.get('content', '')}")
        if s.get("evidence"):
            st.caption(f"数据证据：`{s['evidence']}`")

    st.download_button("⬇️ 下载体验分析报告 (Markdown)", exp["markdown"],
                       file_name="experience_report.md", mime="text/markdown")


# ------------------------------------------------------------------ 主入口
def main() -> None:
    sidebar()
    st.title("AI 辅助游戏玩法自动化校验 & 体验分析平台")
    st.caption(
        "需求解析 → 用例生成与质检 → 脚本渲染与自动执行 → 缺陷归因 → 玩法体验分析，"
        "一条流水线覆盖游戏 QA 的日常闭环。"
    )
    tabs = st.tabs(["🧩 AI 用例中心", "⚙️ 脚本与执行", "🐞 缺陷看板", "📈 玩法体验分析"])
    with tabs[0]:
        tab_cases()
    with tabs[1]:
        tab_scripts()
    with tabs[2]:
        tab_defects()
    with tabs[3]:
        tab_experience()


if __name__ == "__main__":
    main()
