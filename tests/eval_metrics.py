"""Eval L1 + L2 metric 计算(纯函数,无 API 依赖)。

每个 metric 函数返回 `MetricResult`:
  - passed:bool,本 metric 通过没
  - detail:str,失败原因 / 通过简述(给 markdown 报告用)

设计原则:
- 纯函数,easy unit test
- "expected 字段不存在" → 该 metric 自动 SKIP,不算失败(case 没要求测它)
- "expected 字段存在但 actual 缺失" → 算 FAIL(case 要求测但拿不到数据)
- Set inclusion 哲学:tool_calls / brands 比对走 set,不严格按序
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from server.domain.types import QueryPlan


@dataclass(slots=True)
class MetricResult:
    name: str
    passed: bool
    detail: str
    skipped: bool = False


def _is_default(v: Any) -> bool:
    """HardConstraints 的"未填"判定:None / 空 list / 空 dict。

    bool False / 数字 0 视为"有值"(contains_alcohol=False 是"明确要无酒精",
    不是缺省)。收紧 exact 匹配时用它识别 actual 里多出的非默认字段。
    """
    return v is None or v == [] or v == {}


# ──────────────────────────────────────────────────────────────────────
# L1:Planner 准确率
# ──────────────────────────────────────────────────────────────────────
def planner_query_type(actual: QueryPlan | None, expected: str | None) -> MetricResult:
    if expected is None:
        return MetricResult("planner_query_type", True, "no expectation", skipped=True)
    if actual is None:
        return MetricResult("planner_query_type", False, "actual plan is None (Planner 没跑或失败)")
    ok = actual.query_type == expected
    return MetricResult(
        "planner_query_type",
        ok,
        f"expected={expected!r}, actual={actual.query_type!r}",
    )


def planner_hard_constraints(
    actual: QueryPlan | None,
    expected_full: dict | None,
    expected_partial: dict | None,
) -> MetricResult:
    """expected_hard_constraints(精确匹配) vs expected_hard_constraints_contains(部分匹配)。

    精确匹配:expected 里每个字段都要等于 actual,且 actual 不能有多出的非默认字段
    部分匹配:expected 里指定的字段必须等于 actual,其他字段不管
    """
    if expected_full is None and expected_partial is None:
        return MetricResult("planner_hard_constraints", True, "no expectation", skipped=True)
    if actual is None:
        return MetricResult("planner_hard_constraints", False, "actual plan is None")

    actual_dict = actual.hard_constraints.model_dump(exclude_defaults=False)

    if expected_full is not None:
        # 精确匹配:expected 全字段必须等于 actual
        mismatches = []
        for k, v in expected_full.items():
            av = actual_dict.get(k)
            # list 比较走 set(brand_exclude / suitable_skin 顺序无关)
            if isinstance(v, list) and isinstance(av, list):
                if set(v) != set(av):
                    mismatches.append(f"{k}: expected={v}, actual={av}")
            elif av != v:
                mismatches.append(f"{k}: expected={v!r}, actual={av!r}")
        # 收紧:actual 不许有 expected 没列、且非默认的字段。
        # 防"画像泄漏进 hard"类回归(如 gender 从画像渗进硬过滤),exact case
        # 必须列全 Planner 该吐的字段;只想部分校验请用 expected_hard_constraints_contains。
        for k, av in actual_dict.items():
            if k in expected_full or _is_default(av):
                continue
            mismatches.append(f"多余字段 {k}={av!r}(期望未列,疑似泄漏)")
        if mismatches:
            return MetricResult(
                "planner_hard_constraints",
                False,
                "; ".join(mismatches),
            )
        return MetricResult("planner_hard_constraints", True, "exact match (无多余字段)")

    # 部分匹配
    assert expected_partial is not None
    mismatches = []
    for k, v in expected_partial.items():
        av = actual_dict.get(k)
        if isinstance(v, list) and isinstance(av, list):
            if not set(v).issubset(set(av)):
                mismatches.append(f"{k}: expected superset of {v}, actual={av}")
        elif av != v:
            mismatches.append(f"{k}: expected={v!r}, actual={av!r}")
    if mismatches:
        return MetricResult("planner_hard_constraints", False, "; ".join(mismatches))
    return MetricResult("planner_hard_constraints", True, "partial match OK")


def planner_referenced_ids(
    actual: QueryPlan | None,
    expected_ids: list[str] | None,
    expected_count_min: int | None,
) -> MetricResult:
    if expected_ids is None and expected_count_min is None:
        return MetricResult("planner_referenced_ids", True, "no expectation", skipped=True)
    if actual is None:
        return MetricResult("planner_referenced_ids", False, "actual plan is None")

    actual_ids = list(actual.referenced_product_ids)

    if expected_ids is not None:
        if set(expected_ids) != set(actual_ids):
            return MetricResult(
                "planner_referenced_ids",
                False,
                f"expected={expected_ids}, actual={actual_ids}",
            )
        return MetricResult("planner_referenced_ids", True, "exact match")

    assert expected_count_min is not None
    ok = len(actual_ids) >= expected_count_min
    return MetricResult(
        "planner_referenced_ids",
        ok,
        f"expected count>={expected_count_min}, actual count={len(actual_ids)}",
    )


def planner_soft_preferences(
    actual: QueryPlan | None,
    expected_contains: dict[str, Any] | None,
) -> MetricResult:
    """soft_preferences 部分匹配(宽松,对应 expected_soft_preferences_contains)。

    哲学:soft 是开放词表、接受同义漂移(planner prompt #6),所以只查"包含"不查精确:
    expected 里每个 key 必须存在,且其每个值要能在 actual 对应项里**双向子串**命中
    (如 expected "保湿" 命中 actual "长效保湿")。这样能锁住"画像偏好软化"这条新行为
    (skin/fragrance 该落在 soft 而非 hard),又不会因近义词写法不同而误判。
    """
    if expected_contains is None:
        return MetricResult(
            "planner_soft_preferences", True, "no expectation", skipped=True
        )
    if actual is None:
        return MetricResult("planner_soft_preferences", False, "actual plan is None")

    soft = actual.soft_preferences or {}
    issues: list[str] = []
    for key, exp_vals in expected_contains.items():
        if key not in soft:
            issues.append(f"缺 key {key!r}")
            continue
        act_raw = soft[key]
        act_list = act_raw if isinstance(act_raw, list) else [act_raw]
        act_str = [str(a) for a in act_list]
        exp_list = exp_vals if isinstance(exp_vals, list) else [exp_vals]
        for ev in exp_list:
            ev_s = str(ev)
            if not any(ev_s in a or a in ev_s for a in act_str):
                issues.append(f"{key}: {ev_s!r} 不在 {act_str}")
    if issues:
        return MetricResult("planner_soft_preferences", False, "; ".join(issues))
    return MetricResult("planner_soft_preferences", True, "soft prefs 全部命中")


# ──────────────────────────────────────────────────────────────────────
# L2:Tool 调用序列(set inclusion,顺序不敏感)
# ──────────────────────────────────────────────────────────────────────
def tool_sequence_match(
    actual_calls: list[tuple[str, dict]],
    include: list[str] | None,
    exclude: list[str] | None,
    min_count: int | None,
) -> MetricResult:
    if include is None and exclude is None and min_count is None:
        return MetricResult("tool_sequence", True, "no expectation", skipped=True)

    actual_names = {name for name, _ in actual_calls}

    issues = []
    if include is not None:
        missing = set(include) - actual_names
        if missing:
            issues.append(f"missing required tools: {sorted(missing)}")
    if exclude is not None:
        unexpected = set(exclude) & actual_names
        if unexpected:
            issues.append(f"forbidden tools called: {sorted(unexpected)}")
    if min_count is not None and len(actual_calls) < min_count:
        issues.append(f"call count {len(actual_calls)} < min {min_count}")

    if issues:
        return MetricResult(
            "tool_sequence",
            False,
            f"actual={sorted(actual_names)}; " + "; ".join(issues),
        )
    return MetricResult(
        "tool_sequence",
        True,
        f"actual={sorted(actual_names)}",
    )


# ──────────────────────────────────────────────────────────────────────
# L2:Top-K 商品 / brand 过滤
# ──────────────────────────────────────────────────────────────────────
def top_k_contains_ids(
    cards: list[dict],
    expected_ids: list[str] | None,
    k: int = 5,
) -> MetricResult:
    if expected_ids is None:
        return MetricResult("top_k_contains_ids", True, "no expectation", skipped=True)
    top_ids = [c.get("product_id") for c in cards[:k] if c.get("product_id")]
    missing = set(expected_ids) - set(top_ids)
    if missing:
        return MetricResult(
            "top_k_contains_ids",
            False,
            f"expected to contain {expected_ids}, missing {sorted(missing)}; top_{k}={top_ids}",
        )
    return MetricResult("top_k_contains_ids", True, f"all of {expected_ids} in top_{k}")


def top_k_excludes_brands(
    cards: list[dict],
    expected_excludes: list[str] | None,
    k: int = 5,
) -> MetricResult:
    if expected_excludes is None:
        return MetricResult("top_k_excludes_brands", True, "no expectation", skipped=True)
    top_brands = {c.get("brand") for c in cards[:k] if c.get("brand")}
    leaked = top_brands & set(expected_excludes)
    if leaked:
        return MetricResult(
            "top_k_excludes_brands",
            False,
            f"forbidden brands in top_{k}: {sorted(leaked)}; all_top_brands={sorted(top_brands)}",
        )
    return MetricResult(
        "top_k_excludes_brands",
        True,
        f"none of {expected_excludes} in top_{k}",
    )


# ──────────────────────────────────────────────────────────────────────
# L2:No-match 触发
# ──────────────────────────────────────────────────────────────────────
def no_match_check(cards: list[dict], expected_no_match: bool | None) -> MetricResult:
    if expected_no_match is None or expected_no_match is False:
        return MetricResult("no_match", True, "no expectation", skipped=True)
    if len(cards) == 0:
        return MetricResult("no_match", True, "cards empty as expected")
    return MetricResult(
        "no_match",
        False,
        f"expected no_match but got {len(cards)} cards",
    )


# ──────────────────────────────────────────────────────────────────────
# L2:回复关键词
# ──────────────────────────────────────────────────────────────────────
def response_keyword_match(
    text: str,
    must_contain_any: list[str] | None,
    must_not_contain: list[str] | None,
) -> MetricResult:
    if must_contain_any is None and must_not_contain is None:
        return MetricResult("response_keywords", True, "no expectation", skipped=True)

    issues = []
    if must_contain_any is not None:
        if not any(kw in text for kw in must_contain_any):
            issues.append(f"none of {must_contain_any} in response")
    if must_not_contain is not None:
        leaked = [kw for kw in must_not_contain if kw in text]
        if leaked:
            issues.append(f"forbidden in response: {leaked}")

    if issues:
        return MetricResult(
            "response_keywords",
            False,
            f"text_prefix={text[:80]!r}; " + "; ".join(issues),
        )
    return MetricResult("response_keywords", True, "all keyword constraints met")


# ──────────────────────────────────────────────────────────────────────
# L2:Profile 字段未变
# ──────────────────────────────────────────────────────────────────────
def profile_unchanged(
    before: dict[str, Any],
    after: dict[str, Any],
    fields: list[str] | None,
) -> MetricResult:
    if fields is None:
        return MetricResult("profile_unchanged", True, "no expectation", skipped=True)
    changes = []
    for f in fields:
        b = before.get(f)
        a = after.get(f)
        if b != a:
            changes.append(f"{f}: before={b!r}, after={a!r}")
    if changes:
        return MetricResult(
            "profile_unchanged",
            False,
            "; ".join(changes),
        )
    return MetricResult("profile_unchanged", True, f"all {fields} unchanged")


# ──────────────────────────────────────────────────────────────────────
# L2:Cart 变化
# ──────────────────────────────────────────────────────────────────────
def cart_change_match(
    cart_after: list[dict],
    expected_change: dict | None,
) -> MetricResult:
    """简单实现:只验证 action='add' 时 cart 非空,以及指定 product_id 是否出现。

    expected_change schema:
      {"action": "add", "product_id": "p_food_001"} → 验证 cart 里有该 product
    """
    if expected_change is None:
        return MetricResult("cart_change", True, "no expectation", skipped=True)

    action = expected_change.get("action")
    if action == "add":
        if not cart_after:
            return MetricResult("cart_change", False, "expected add but cart empty")
        expected_pid = expected_change.get("product_id")
        if expected_pid is not None:
            found = any(
                item.get("product_id") == expected_pid or
                item.get("sku_id", "").startswith(f"s_{expected_pid}_")
                for item in cart_after
            )
            if not found:
                return MetricResult(
                    "cart_change",
                    False,
                    f"expected product_id={expected_pid} not in cart; cart={cart_after}",
                )
        return MetricResult("cart_change", True, f"add action verified, cart_size={len(cart_after)}")

    return MetricResult(
        "cart_change",
        True,
        f"action={action} not strictly verified (skipped)",
        skipped=True,
    )


# ──────────────────────────────────────────────────────────────────────
# 聚合:跑完一个 case 的所有适用 metric
# ──────────────────────────────────────────────────────────────────────
def compute_all_metrics(
    case: dict,
    *,
    plan: QueryPlan | None,
    tool_calls: list[tuple[str, dict]],
    cards: list[dict],
    assistant_text: str,
    profile_before: dict,
    profile_after: dict,
    cart_after: list[dict],
) -> list[MetricResult]:
    """跑一个 case 的全部 L1+L2 metric,返回结果列表。"""
    results: list[MetricResult] = []

    # L1
    results.append(planner_query_type(plan, case.get("expected_query_type")))
    results.append(
        planner_hard_constraints(
            plan,
            case.get("expected_hard_constraints"),
            case.get("expected_hard_constraints_contains"),
        )
    )
    results.append(
        planner_referenced_ids(
            plan,
            case.get("expected_referenced_product_ids"),
            case.get("expected_referenced_count_min"),
        )
    )
    results.append(
        planner_soft_preferences(plan, case.get("expected_soft_preferences_contains"))
    )

    # L2
    results.append(
        tool_sequence_match(
            tool_calls,
            case.get("expected_tool_calls_include"),
            case.get("expected_tool_calls_not_include"),
            case.get("expected_tool_calls_min"),
        )
    )
    results.append(top_k_contains_ids(cards, case.get("expected_top_5_product_ids_contains")))
    results.append(top_k_excludes_brands(cards, case.get("expected_top_5_excludes_brands")))
    results.append(no_match_check(cards, case.get("expected_no_match")))
    results.append(
        response_keyword_match(
            assistant_text,
            case.get("expected_response_must_contain_any"),
            case.get("expected_response_must_not_contain"),
        )
    )
    results.append(
        profile_unchanged(
            profile_before,
            profile_after,
            case.get("expected_profile_unchanged_fields"),
        )
    )
    results.append(cart_change_match(cart_after, case.get("expected_cart_change")))

    return results


__all__ = [
    "MetricResult",
    "planner_query_type",
    "planner_hard_constraints",
    "planner_referenced_ids",
    "planner_soft_preferences",
    "tool_sequence_match",
    "top_k_contains_ids",
    "top_k_excludes_brands",
    "no_match_check",
    "response_keyword_match",
    "profile_unchanged",
    "cart_change_match",
    "compute_all_metrics",
]
