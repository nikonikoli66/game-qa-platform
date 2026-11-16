"""提示词模板集中管理。

工程实践：提示词里必须把「输出契约」写死（JSON Schema + 接口白名单 + 枚举值），
否则模型自由发挥出来的字段名会让下游解析器全线崩溃。
"""
from __future__ import annotations

import json
from typing import Any

from app.api_catalog import catalog_table
from app.m1_cases.parser import RequirementProfile

CATEGORIES = ["正向", "反向", "边界", "异常", "性能", "安全"]
PRIORITIES = ["P0", "P1", "P2"]
RISK_TAGS = [
    "保底计数与概率边界", "异步奖励到账", "背包溢出", "并发与幂等", "资源扣减一致性",
    "状态机流转", "超时与SLA", "参数校验", "难度曲线与卡点", "数据一致性",
]

CASE_SCHEMA_HINT = {
    "cases": [
        {
            "title": "用例标题（15 字内，体现验证点）",
            "category": "正向|反向|边界|异常|性能|安全",
            "priority": "P0|P1|P2",
            "precondition": "前置条件（账号状态、资源、配置）",
            "steps": [
                {
                    "action": "METHOD /api/path",
                    "payload": {"字段": "值"},
                    "expect": {"code": 0, "data.items": "len:1"},
                    "snapshot": ["gems"],
                    "wait_async": False,
                }
            ],
            "expected": "期望结果，必须可观测（含字段名或数值）",
            "risk_tags": ["风险标签，取值见枚举"],
        }
    ]
}


def _catalog_block() -> str:
    rows = catalog_table()
    lines = ["| 接口 | 说明 | 必填 | 约束 | 异步 | 响应 data 字段 |", "|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(
            f"| {r['endpoint']} | {r['summary']} | {r['required']} | {r['constraints']} "
            f"| {r['async'] or '-'} | {r['response']} |"
        )
    return "\n".join(lines)


# 步骤约定与示例单独抽成普通字符串：塞进 f-string 里的话，JSON 的大括号会被当成表达式
STEP_CONVENTIONS = """
## steps 写法约定（违反会导致用例不可执行，必须严格遵守）

1. `action` 只能是白名单里的 `"METHOD /path"`。
2. **动态 ID 必须用变量引用，禁止编造字面量**：
   - `"$account"` → 当前测试账号
   - `"$battle_id"` → 由 `POST /api/stage/enter` 的响应产生
   - `"$reward_id"` → 由 `POST /api/stage/settle` 的响应产生
   需要 `battle_id` 的步骤之前必须先有 `enter` 步骤；需要 `reward_id` 之前必须先有 `settle` 步骤。
   注意：只有「故意测试不存在的对象」的反向用例，才允许写字面量 ID。
3. `delta` 断言必须与 `snapshot` 写在**同一个步骤**上——snapshot 采集的是"调用前"的值：
   {"action":"POST /api/gacha/draw","payload":{"pool_id":"pool_standard","times":1},
    "expect":{"code":0,"data.gems":"delta:-160"},"snapshot":["gems"]}
4. `wait_async: true` 只用于轮询奖励状态（`GET /api/reward/{reward_id}`），其余步骤为 false。

## 字段语义提示（真实踩过的坑，务必遵守）
- `POST /api/gacha/draw` 的 `data.items` 是"本次产出列表"：背包满时产物转邮件，`items` 里**依然有内容**，
  是否转邮件只看 `data.mail_delivered`。不要断言 `data.items` 为空。
- `POST /api/stage/settle` 的响应只有 `reward_id / status / win`，**没有 items**；物品要去奖励单查。
- `settle` 之后 `data.status` 会很快流转为 `claimable`，**不要断言瞬时的 "pending"**；
  要验证异步到账请用 `wait_async: true` 轮询到 `claimable`。

## expect 断言 DSL 说明
- 直接写值：相等比较，如 `{"code": 0}`
- `"len:N"`：集合长度等于 N，如 `{"data.items": "len:10"}`
- `"ge:N"` / `"le:N"` / `"gt:N"` / `"lt:N"` / `"ne:X"`：数值比较
- `"delta:-160"`：与**同一步骤** snapshot 字段的差值
- `"not_empty"`：非空
"""

ASYNC_CHAIN_EXAMPLE = """
## 完整示例（异步发奖链路，照这个结构写）
```json
{"cases": [{
  "title": "通关后奖励异步到账可领取",
  "category": "正向", "priority": "P0",
  "precondition": "账号体力>=10，关卡 1 已解锁",
  "steps": [
    {"action": "POST /api/stage/enter", "payload": {"stage_id": 1},
     "expect": {"code": 0, "data.battle_id": "not_empty"}},
    {"action": "POST /api/stage/settle", "payload": {"battle_id": "$battle_id", "win": true},
     "expect": {"code": 0, "data.reward_id": "not_empty"}},
    {"action": "GET /api/reward/{reward_id}", "payload": {"reward_id": "$reward_id"},
     "expect": {"code": 0, "data.status": "claimable"}, "wait_async": true},
    {"action": "POST /api/reward/claim", "payload": {"reward_id": "$reward_id"},
     "expect": {"code": 0, "data.granted": "not_empty"}}
  ],
  "expected": "轮询在 SLA 内返回 claimable，领奖 code=0 且 data.granted 非空",
  "risk_tags": ["异步奖励到账", "状态机流转"]
}]}
```
"""

