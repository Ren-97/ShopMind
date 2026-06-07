"""Eval 框架的 offline 自测(纯函数,**不需要任何 API key / DB / Qdrant**)。

目的:在不烧 Claude/豆包的前提下,验证"判分逻辑 + 报告渲染"本身写对了 ——
用手造的 QueryPlan / 卡片 / judge 返回喂进纯函数,断言结果符合预期。

跑法:
  uv run pytest tests/test_eval_offline.py -v

这是开发期的主力测试:改了 metric/judge/report 的逻辑,先跑这个确认不回归,
再去花钱跑真实的 `python tests/eval.py`。
"""

from __future__ import annotations

from server.domain.types import HardConstraints, QueryPlan
from tests.eval_judge import (
    build_judge_prompt,
    parse_judge_response,
    requested_dimensions,
)
from tests.eval_metrics import (
    compute_all_metrics,
    planner_hard_constraints,
    planner_soft_preferences,
)
from tests.eval_report import CaseEvalResult, aggregate, render_markdown
from tests.eval_judge import JudgeVerdict
from tests.eval_metrics import MetricResult


def _plan(**kw) -> QueryPlan:
    """便捷构造 QueryPlan,hard/soft 可传 dict。"""
    hc = HardConstraints(**kw.pop("hard", {}))
    return QueryPlan(hard_constraints=hc, **kw)


# ──────────────────────────────────────────────────────────────────────
# soft_preferences metric(新增)
# ──────────────────────────────────────────────────────────────────────
def test_soft_pref_substring_match():
    """expected 保湿 命中 actual 长效保湿(双向子串,宽松)。"""
    plan = _plan(query_type="filtered_semantic", soft_preferences={"effects": ["长效保湿"]})
    r = planner_soft_preferences(plan, {"effects": ["保湿"]})
    assert r.passed, r.detail


def test_soft_pref_missing_key_fails():
    plan = _plan(query_type="filtered_semantic", soft_preferences={"scene": ["送礼"]})
    r = planner_soft_preferences(plan, {"effects": ["保湿"]})
    assert not r.passed and "缺 key" in r.detail


def test_soft_pref_no_expectation_skips():
    plan = _plan(query_type="filtered_semantic")
    r = planner_soft_preferences(plan, None)
    assert r.skipped


# ──────────────────────────────────────────────────────────────────────
# hard_constraints exact 收紧:多余字段(如 gender 泄漏)必须被抓到
# ──────────────────────────────────────────────────────────────────────
def test_exact_catches_leaked_gender():
    """这是 Case 3 想守的:Planner 误把画像 gender 渗进 hard → 必须 FAIL。"""
    plan = _plan(
        query_type="filtered_semantic",
        hard={"category": "服饰运动", "sub_category": "跑步鞋", "gender": "男"},
    )
    expected = {"category": "服饰运动", "sub_category": "跑步鞋"}
    r = planner_hard_constraints(plan, expected, None)
    assert not r.passed and "gender" in r.detail


def test_exact_clean_passes():
    plan = _plan(
        query_type="filtered_semantic",
        hard={"category": "服饰运动", "sub_category": "跑步鞋"},
    )
    expected = {"category": "服饰运动", "sub_category": "跑步鞋"}
    r = planner_hard_constraints(plan, expected, None)
    assert r.passed, r.detail


def test_exact_list_order_insensitive():
    plan = _plan(query_type="filtered_semantic", hard={"brand_exclude": ["阿迪达斯", "Nike"]})
    r = planner_hard_constraints(plan, {"brand_exclude": ["Nike", "阿迪达斯"]}, None)
    assert r.passed, r.detail


def test_contains_ignores_extra_fields():
    """部分匹配分支不收紧:多余字段不算错。"""
    plan = _plan(query_type="filtered_semantic", hard={"price_max": 500.0, "category": "美妆护肤"})
    r = planner_hard_constraints(plan, None, {"price_max": 500.0})
    assert r.passed, r.detail


# ──────────────────────────────────────────────────────────────────────
# compute_all_metrics 端到端(合成 case + plan)
# ──────────────────────────────────────────────────────────────────────
def test_compute_all_metrics_smoke():
    case = {
        "expected_query_type": "filtered_semantic",
        "expected_hard_constraints": {"category": "美妆护肤", "sub_category": "精华", "price_max": 500.0},
        "expected_soft_preferences_contains": {"effects": ["保湿"]},
        "expected_tool_calls_include": ["search_products"],
    }
    plan = _plan(
        query_type="filtered_semantic",
        hard={"category": "美妆护肤", "sub_category": "精华", "price_max": 500.0},
        soft_preferences={"effects": ["保湿"]},
    )
    results = compute_all_metrics(
        case,
        plan=plan,
        tool_calls=[("search_products", {})],
        cards=[],
        assistant_text="给你推荐这款精华。",
        profile_before={},
        profile_after={},
        cart_after=[],
    )
    evaluated = [m for m in results if not m.skipped]
    assert all(m.passed for m in evaluated), [m.detail for m in evaluated if not m.passed]


