"""用例质量校验器 —— 这个项目的核心差异化能力。

大模型生成用例最大的问题不是"写得不好"，而是"写得很像但根本跑不起来"：
    · 编造一个不存在的接口 /api/gacha/drawTen
    · 编造不存在的字段 {"poolId": "xxx"}（驼峰 vs 下划线）
    · 把参数写成超出配置区的值（times=100）
    · 期望结果写"功能正常"——等于没断言

所以这里不是"让 AI 自己检查"，而是用**可执行、可单测的硬规则**做质检：
    接口白名单 → 参数契约 → 断言强度 → 语义一致性 → 重复度
不合格的用例不进用例库、不生成脚本，并把原因回灌给 AI 让它重写（闭环）。

这套规则的价值：把 AI 的幻觉挡在「用例层」，永远不会污染到「脚本层」。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app import config
from app.api_catalog import lookup, normalize_action
from app.m1_cases.schema import (
    CATEGORIES,
    PRIORITIES,
    RISK_TAGS,
    VAGUE_WORDS,
    case_fingerprint,
    jaccard,
    step_signature_set,
)

ERROR = "error"
WARN = "warn"

POSITIVE_LIKE = ("正向", "异常", "性能")


@dataclass
class Issue:
    rule: str
    level: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"rule": self.rule, "level": self.level, "message": self.message}


@dataclass
class ValidationResult:
    case: dict[str, Any]
    issues: list[Issue] = field(default_factory=list)
    score: float = 100.0

    @property
    def ok(self) -> bool:
        return not any(i.level == ERROR for i in self.issues)

    @property
    def status(self) -> str:
        return "valid" if self.ok else "rejected"

    def add(self, rule: str, level: str, message: str) -> None:
        self.issues.append(Issue(rule, level, message))

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "score": round(self.score, 1),
            "issues": [i.as_dict() for i in self.issues],
        }


# ------------------------------------------------------------------ 单条校验

def _check_structure(res: ValidationResult) -> None:
    case = res.case
    if not case.get("title"):
        res.add("R1-结构", ERROR, "缺少用例标题")
    if case.get("category") not in CATEGORIES:
        res.add("R1-结构", ERROR, f"category 非法：{case.get('category')!r}，必须是 {CATEGORIES}")
    if case.get("priority") not in PRIORITIES:
        res.add("R1-结构", WARN, f"priority 非法：{case.get('priority')!r}，已按 P1 处理")
    if not case.get("steps"):
        res.add("R1-结构", ERROR, "steps 为空，用例不可执行")
    if not case.get("expected"):
        res.add("R1-结构", ERROR, "缺少 expected")


def _check_api_contract(res: ValidationResult) -> None:
    """R2：接口必须真实存在（幻觉接口检测）。"""
    for idx, step in enumerate(res.case.get("steps", []), 1):
        action = step.get("action", "")
        if not action:
            res.add("R2-接口契约", ERROR, f"步骤{idx} 缺少 action")
            continue
        spec = lookup(action)
        if spec is None:
            res.add(
                "R2-接口契约", ERROR,
                f"步骤{idx} 引用了不存在的接口「{normalize_action(action)}」——疑似 AI 幻觉，"
                f"可用接口请参考 api_catalog",
            )


def _check_params(res: ValidationResult, enforce_range: bool) -> None:
    """R3：参数字段与取值必须符合契约（反向/边界用例故意越界时不校验取值范围）。"""
    case = res.case
    for idx, step in enumerate(case.get("steps", []), 1):
        spec = lookup(step.get("action", ""))
        if spec is None:
            continue
        payload = step.get("payload") or {}
        known = set(spec.required) | set(spec.optional)
        unknown = [k for k in payload if k not in known]
        if unknown:
            res.add(
                "R3-参数契约", ERROR,
                f"步骤{idx}「{spec.endpoint}」出现未定义字段 {unknown}，"
                f"可用字段 {sorted(known) or '无'}",
            )
        if not enforce_range:
            continue
        missing = [k for k in spec.required if k not in payload]
        if missing:
            res.add("R3-参数契约", ERROR,
                    f"步骤{idx}「{spec.endpoint}」缺少必填参数 {missing}")
        for field_name, rule in spec.constraints.items():
            if field_name not in payload:
                continue
            value = payload[field_name]
            # 正向用例出现越界参数时，很可能是把「反向用例」误标成了「正向」——直接把排查方向给出来
            label_hint = "；若本意是验证非法参数，应把 category 标为「反向」或「边界」"
            if "enum" in rule and value not in rule["enum"]:
                res.add("R3-参数契约", ERROR,
                        f"步骤{idx} 参数 {field_name}={value!r} 不在允许取值 {rule['enum']} 内"
                        + (label_hint if enforce_range else ""))
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if "min" in rule and value < rule["min"]:
                    res.add("R3-参数契约", ERROR,
                            f"步骤{idx} 参数 {field_name}={value} 小于下限 {rule['min']}")
                if "max" in rule and value > rule["max"]:
                    res.add("R3-参数契约", ERROR,
                            f"步骤{idx} 参数 {field_name}={value} 超过上限 {rule['max']}"
                            + (label_hint if enforce_range else ""))


# 这些字段的值是「运行时产生的」动态 ID，用例里必须用 $变量 引用而不是写死
DYNAMIC_REFS = {"battle_id": "$battle_id", "reward_id": "$reward_id"}
# 哪个接口会产出哪个变量（渲染器会在拿到响应后自动捕获）
PRODUCED_BY = {
    "POST /api/login": {"account", "token"},
    "POST /api/stage/enter": {"battle_id"},
    "POST /api/stage/settle": {"reward_id"},
}


def _check_variable_refs(res: ValidationResult) -> None:
    """R9：动态 ID 必须走变量引用，且依赖链完整。

    真实大模型踩过的坑：它不知道 `$battle_id` 这套约定（提示词没写），
    于是编了 `battle_id="BATTLE_5_123456"`，执行时必然是「对象不存在」，
    15 条用例里 11 条被误判成环境问题。规则拦在用例层，就不会污染执行结果。
    """
    produced: set[str] = set()
    for idx, step in enumerate(res.case.get("steps", []), 1):
        payload = step.get("payload") or {}
        expect = step.get("expect") or {}
        spec = lookup(step.get("action", ""))

        for field_name, ref in DYNAMIC_REFS.items():
            if field_name not in payload:
                continue
            value = payload[field_name]
            if isinstance(value, str) and value.startswith("$"):
                continue
            if field_name in produced:
                res.add("R9-变量引用", ERROR,
                        f"步骤{idx} 把 {field_name} 写死为 {value!r}，"
                        f"应改用 {ref} 引用上一步的返回值")
            else:
                try:
                    exp_code = int(expect.get("code", 0))
                except (TypeError, ValueError):
                    exp_code = 0
                if exp_code == 0:
                    res.add("R9-变量引用", ERROR,
                            f"步骤{idx} 使用了 {field_name}={value!r}，但前面没有任何步骤产生它；"
                            f"正向用例必须先调用产生该 ID 的接口，并改用 {ref}")
                else:
                    res.add("R9-变量引用", WARN,
                            f"步骤{idx} 使用字面量 {field_name}（若刻意测试不存在的对象可忽略）")

        if spec and spec.endpoint in PRODUCED_BY:
            produced |= PRODUCED_BY[spec.endpoint]


def _check_delta_snapshot(res: ValidationResult) -> None:
    """R10：delta 断言与 snapshot 必须同步骤。

    snapshot 采集的是「调用前」的值，所以只能跟在发起请求的那一步上；
    写在上一步等于拿了个不相关的基准，delta 永远对不上。
    """
    for idx, step in enumerate(res.case.get("steps", []), 1):
        expect = step.get("expect") or {}
        snapshot = step.get("snapshot") or []
        uses_delta = any(isinstance(v, str) and v.startswith("delta:") for v in expect.values())
        if uses_delta and not snapshot:
            res.add("R10-快照断言", ERROR,
                    f"步骤{idx} 使用了 delta 断言，但该步骤没有声明 snapshot；"
                    f"delta 与 snapshot 必须写在同一步骤")
        elif snapshot and not uses_delta:
            res.add("R10-快照断言", WARN,
                    f"步骤{idx} 声明了 snapshot 却没有任何 delta 断言，快照无意义")


def _check_response_fields(res: ValidationResult) -> None:
    """R11：断言路径必须是接口真实返回的字段。

    真实踩坑：模型看不到响应结构，于是编了 data.bag_used / data.mail_pending /
    data.pity_pool_featured 这些不存在的字段 —— 执行时才 KeyError，白跑一轮。
    契约补上 response 之后，这条规则能在用例层直接把它们挡掉。
    """
    for idx, step in enumerate(res.case.get("steps", []), 1):
        spec = lookup(step.get("action", ""))
        expect = step.get("expect") or {}
        if spec is None or not spec.response:
            continue
        data_keys = spec.response.get("data") or {}
        if not data_keys:
            continue
        for path in expect:
            if path == "code" or not isinstance(path, str) or not path.startswith("data."):
                continue
            head = path[len("data."):].split(".")[0].split("[")[0]
            if head and head not in data_keys:
                res.add("R11-响应字段", ERROR,
                        f"步骤{idx} 断言了 {path}，但 {spec.endpoint} 的响应里没有 data.{head}；"
                        f"可用字段：{sorted(data_keys)}")


def _check_transient_state(res: ValidationResult) -> None:
    """R12：不要断言"瞬时状态"。

    真实踩过的坑：用例断言 settle 之后 data.status == "pending"，
    但服务端 1 秒内就流转到 claimable，执行时随机失败并被误判成业务缺陷。
    异步要用 wait_async 轮询到目标状态，而不是赌某个瞬间。
    """
    for idx, step in enumerate(res.case.get("steps", []), 1):
        expect = step.get("expect") or {}
        status = expect.get("data.status")
        if isinstance(status, str) and status == "pending" and not step.get("wait_async"):
            res.add("R12-时序断言", WARN,
                    f"步骤{idx} 断言了瞬时状态 data.status=pending，异步链路上极易误报；"
                    f"建议改为 wait_async 轮询到 claimable")


def _check_assertion(res: ValidationResult) -> None:
    """R4：断言强度。只断言 code 的用例等于没测业务。

    注意区分两类"非正向"用例：
        · 反向用例：传非法参数，必须断言非 0 错误码；
        · 边界用例：参数合法但落在边界上，断言的是「边界值带来的差异化结果」
          （如保底必出 SSR、扣钻 delta 精确、len:0），不能强求非 0 错误码。
    """
    case = res.case
    positive = case.get("category") in POSITIVE_LIKE
    business_asserts = 0
    strong_business_assert = False
    has_code = False
    non_zero_code = False
    for step in case.get("steps", []):
        expect = step.get("expect") or {}
        if not expect:
            res.add("R4-断言强度", ERROR, f"步骤「{step.get('action')}」没有任何断言")
            continue
        for key, value in expect.items():
            if key == "code":
                has_code = True
                try:
                    if int(value) != 0:
                        non_zero_code = True
                except (TypeError, ValueError):
                    pass
            else:
                business_asserts += 1
                # not_empty 属于弱断言；其余（精确值 / len: / delta: / ge: / ne:）算强断言
                if not (isinstance(value, str) and value.strip() == "not_empty"):
                    strong_business_assert = True
    if positive and not has_code:
        res.add("R4-断言强度", ERROR, "正向用例必须断言 code=0")
    if positive and business_asserts == 0:
        res.add("R4-断言强度", ERROR,
                "正向用例只断言了 code，缺少业务字段断言（如 data.items / data.gems）")
    if not positive and not (non_zero_code or strong_business_assert):
        res.add(
            "R4-断言强度", ERROR,
            f"{case.get('category')}用例既没有断言非 0 错误码，也没有断言的边界值"
            f"（如 rarity=SSR / delta:-160 / len:0），验证不出差异化结果",
        )


def _check_expected(res: ValidationResult) -> None:
    """R5：expected 必须可观测，禁止"功能正常"这类空话。"""
    text = res.case.get("expected", "")
    if any(word in text for word in VAGUE_WORDS) and len(text) < 30:
        res.add("R5-期望可观测", ERROR, f"expected 过于空泛（{text!r}），无法作为验收依据")
    has_number = any(ch.isdigit() for ch in text)
    has_field = any(token in text for token in ("data.", "code", "=", "返回"))
    if not (has_number or has_field):
        res.add("R5-期望可观测", WARN, "expected 中未出现字段名或数值，建议补充可观测断言点")


def _check_precondition(res: ValidationResult) -> None:
    if not res.case.get("precondition"):
        res.add("R6-前置条件", WARN, "缺少前置条件，脚本执行前无法保证数据状态")
    tags = res.case.get("risk_tags") or []
    illegal = [t for t in tags if t not in RISK_TAGS]
    if illegal:
        res.add("R7-枚举", WARN, f"risk_tags 含未定义标签 {illegal}")
    if not tags:
        res.add("R7-枚举", WARN, "未标注任何风险标签，无法评估风险覆盖")


def _apply_score(res: ValidationResult) -> None:
    penalty = 0.0
    for issue in res.issues:
        penalty += 25 if issue.level == ERROR else 8
    res.score = max(0.0, 100.0 - penalty)


def validate_case(case: dict[str, Any]) -> ValidationResult:
    res = ValidationResult(case=case)
    _check_structure(res)
    _check_api_contract(res)
    _check_params(res, enforce_range=case.get("category") in POSITIVE_LIKE)
    _check_variable_refs(res)
    _check_response_fields(res)
    _check_delta_snapshot(res)
    _check_transient_state(res)
    _check_assertion(res)
    _check_expected(res)
    _check_precondition(res)
    _apply_score(res)
    return res


# ------------------------------------------------------------------ 批次校验

@dataclass
class BatchReport:
    total: int = 0
    valid: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    details: list[dict[str, Any]] = field(default_factory=list)
    coverage: dict[str, Any] = field(default_factory=dict)
    reject_reasons: dict[str, list[str]] = field(default_factory=dict)

    @property
    def valid_rate(self) -> float:
        return round(len(self.valid) / self.total * 100, 1) if self.total else 0.0

    @property
    def avg_score(self) -> float:
        if not self.details:
            return 0.0
        return round(sum(d["score"] for d in self.details) / len(self.details), 1)

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "valid": len(self.valid),
            "rejected": len(self.rejected),
            "valid_rate": self.valid_rate,
            "avg_score": self.avg_score,
            "coverage": self.coverage,
        }


def validate_batch(cases: list[dict[str, Any]],
                   threshold: float | None = None) -> BatchReport:
    dedup_threshold = config.CASE_DEDUP_THRESHOLD if threshold is None else threshold
    report = BatchReport(total=len(cases))
    seen: list[tuple[dict[str, Any], set[str]]] = []

    for case in cases:
        res = validate_case(case)

        # R8：重复度检测（与已通过用例比 Jaccard 相似度）
        sig = step_signature_set(case)
        for prev_case, prev_sig in seen:
            similarity = jaccard(sig, prev_sig)
            if similarity >= dedup_threshold:
                res.add("R8-去重", ERROR,
                        f"与用例「{prev_case.get('title', '')}」步骤相似度 {similarity:.2f}，"
                        f"属于重复用例")
                res.score = max(0.0, res.score - 20)
                break

        case["_fingerprint"] = case_fingerprint(case)
        case["_validation"] = res.as_dict()
        case["score"] = res.score
        case["status"] = res.status
        case["title"] = case.get("title") or f"未命名用例-{len(report.details) + 1}"

        report.details.append({
            "title": case["title"],
            "category": case.get("category", ""),
            "score": round(res.score, 1),
            "status": res.status,
            "issues": [i.as_dict() for i in res.issues],
        })

        if res.ok and res.score >= config.MIN_CASE_SCORE:
            report.valid.append(case)
            seen.append((case, sig))
        else:
            report.rejected.append(case)
            report.reject_reasons[case["title"]] = [i.message for i in res.issues]

    report.coverage = _coverage_report(report.valid)
    return report


def _coverage_report(cases: list[dict[str, Any]]) -> dict[str, Any]:
    by_category: dict[str, int] = {}
    tags: set[str] = set()
    endpoints: set[str] = set()
    for c in cases:
        by_category[c.get("category", "未知")] = by_category.get(c.get("category", "未知"), 0) + 1
        tags.update(c.get("risk_tags") or [])
        endpoints.update(c.get("api_refs") or [])
    missing = [cat for cat in CATEGORIES if cat not in by_category and cat in ("正向", "反向", "边界")]
    return {
        "by_category": by_category,
        "risk_tags": sorted(tags),
        "endpoints": sorted(endpoints),
        "missing_categories": missing,
        "risk_coverage": len(tags),
    }
