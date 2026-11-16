"""离线规则引擎 —— 没有大模型 Key 时的全功能兜底实现。

为什么要有它？
    1. 演示稳定性：断网 / 额度用尽时，平台必须照样能跑通全流程；
    2. 成本：用例生成、缺陷描述这类高频调用，规则能覆盖的部分没必要烧 token；
    3. 可回归：规则输出是确定的，可以写单元测试，比"模型今天心情好不好"可靠。

它是「兜底」而不是「替代」：真实 LLM 可用时优先走模型，模型失败自动降级到这里。
"""
from __future__ import annotations

from typing import Any

from app.m1_cases.parser import RequirementProfile
from app.m1_cases.schema import normalize_case

# ============================================================ 用例生成


def _c(**kwargs: Any) -> dict[str, Any]:
    return normalize_case(kwargs)


def _gacha_cases(p: RequirementProfile) -> list[dict[str, Any]]:
    cost = int(p.constraints["cost_gems"])
    mx = int(p.constraints["max_times"])
    pity = int(p.constraints["pity"])
    pool = "pool_standard"
    return [
        _c(
            title=f"单抽成功且精确扣钻{cost}",
            category="正向", priority="P0",
            precondition=f"账号已登录，钻石≥{cost}，卡池 {pool} 已开放",
            steps=[{
                "action": "POST /api/gacha/draw",
                "payload": {"pool_id": pool, "times": 1},
                "expect": {"code": 0, "data.items": "len:1", "data.gems": f"delta:-{cost}"},
                "snapshot": ["gems"],
            }],
            expected=f"code=0，data.items 长度=1，data.gems 相比抽卡前精确减少 {cost}",
            risk_tags=["资源扣减一致性", "保底计数与概率边界"],
        ),
        _c(
            title=f"十连抽产出{mx}件道具",
            category="正向", priority="P0",
            precondition=f"账号已登录，钻石≥{cost * mx}",
            steps=[{
                "action": "POST /api/gacha/draw",
                "payload": {"pool_id": pool, "times": mx},
                "expect": {"code": 0, "data.items": f"len:{mx}", "data.gems": f"delta:-{cost * mx}"},
                "snapshot": ["gems"],
            }],
            expected=f"code=0，data.items 长度={mx}，data.gems 减少 {cost * mx}",
            risk_tags=["资源扣减一致性"],
        ),
        _c(
            title="钻石不足时抽卡失败且不扣钻",
            category="边界", priority="P0",
            precondition=f"账号钻石被 GM 指令置为 {cost - 10}，不足一次抽卡",
            steps=[
                {"action": "POST /api/admin/grant",
                 "payload": {"account": "$account", "gems": cost - 10},
                 "expect": {"code": 0}},
                {"action": "POST /api/gacha/draw",
                 "payload": {"pool_id": pool, "times": 1},
                 "expect": {"code": 1003},
                 "snapshot": ["gems"]},
            ],
            expected=f"抽卡返回 code=1003，且 data.gems 仍为 {cost - 10}（不允许扣成负数）",
            risk_tags=["资源扣减一致性", "参数校验"],
        ),
        _c(
            title="抽卡次数为0参数非法",
            category="反向", priority="P1",
            precondition="账号已登录且资源充足",
            steps=[{
                "action": "POST /api/gacha/draw",
                "payload": {"pool_id": pool, "times": 0},
                "expect": {"code": 1001},
            }],
            expected="返回 code=1001（参数非法），不产生任何道具",
            risk_tags=["参数校验"],
        ),
        _c(
            title=f"抽卡次数超过上限{mx}被拦截",
            category="边界", priority="P1",
            precondition="账号已登录，钻石充足",
            steps=[{
                "action": "POST /api/gacha/draw",
                "payload": {"pool_id": pool, "times": mx + 1},
                "expect": {"code": 1001},
            }],
            expected=f"times={mx + 1} 返回 code=1001，服务端不接受越界次数",
            risk_tags=["参数校验"],
        ),
        _c(
            title="不存在的卡池返回对象不存在",
            category="反向", priority="P1",
            precondition="账号已登录，钻石充足",
            steps=[{
                "action": "POST /api/gacha/draw",
                "payload": {"pool_id": "pool_not_exist", "times": 1},
                "expect": {"code": 1004},
            }],
            expected="返回 code=1004，提示卡池不存在",
            risk_tags=["参数校验"],
        ),
        _c(
            title=f"保底计数{pity - 1}时下一抽必出SSR",
            category="边界", priority="P0",
            precondition=f"用 GM 指令把 {pool} 保底计数置为 {pity - 1}",
            steps=[
                {"action": "POST /api/admin/pity",
                 "payload": {"account": "$account", "pool_id": pool, "pity": pity - 1},
                 "expect": {"code": 0}},
                {"action": "POST /api/gacha/draw",
                 "payload": {"pool_id": pool, "times": 1},
                 "expect": {"code": 0, "data.items[0].rarity": "SSR"}},
            ],
            expected=f"保底计数达到 {pity} 时该抽必为 SSR，data.items[0].rarity=SSR",
            risk_tags=["保底计数与概率边界"],
        ),
        _c(
            title="背包满时抽卡产物转邮件发放",
            category="异常", priority="P1",
            precondition=f"用 GM 指令把背包填满至容量 {p.constraints['bag_cap']}",
            steps=[
                {"action": "POST /api/admin/bag_fill",
                 "payload": {"account": "$account", "count": int(p.constraints["bag_cap"])},
                 "expect": {"code": 0}},
                {"action": "POST /api/gacha/draw",
                 "payload": {"pool_id": pool, "times": 1},
                 "expect": {"code": 0, "data.mail_delivered": True}},
            ],
            expected="抽卡成功，data.mail_delivered=true，道具通过邮件补发不丢失",
            risk_tags=["背包溢出"],
        ),
    ]