# ──────────────────────────────────────────────────────────────────────
# L3 judge 纯函数(prompt 构造 + 响应解析,不打网络)
# ──────────────────────────────────────────────────────────────────────
def test_requested_dimensions_filters_unknown():
    assert requested_dimensions({"judge_focus": ["faithfulness", "bogus"]}) == ["faithfulness"]
    assert requested_dimensions({}) == []


def test_build_judge_prompt_contains_facts():
    prompt = build_judge_prompt(
        query="这款有 200ml 吗",
        assistant_text="只有 30/50/75ml 三个规格。",
        product_facts=[{"product_id": "p_beauty_001", "skus": [{"properties": {"容量": "30ml"}}]}],
        dimensions=["faithfulness"],
    )
    assert "30ml" in prompt and "faithfulness" in prompt


def test_build_judge_prompt_empty_facts_warns():
    """没检索到商品 → prompt 要提示'声称商品存在即幻觉',让 judge 抓 no_match 类硬编。"""
    prompt = build_judge_prompt(
        query="推荐 Yeti 保温杯",
        assistant_text="为你推荐 Yeti 经典款。",
        product_facts=[],
        dimensions=["faithfulness"],
    )
    assert "幻觉" in prompt


def test_parse_judge_response_ok():
    raw = '这是判定结果:{"verdicts": [{"dimension": "faithfulness", "pass": true, "reason": "有依据"}]}'
    v = parse_judge_response(raw, ["faithfulness"])
    assert len(v) == 1 and v[0].passed


def test_parse_judge_response_missing_dim_fails():
    """judge 没返回某维度 → 记 fail(不达标),不静默放过。"""
    v = parse_judge_response('{"verdicts": []}', ["source_attribution"])
    assert len(v) == 1 and not v[0].passed


def test_parse_judge_garbage_fails_gracefully():
    v = parse_judge_response("模型抽风没给 json", ["faithfulness"])
    assert len(v) == 1 and not v[0].passed


# ──────────────────────────────────────────────────────────────────────
# 报告聚合 + 渲染(合成 CaseEvalResult)
# ──────────────────────────────────────────────────────────────────────
def _case_result(case_id: str, ok: bool, ms: int, judge_skip: bool = True) -> CaseEvalResult:
    metrics = [
        MetricResult("planner_query_type", ok, "..."),
        MetricResult("no_match", True, "...", skipped=True),  # skipped 不进分母
    ]
    judge = [JudgeVerdict("faithfulness", True, "skip", skipped=judge_skip)]
    return CaseEvalResult(case_id, [1, 2], ["core"], metrics, judge, ms)


def test_aggregate_pass_rate_excludes_skipped():
    results = [_case_result("a", True, 100), _case_result("b", False, 300)]
    s = aggregate(results)
    # 每 case 只有 1 个非 skipped metric → 分母 2,通过 1
    assert s.metrics_evaluated == 2 and s.metrics_passed == 1
    assert s.judge_skipped is True  # judge 全 skipped → 整批视为未真跑


def test_aggregate_latency_percentiles():
    results = [_case_result(f"c{i}", True, ms) for i, ms in enumerate([100, 200, 300, 400])]
    s = aggregate(results)
    assert s.latency_max == 400.0
    assert 100 <= s.latency_p50 <= 400


def test_render_markdown_nonempty():
    results = [_case_result("a", True, 100), _case_result("b", False, 300)]
    s = aggregate(results)
    md = render_markdown(results, s)
    assert "ShopMind Eval 报告" in md
    assert "❌ b" in md and "✅ a" in md
    assert "L3 judge(豆包):**未运行**" in md  # 没真跑 judge 时的措辞
    assert "L3 judge 详情" not in md  # judge 全 skip → 不出详情段


def test_render_shows_passing_judge_reason():
    """judge 真跑了 → 详情段要列出理由,即便 pass。"""
    metrics = [MetricResult("planner_query_type", True, "ok")]
    judge = [
        JudgeVerdict("faithfulness", True, "所有事实均有依据", skipped=False),
        JudgeVerdict("source_attribution", False, "未标注评论来源", skipped=False),
    ]
    results = [
        CaseEvalResult(
            "c", [2, 3], ["core"], metrics, judge, 200,
            scenario="来源标注", query="推荐一款防晒霜", assistant_text="给你推荐这款。",
        )
    ]
    md = render_markdown(results, aggregate(results))
    assert "## L3 judge 详情" in md
    assert "问:推荐一款防晒霜" in md
    assert "答:给你推荐这款。" in md
    assert "豆包判定 ✅ faithfulness:所有事实均有依据" in md
    assert "豆包判定 ❌ source_attribution:未标注评论来源" in md
