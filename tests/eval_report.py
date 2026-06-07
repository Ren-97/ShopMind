"""Eval 报告聚合 + markdown 渲染(纯函数,无网络 / 无 API,offline 可单测)。

输入是一批 `CaseEvalResult`(每个 case 的 metric 结果 + judge 结论 + 耗时),
输出是:
- `aggregate(...)` → `EvalSummary` 结构化汇总(通过率 / L4 时延分位 / 错误数)
- `render_markdown(...)` → 一整篇答辩/调参用的 markdown 报告

把"跑批"和"出报告"解耦:eval.py 跑完把结果丢进来,这里只做纯计算和排版,
所以不需要 key 也能用合成数据测排版对不对。
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from tests.eval_judge import JudgeVerdict
from tests.eval_metrics import MetricResult


@dataclass(slots=True)
class CaseEvalResult:
    """一个 case 跑完 + 判分完的完整结论(喂给报告)。"""

    case_id: str
    layers: list[int]
    tags: list[str]
    metrics: list[MetricResult]
    judge: list[JudgeVerdict] = field(default_factory=list)
    elapsed_ms: int = 0
    error: str | None = None
    # 报告可读性 / 答辩佐证用(self-contained:单看 md 就懂在测什么)
    scenario: str = ""
    query: str = ""
    assistant_text: str = ""


@dataclass(slots=True)
class EvalSummary:
    total_cases: int
    errored_cases: int
    # metric 维度(跳过的不计入分母)
    metrics_passed: int
    metrics_evaluated: int
    # judge 维度(skipped 的不计入分母)
    judge_passed: int
    judge_evaluated: int
    judge_skipped: bool  # 是否整批都没真正判(没配 key)
    # L4 时延(ms)
    latency_p50: float
    latency_p95: float
    latency_max: float

    @property
    def metric_pass_rate(self) -> float:
        return self.metrics_passed / self.metrics_evaluated if self.metrics_evaluated else 0.0

    @property
    def judge_pass_rate(self) -> float:
        return self.judge_passed / self.judge_evaluated if self.judge_evaluated else 0.0


def _percentile(values: list[float], pct: float) -> float:
    """简单分位(线性,空集返回 0)。pct ∈ [0,100]。"""
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def aggregate(results: list[CaseEvalResult]) -> EvalSummary:
    """把一批 case 结论聚合成总览数字。"""
    metrics_passed = metrics_evaluated = 0
    judge_passed = judge_evaluated = 0
    judge_any_real = False
    latencies: list[float] = []
    errored = 0

    for r in results:
        if r.error:
            errored += 1
        if r.elapsed_ms:
            latencies.append(float(r.elapsed_ms))
        for m in r.metrics:
            if m.skipped:
                continue
            metrics_evaluated += 1
            if m.passed:
                metrics_passed += 1
        for v in r.judge:
            if v.skipped:
                continue
            judge_any_real = True
            judge_evaluated += 1
            if v.passed:
                judge_passed += 1

    return EvalSummary(
        total_cases=len(results),
        errored_cases=errored,
        metrics_passed=metrics_passed,
        metrics_evaluated=metrics_evaluated,
        judge_passed=judge_passed,
        judge_evaluated=judge_evaluated,
        judge_skipped=not judge_any_real,
        latency_p50=_percentile(latencies, 50),
        latency_p95=_percentile(latencies, 95),
        latency_max=max(latencies) if latencies else 0.0,
    )


def render_markdown(results: list[CaseEvalResult], summary: EvalSummary) -> str:
    """渲染完整报告。"""
    lines: list[str] = []
    lines.append("# ShopMind Eval 报告\n")

    # ── 总览 ──
    lines.append("## 总览\n")
    lines.append(f"- 用例数:**{summary.total_cases}**(报错 {summary.errored_cases})")
    lines.append(
        f"- L1/L2 metric 通过率:**{summary.metrics_passed}/{summary.metrics_evaluated}** "
        f"({summary.metric_pass_rate:.0%})  _(skipped 的断言不计入分母)_"
    )
    if summary.judge_skipped:
        lines.append("- L3 judge(豆包):**未运行**(没配 ARK_API_KEY)")
    else:
        lines.append(
            f"- L3 judge 通过率:**{summary.judge_passed}/{summary.judge_evaluated}** "
            f"({summary.judge_pass_rate:.0%})"
        )
    lines.append(
        f"- L4 时延(ms):p50 **{summary.latency_p50:.0f}** / "
        f"p95 **{summary.latency_p95:.0f}** / max **{summary.latency_max:.0f}**\n"
    )

    # ── 逐 case 明细 ──
    lines.append("## 逐用例明细\n")
    lines.append("| case | 场景 | layers | metric (过/总) | judge | 耗时ms | 失败明细 |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in results:
        evaluated = [m for m in r.metrics if not m.skipped]
        passed = sum(1 for m in evaluated if m.passed)
        metric_cell = f"{passed}/{len(evaluated)}" if evaluated else "—"

        real_judge = [v for v in r.judge if not v.skipped]
        if not r.judge:
            judge_cell = "—"
        elif not real_judge:
            judge_cell = "skip"
        else:
            jp = sum(1 for v in real_judge if v.passed)
            judge_cell = f"{jp}/{len(real_judge)}"

        fails: list[str] = []
        if r.error:
            fails.append(f"ERROR: {r.error}")
        fails += [f"{m.name}: {m.detail}" for m in evaluated if not m.passed]
        fails += [f"judge/{v.dimension}: {v.reason}" for v in real_judge if not v.passed]
        fail_cell = "<br>".join(fails) if fails else "✅"

        status = "❌" if (fails or r.error) else "✅"
        lines.append(
            f"| {status} {r.case_id} | {r.scenario} | {r.layers} | {metric_cell} "
            f"| {judge_cell} | {r.elapsed_ms} | {fail_cell} |"
        )

    # ── L3 judge 详情(完整佐证单元:问→答→豆包判定,过/不过都列)──
    # 答辩拿这段给评委:用户问什么、AI 怎么答、跨模型(豆包)判定有没有幻觉 + 理由。
    detail_lines: list[str] = []
    for r in results:
        real_judge = [v for v in r.judge if not v.skipped]
        if not real_judge:
            continue
        title = f"\n**{r.case_id}**" + (f" — {r.scenario}" if r.scenario else "")
        detail_lines.append(title)
        if r.query:
            detail_lines.append(f"- 问:{r.query}")
        if r.assistant_text:
            reply = r.assistant_text.replace("\n", " ").strip()
            if len(reply) > 160:
                reply = reply[:160] + "…"
            detail_lines.append(f"- 答:{reply}")
        for v in real_judge:
            mark = "✅" if v.passed else "❌"
            detail_lines.append(f"- 豆包判定 {mark} {v.dimension}:{v.reason}")
    if detail_lines:
        lines.append("\n## L3 judge 详情(豆包跨模型评估)")
        lines.extend(detail_lines)

    return "\n".join(lines) + "\n"


__all__ = ["CaseEvalResult", "EvalSummary", "aggregate", "render_markdown", "_percentile"]