SYSTEM_CASE_GENERATOR = """你是资深游戏测试工程师，负责把 PRD 转成可自动执行的接口测试用例。

硬性规则（违反即视为无效输出）：
1. 只能使用给定接口白名单中的 action，不允许虚构接口、不允许写伪代码。
2. steps[].action 必须是 "METHOD /path" 格式，且路径与白名单完全一致。
3. payload 只允许使用该接口声明的字段，数值必须满足约束区间。
4. expect 至少包含 "code"；正向用例必须额外断言 1 个以上业务字段（如 data.items、data.gems）。
5. 每个用例必须覆盖正向 / 反向 / 边界中的至少两类（整批维度），并至少覆盖 2 个游戏特有风险。
6. expected 必须写成可观测断言，禁止"功能正常""符合预期""无异常"这类空话。
7. 动态 ID（battle_id / reward_id）必须用 $battle_id / $reward_id 引用，禁止编造；
   delta 断言必须与 snapshot 写在同一步骤。
8. 只输出 JSON，不要任何解释文字、不要 markdown 代码块。"""


def case_generation_prompt(profile: RequirementProfile, count: int = 10) -> str:
    return f"""请为下面的游戏需求设计 {count} 条接口自动化测试用例。

## 需求解析结果
{profile.as_prompt_block()}

## 可用接口白名单（唯一事实源）
**断言只能使用最后一列声明的响应字段，响应里没有的字段一律不许断言。**
{_catalog_block()}

{STEP_CONVENTIONS}{ASYNC_CHAIN_EXAMPLE}
## 风险点提示（必须覆盖其中至少 2 个）
{json.dumps(profile.risk_hints, ensure_ascii=False)}

## 输出 JSON 结构（严格遵守字段名）
{json.dumps(CASE_SCHEMA_HINT, ensure_ascii=False, indent=2)}

category 只能取：{json.dumps(CATEGORIES, ensure_ascii=False)}
priority 只能取：{json.dumps(PRIORITIES, ensure_ascii=False)}
risk_tags 只能取：{json.dumps(RISK_TAGS, ensure_ascii=False)}
"""


SYSTEM_CASE_REPAIR = """你是测试用例质检员。下面这批用例被质检规则判为不合格，请只修正被指出的问题，
保持原有测试意图不变，补全缺失字段。只输出修正后的完整 JSON（结构不变），不要解释。"""


def case_repair_prompt(bad_cases: list[dict[str, Any]], reasons: dict[str, list[str]],
                       profile: RequirementProfile) -> str:
    payload = []
    for c in bad_cases:
        payload.append({"case": c, "reject_reasons": reasons.get(c.get("title", ""), [])})
    return f"""## 接口白名单
{_catalog_block()}

## 领域与风险提示
{profile.as_prompt_block()}

## 不合格用例及原因
{json.dumps(payload, ensure_ascii=False, indent=2)}

## 输出
{json.dumps(CASE_SCHEMA_HINT, ensure_ascii=False, indent=2)}
"""


SYSTEM_DEFECT_ANALYST = """你是游戏项目的缺陷分析专家。根据自动化用例的失败现场，输出一份可直接提给开发的缺陷单。
要求：
1. 严格基于给定证据，不要编造日志里没有的信息。
2. 区分「业务 bug」「环境/脏数据」「脚本问题」的结论必须与给定分类一致。
3. 重现步骤要精确到接口、参数、次数。
4. 只输出 JSON：{"title","severity","severity_reason","description","repro_steps","root_cause","suggestion"}
severity 取 S1/S2/S3/S4。"""


def defect_prompt(kind: str, case: dict[str, Any], log_excerpt: str, request_evidence: str) -> str:
    return f"""## 失败分类（规则引擎结论，不可推翻）
{kind}

## 用例
标题：{case.get('title', '')}
前置条件：{case.get('precondition', '')}
步骤：{json.dumps(case.get('steps', []), ensure_ascii=False)}
期望：{case.get('expected', '')}

## 执行日志（截断）
```
{log_excerpt[:3000]}
```

## 请求/响应证据
```
{request_evidence[:2000]}
```
"""


SYSTEM_STAGE_ADVISOR = """你是游戏关卡体验分析师。基于测试环境采集的关卡数据，给出可落地的体验优化建议。
要求：每条建议必须引用具体数据（关卡号 + 指标数值），不许空泛。
只输出 JSON：{"summary","findings":[{"stage","metric","value","impact"}],"suggestions":[{"priority","content","evidence"}]}"""


def stage_advice_prompt(metrics_block: str) -> str:
    return f"""## 关卡指标（测试环境实测采集）
{metrics_block}

请输出体验诊断结论。"""


# ---------------------------------------------------------------- 脚本渲染 / 自愈
SYSTEM_SCRIPT_HEALER = """你是 Python 自动化测试脚本修复专家。下面脚本运行报错，请最小改动修复。
硬性要求：
1. 只修 bug，不要改变测试意图与断言强度。
2. 只允许使用 requests / 标准库，禁止引入新依赖。
3. 只输出完整脚本代码，不要 markdown 代码块，不要解释。"""