def _stage_cases(p: RequirementProfile) -> list[dict[str, Any]]:
    stamina = int(p.constraints["stamina"])
    stage = 1
    enter = {"action": "POST /api/stage/enter", "payload": {"stage_id": stage},
             "expect": {"code": 0, "data.battle_id": "not_empty"}}
    settle_win = {"action": "POST /api/stage/settle", "payload": {"battle_id": "$battle_id", "win": True},
                  "expect": {"code": 0, "data.reward_id": "not_empty"}}
    poll = {"action": "GET /api/reward/{reward_id}", "payload": {"reward_id": "$reward_id"},
            "expect": {"code": 0, "data.status": "claimable"}, "wait_async": True}
    claim = {"action": "POST /api/reward/claim", "payload": {"reward_id": "$reward_id"},
             "expect": {"code": 0, "data.granted": "not_empty"}}
    return [
        _c(
            title="通关后异步奖励到账并可领取",
            category="正向", priority="P0",
            precondition=f"账号体力≥{stamina}，关卡 {stage} 已解锁",
            steps=[dict(enter), dict(settle_win), dict(poll), dict(claim)],
            expected="轮询在 SLA 内返回 claimable，领奖 code=0 且 data.granted 非空",
            risk_tags=["异步奖励到账", "状态机流转"],
        ),
        _c(
            title="战斗失败不发放通关奖励",
            category="正向", priority="P0",
            precondition="账号体力充足，关卡已解锁",
            steps=[
                dict(enter),
                {"action": "POST /api/stage/settle", "payload": {"battle_id": "$battle_id", "win": False},
                 "expect": {"code": 0, "data.reward_id": "not_empty"}},
                {"action": "GET /api/reward/{reward_id}", "payload": {"reward_id": "$reward_id"},
                 "expect": {"code": 0, "data.items": "len:0"}, "wait_async": True},
            ],
            expected="失败结算仍生成奖励单，但 data.items 为空（无通关奖励）",
            risk_tags=["状态机流转", "数据一致性"],
        ),
        _c(
            title="体力不足无法进入关卡",
            category="边界", priority="P1",
            precondition="GM 指令把体力置为 0",
            steps=[
                {"action": "POST /api/admin/grant",
                 "payload": {"account": "$account", "stamina": 0},
                 "expect": {"code": 0}},
                {"action": "POST /api/stage/enter", "payload": {"stage_id": stage},
                 "expect": {"code": 1003}},
            ],
            expected="code=1003，且 battle_id 不下发",
            risk_tags=["资源扣减一致性", "参数校验"],
        ),
        _c(
            title="非法关卡号返回参数错误",
            category="反向", priority="P2",
            precondition="账号已登录",
            steps=[{"action": "POST /api/stage/enter", "payload": {"stage_id": 0},
                    "expect": {"code": 1001}}],
            expected="stage_id=0 返回 code=1001",
            risk_tags=["参数校验"],
        ),
        _c(
            title="同一战斗重复结算被拒绝",
            category="异常", priority="P1",
            precondition="已完成一次胜利结算",
            steps=[
                dict(enter), dict(settle_win),
                {"action": "POST /api/stage/settle", "payload": {"battle_id": "$battle_id", "win": True},
                 "expect": {"code": 1005}},
            ],
            expected="重复结算同一 battle_id 返回 code=1005（状态冲突）",
            risk_tags=["状态机流转", "并发与幂等"],
        ),
        _c(
            title="奖励重复领取只发一次",
            category="异常", priority="P0",
            precondition="奖励单已到 claimable 状态",
            steps=[
                dict(enter), dict(settle_win), dict(poll),
                dict(claim),
                {"action": "POST /api/reward/claim", "payload": {"reward_id": "$reward_id"},
                 "expect": {"code": 1005}},
            ],
            expected="第二次领取返回 code=1005，且不重复发奖（幂等）",
            risk_tags=["并发与幂等", "数据一致性"],
        ),
        _c(
            title="连续三次进出关卡体力扣减一致",
            category="异常", priority="P1",
            precondition=f"账号体力≥{stamina * 3}",
            steps=[
                {"action": "POST /api/stage/enter", "payload": {"stage_id": stage},
                 "expect": {"code": 0}, "snapshot": ["stamina"]},
                {"action": "POST /api/stage/enter", "payload": {"stage_id": stage},
                 "expect": {"code": 0}, "snapshot": ["stamina"]},
                {"action": "POST /api/stage/enter", "payload": {"stage_id": stage},
                 "expect": {"code": 0, "data.stamina": f"delta:-{stamina}"},
                 "snapshot": ["stamina"]},
            ],
            expected=f"每次进入关卡体力减少 {stamina}，连续三次扣减一致",
            risk_tags=["资源扣减一致性"],
        ),
    ]


