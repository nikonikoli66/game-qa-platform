"""API 契约目录 —— 整个平台的「唯一事实源」。

为什么单独抽这一层？
    现实项目里最常见的坑是：用例里写的接口名、脚本里调的接口名、测试环境实际
    的接口名，三处对不上，导致大量「假失败」。所以把接口契约收敛成一份可被
    代码消费的元数据：

        api_catalog  ──┬─→ Mock 服（按契约实现）
                       ├─→ AI 用例生成（只允许引用这里存在的接口）
                       ├─→ 幻觉校验（引用不存在的接口 → 直接判不合格）
                       └─→ 脚本渲染（按契约拼请求与断言）

    契约变了只改这一处，其余模块自动跟随，消灭跨模块不一致。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# 统一错误码，Mock 服与断言共用
ERR_OK = 0
ERR_PARAM = 1001          # 参数非法
ERR_UNAUTHORIZED = 1002   # 登录态失效
ERR_RESOURCE_LACK = 1003  # 资源不足（钻石/体力）
ERR_NOT_FOUND = 1004      # 对象不存在
ERR_STATE_CONFLICT = 1005 # 状态冲突（重复领取等）


@dataclass(frozen=True)
class ApiSpec:
    method: str
    path: str
    summary: str
    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    # 字段约束：{"times": {"min": 1, "max": 10}, "pool_id": {"enum": [...]}}
    constraints: dict[str, dict[str, Any]] = field(default_factory=dict)
    # 该接口是否会异步产出奖励（需要轮询等待），用于自动生成等待逻辑
    async_reward: bool = False
    # 响应体结构（data 下的字段）。声明它是为了把契约做成「双向」：
    # 只给入参不给响应，模型就会编字段名（真实踩过 bag_used / mail_pending / items.0.rarity）
    response: dict[str, Any] = field(default_factory=dict)
    # 是否需要登录态
    auth: bool = True

    @property
    def endpoint(self) -> str:
        return f"{self.method} {self.path}"


_API_SPECS: tuple[ApiSpec, ...] = (
    ApiSpec("GET", "/api/health", "健康检查", auth=False,
            response={"data": {"status": "str"}}),
    ApiSpec(
        "POST", "/api/login", "账号登录，返回 token 与 player_id",
        required=("account",), auth=False,
        response={"data": {"token": "str", "player_id": "str", "account": "str",
                           "gems": "int", "stamina": "int"}},
    ),
    ApiSpec("GET", "/api/bag", "查询背包、钻石、体力",
            response={"data": {"gems": "int", "stamina": "int",
                               "items": "[{item_id,count}]", "capacity": "int",
                               "used": "int", "mail_count": "int"}}),
    ApiSpec(
        "POST", "/api/gacha/draw", "卡池抽卡，扣钻并产出道具",
        required=("pool_id", "times"),
        constraints={
            "pool_id": {"enum": ["pool_standard", "pool_featured"]},
            "times": {"min": 1, "max": 10, "enum": [1, 10]},
        },
        response={"data": {"items": "[{item_id,rarity}]", "gems": "int",
                           "pity": "int", "pity_limit": "int", "mail_delivered": "bool"}},
    ),
    ApiSpec(
        "POST", "/api/stage/enter", "进入关卡，扣体力，返回战斗 ID",
        required=("stage_id",),
        constraints={"stage_id": {"min": 1, "max": 15}},
        response={"data": {"battle_id": "str", "stage_id": "int",
                           "stamina": "int", "stamina_cost": "int"}},
    ),
    ApiSpec(
        "POST", "/api/stage/settle", "结算关卡，返回异步奖励 ID",
        required=("battle_id", "win"),
        async_reward=True,
        response={"data": {"reward_id": "str", "status": "str", "win": "bool"}},
    ),
    ApiSpec("GET", "/api/reward/{reward_id}", "查询奖励状态（异步到账轮询）", required=("reward_id",),
            response={"data": {"reward_id": "str", "status": "str(pending/claimable/claimed)",
                               "items": "[{item_id,count}]", "remain_ms": "int"}}),
    ApiSpec("POST", "/api/reward/claim", "领取奖励（需幂等）", required=("reward_id",),
            response={"data": {"granted": "[{item_id,count}]", "already_claimed": "bool"}}),
    ApiSpec(
        "POST", "/api/admin/pity", "后台设置保底计数（测试专用 GM 指令）",
        required=("account", "pool_id", "pity"),
        constraints={"pity": {"min": 0, "max": 90}},
        response={"data": {"account": "str", "pool_id": "str", "pity": "int"}},
    ),
    ApiSpec(
        "POST", "/api/admin/grant", "GM 指令：设置钻石/体力（构造边界数据）",
        required=("account",),
        optional=("gems", "stamina"),
        constraints={"gems": {"min": 0, "max": 999999}, "stamina": {"min": 0, "max": 999}},
        response={"data": {"account": "str", "gems": "int", "stamina": "int"}},
    ),
    ApiSpec(
        "POST", "/api/admin/bag_fill", "GM 指令：把背包填到指定数量（构造溢出场景）",
        required=("account",),
        optional=("count",),
        response={"data": {"used": "int", "capacity": "int"}},
    ),
    ApiSpec("POST", "/api/admin/reset", "重置账号数据（脏数据清理）", required=("account",),
            response={"data": {"account": "str", "reset": "bool", "gems": "int", "stamina": "int"}}),
    ApiSpec("POST", "/api/telemetry/stage", "上报关卡行为埋点",
            response={"data": {"received": "int"}}),
)

API_CATALOG: dict[str, ApiSpec] = {spec.endpoint: spec for spec in _API_SPECS}

# 路径模板匹配：/api/reward/{reward_id} -> 可匹配 /api/reward/abc123
_PARAM_RE = re.compile(r"\{[a-zA-Z_]+\}")


def normalize_action(action: str) -> str:
    """把用例里的写法归一化成 "METHOD /path"。容忍大小写、多余空格、带域名。"""
    text = (action or "").strip()
    text = re.sub(r"^https?://[^/]+", "", text)          # 去掉协议+域名
    text = re.sub(r"\s+", " ", text)
    parts = text.split(" ", 1)
    if len(parts) == 1:                                  # 只写了 path
        return f"POST {parts[0].strip()}"
    return f"{parts[0].strip().upper()} {parts[1].strip()}"


def lookup(action: str) -> ApiSpec | None:
    """按用例中的 action 查找契约；支持带具体 id 的实际路径。"""
    key = normalize_action(action)
    if key in API_CATALOG:
        return API_CATALOG[key]
    method, _, path = key.partition(" ")
    for spec in _API_SPECS:
        if spec.method != method:
            continue
        if _PARAM_RE.search(spec.path):
            pattern = "^" + _PARAM_RE.sub(r"[^/]+", spec.path) + "$"
            if re.match(pattern, path):
                return spec
    return None


def known_endpoints() -> set[str]:
    return set(API_CATALOG)


def catalog_table() -> list[dict[str, Any]]:
    """给前端/提示词用的表格化契约。"""
    rows = []
    for spec in _API_SPECS:
        rows.append(
            {
                "endpoint": spec.endpoint,
                "summary": spec.summary,
                "required": ", ".join(spec.required) or "-",
                "constraints": "; ".join(
                    f"{k}:{v}" for k, v in spec.constraints.items()
                ) or "-",
                "async": "是" if spec.async_reward else "",
                "response": "、".join(
                    f"{k}:{v}" for k, v in (spec.response.get("data") or {}).items()
                ) or "-",
            }
        )
    return rows
