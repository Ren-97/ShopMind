"""Eval 入口(CLAUDE.md §7:`python tests/eval.py`)。

把四层串起来跑一批,出 markdown 报告:
  EvalRunner(跑 case,L1+L2,收事件+耗时)
    → compute_all_metrics(L1+L2 断言判分)
    → judge_case(L3 豆包,配了 ARK_API_KEY 才真跑)
    → aggregate + render_markdown(L4 时延 + 总览 + 逐 case)

跑法:
  python tests/eval.py                       # 全部 case
  python tests/eval.py --layers 1            # 只跑 L1(Planner,最便宜:不调检索/agent loop)
  python tests/eval.py --cases basic_beauty_sensitive,no_match_absurd
  python tests/eval.py --tags hallucination  # 只跑某类
  python tests/eval.py --no-judge            # 强制跳过 L3(即便配了 key)
  python tests/eval.py --out tests/eval_report.md

依赖:
- L1/L2 需 ANTHROPIC_API_KEY + 起好 Postgres/Qdrant + 跑过 ingest/seed_users(真烧 Claude)
- L3 需 ARK_API_KEY(没配则报告里标 "L3 未运行",不报错)
- 想"不烧 key 验证框架本身对不对" → 跑 `pytest tests/test_eval_offline.py`(纯函数,合成数据)
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# 允许 `python tests/eval.py` 直接跑(把项目根加进 sys.path)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests.eval_judge import judge_case  # noqa: E402
from tests.eval_metrics import compute_all_metrics  # noqa: E402
from tests.eval_report import (  # noqa: E402
    CaseEvalResult,
    aggregate,
    render_markdown,
)
from tests.eval_runner import EvalRunner  # noqa: E402


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="ShopMind eval")
    p.add_argument("--layers", type=str, default=None, help="逗号分隔,如 1 或 1,2")
    p.add_argument("--cases", type=str, default=None, help="逗号分隔 case id")
    p.add_argument("--tags", type=str, default=None, help="逗号分隔 tag")
    p.add_argument("--no-judge", action="store_true", help="强制跳过 L3 judge")
    p.add_argument("--out", type=str, default="tests/eval_report.md", help="报告输出路径")
    return p.parse_args()


def _split(s: str | None) -> list[str] | None:
    return [x.strip() for x in s.split(",") if x.strip()] if s else None


async def run(args: argparse.Namespace) -> int:
    runner = EvalRunner()
    await runner.setup()

    layers = [int(x) for x in (_split(args.layers) or [])] or None
    cases = runner.list_cases(
        tags=_split(args.tags),
        case_ids=_split(args.cases),
        layers=layers,
    )
    if not cases:
        print("[eval] 没有匹配的 case")
        return 1

    print(f"[eval] 跑 {len(cases)} 个 case…")
    results: list[CaseEvalResult] = []
    for case in cases:
        run_result = await runner.run_case(case)
        metrics = compute_all_metrics(
            case,
            plan=run_result.plan,
            tool_calls=run_result.tool_calls,
            cards=run_result.cards,
            assistant_text=run_result.assistant_text,
            profile_before=run_result.profile_before,
            profile_after=run_result.profile_after,
            cart_after=run_result.cart_after,
        )
        # L3 judge 的真相源 = Agent 实际拿到的 ProductSummary(search/compare 的 payload),
        # 不是 lean card,也不是 dataset 全集。过滤掉报错 payload。
        product_facts = [
            pl
            for name, pl in run_result.tool_payloads
            if name in ("search_products", "compare_products")
            and isinstance(pl, dict)
            and not pl.get("error")
        ]
        judge = (
            []
            if args.no_judge
            else await judge_case(
                case,
                assistant_text=run_result.assistant_text,
                product_facts=product_facts,
            )
        )
        results.append(
            CaseEvalResult(
                case_id=run_result.case_id,
                layers=case.get("layers", []),
                tags=case.get("tags", []),
                metrics=metrics,
                judge=judge,
                elapsed_ms=run_result.elapsed_ms,
                error=run_result.error,
                scenario=case.get("scenario", ""),
                query=run_result.query,
                assistant_text=run_result.assistant_text,
            )
        )
        passed = sum(1 for m in metrics if m.passed and not m.skipped)
        total = sum(1 for m in metrics if not m.skipped)
        flag = "✅" if (not run_result.error and passed == total) else "❌"
        print(f"  {flag} {run_result.case_id}: {passed}/{total} ({run_result.elapsed_ms}ms)")

    await runner.teardown()

    summary = aggregate(results)
    report = render_markdown(results, summary)
    out_path = _PROJECT_ROOT / args.out
    out_path.write_text(report, encoding="utf-8")

    print(
        f"\n[eval] metric 通过率 {summary.metrics_passed}/{summary.metrics_evaluated} "
        f"({summary.metric_pass_rate:.0%}) | 报告 → {out_path}"
    )
    return 0


def main() -> None:
    args = _parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