def _generic_cases(p: RequirementProfile) -> list[dict[str, Any]]:
    return [
        _c(
            title="服务健康检查可用",
            category="正向", priority="P2",
            precondition="测试环境已部署",
            steps=[{"action": "GET /api/health", "payload": {}, "expect": {"code": 0}}],
            expected="code=0，服务可用",
            risk_tags=["参数校验"],
        ),
        _c(
            title="登录返回有效token",
            category="正向", priority="P1",
            precondition="测试账号存在",
            steps=[{"action": "POST /api/login", "payload": {"account": "$account"},
                    "expect": {"code": 0, "data.token": "not_empty"}}],
            expected="code=0，data.token 非空",
            risk_tags=["参数校验"],
        ),
    ]


def generate_cases(profile: RequirementProfile, count: int = 10) -> list[dict[str, Any]]:
    if profile.domain == "卡池抽卡":
        cases = _gacha_cases(profile)
    elif profile.domain == "关卡战斗":
        cases = _stage_cases(profile)
    elif profile.domain == "背包道具":
        cases = _gacha_cases(profile)[:4] + _generic_cases(profile)
    else:
        cases = _generic_cases(profile)
    for c in cases:
        c["source"] = "offline"
    return cases[:count] if count else cases


# ============================================================ 缺陷分析

_SEVERITY_BY_KIND = {
    "BUSINESS_BUG": "S2",
    "SERVER_ERROR": "S1",
    "ASYNC_TIMEOUT": "S2",
    "ENV_ISSUE": "S4",
    "TEST_DATA_ISSUE": "S4",
    "SCRIPT_ERROR": "S4",
}

_KIND_TITLE = {
    "BUSINESS_BUG": "接口行为与需求不符",
    "SERVER_ERROR": "服务端异常",
    "ASYNC_TIMEOUT": "异步奖励未在 SLA 内到账",
    "ENV_ISSUE": "测试环境/脏数据导致的失败",
    "TEST_DATA_ISSUE": "用例数据不满足契约",
    "SCRIPT_ERROR": "测试脚本自身报错",
}


def analyze_defect(kind: str, case: dict[str, Any], log_excerpt: str,
                   evidence: str = "", account: str = "") -> dict[str, Any]:
    title = f"【{_KIND_TITLE.get(kind, kind)}】{case.get('title', '未命名用例')}"
    steps_desc = []
    for i, s in enumerate(case.get("steps", []), 1):
        payload = s.get("payload") or {}
        args = ", ".join(
            f"{k}={account if str(v) == '$account' and account else v}"
            for k, v in payload.items()
        )
        steps_desc.append(f"{i}. 调用 {s.get('action')} （{args or '无参数'}）")
    repro = "\n".join(steps_desc) + f"\n{len(steps_desc) + 1}. 观察实际返回与断言差异"

    root_cause_map = {
        "BUSINESS_BUG": "服务端逻辑与需求不符：断言期望值与接口实际返回不一致，且请求参数、账号状态均合法，判定为业务实现缺陷。",
        "SERVER_ERROR": "服务端 5xx，疑似代码异常或依赖不可用，需查服务端错误日志堆栈。",
        "ASYNC_TIMEOUT": "异步发奖链路（MQ/定时任务）未在 SLA 内完成投递，需检查消费者堆积与重试策略。",
        "ENV_ISSUE": "失败由环境或账号脏数据引起：登录态/资源配置/前置数据未就绪，非产品缺陷。",
        "TEST_DATA_ISSUE": "用例数据违反接口契约约束，属于用例质量问题，需回到用例层修正。",
        "SCRIPT_ERROR": "测试脚本自身异常（语法/名称/属性错误），与产品无关，可走自愈修复。",
    }

    return {
        "title": title,
        "severity": _SEVERITY_BY_KIND.get(kind, "S3"),
        "severity_reason": "依据失败分类与影响面自动评估",
        "description": (
            f"自动化用例「{case.get('title', '')}」执行失败。\n"
            f"期望：{case.get('expected', '')}\n"
            f"失败分类：{kind}\n"
            f"日志摘要：{log_excerpt.strip().splitlines()[-1] if log_excerpt.strip() else '无'}"
        ),
        "repro_steps": repro,
        "root_cause": root_cause_map.get(kind, "待人工确认"),
        "suggestion": "补充服务端日志与链路追踪后定位具体分支逻辑。",
    }


# ============================================================ 关卡体验诊断

def stage_advice(metrics: dict[str, Any]) -> dict[str, Any]:
    rows = metrics.get("stages", []) or []
    cliffs = metrics.get("cliffs", []) or []
    worst = sorted(rows, key=lambda r: r.get("clear_rate", 1.0))[:3]
    findings = [
        {
            "stage": r["stage_id"],
            "metric": "通关率",
            "value": f"{r['clear_rate'] * 100:.1f}%",
            "impact": f"平均死亡 {r['avg_deaths']:.1f} 次，平均耗时 {r['avg_duration_s']:.1f}s",
        }
        for r in worst
    ]
    suggestions = []
    for c in cliffs[:3]:
        suggestions.append({
            "priority": "P0",
            "content": (
                f"第 {c['from_stage']}→{c['to_stage']} 关通关率骤降 "
                f"{c['drop'] * 100:.1f} 个百分点，建议复核 {c['to_stage']} 关的怪物数值与"
                f"推荐战力配置，或将难度爬坡拆分为两级。"
            ),
            "evidence": f"clear_rate {c['from_rate'] * 100:.1f}% → {c['to_rate'] * 100:.1f}%",
        })
    if worst:
        w = worst[0]
        suggestions.append({
            "priority": "P1",
            "content": (
                f"第 {w['stage_id']} 关为全服最大卡点（通关率 {w['clear_rate'] * 100:.1f}%，"
                f"失败平均进度 {w['avg_progress_at_fail'] * 100:.0f}%），"
                f"建议在该进度点增加引导提示或降低该波怪血量。"
            ),
            "evidence": f"fail_progress={w['avg_progress_at_fail']:.2f}",
        })
    if metrics.get("item_low_usage"):
        item = metrics["item_low_usage"][0]
        suggestions.append({
            "priority": "P2",
            "content": (
                f"道具「{item['item_id']}」使用率仅 {item['usage_rate'] * 100:.1f}%，"
                f"疑似价值感知弱，建议在卡关提示中引导使用。"
            ),
            "evidence": f"usage_rate={item['usage_rate']:.2f}",
        })
    summary = (
        f"共采集 {metrics.get('total_sessions', 0)} 场关卡会话，"
        f"整体通关率 {metrics.get('overall_clear_rate', 0) * 100:.1f}%，"
        f"检测到 {len(cliffs)} 处难度断崖，最难关卡为第 {worst[0]['stage_id']} 关。"
        if worst else "采集数据不足，无法给出结论。"
    )
    return {"summary": summary, "findings": findings, "suggestions": suggestions}


def repair_script(code: str, error: str) -> str | None:
    """离线模式不做语义修复，交给 healer 的确定性规则库处理。"""
    return None
