"""search_products tool(§4.6.1 + §4.6.7)。

完整链路:
  user_query → Planner → Dispatcher → Reranker → DB enrich → ProductSummary

防幻觉链路:
  - Planner 把硬约束抽到 hard_constraints,Dispatcher 走 SQL hard filter(铁律 1)
  - Reranker 阈值过滤 < RERANK_THRESHOLD → 返回空(no_match,铁律 2)
  - 全字段从 DB enrich(铁律 3),LLM 只能复述

确定性结果旁路:判据是 **hits 带不带 matched_chunks**。
  - **空**(id_lookup / structured / filtered_semantic 在 text_query 为空退化时)
    = 纯 SQL 确定性命中,候选即答案 → 跳过 LLM rerank 的打分/阈值,直接全字段 enrich
    (取数同 compare_products)。rerank 的阈值是给"模糊语义召回"做相关性裁决的(铁律 2),
    确定性结果没有"不相关"可丢,硬打分反而误伤(代词/俗称当 query 被刷掉)。
  - **非空**(filtered_semantic 带 text / pure_semantic)= 语义召回 → 正常走 rerank。
  两条路都从 DB 全字段 enrich,铁律 3(facts 走 DB)始终满足。
"""

from __future__ import annotations

from typing import Any, ClassVar

import structlog
from pydantic import BaseModel, ConfigDict, Field

from server.domain.types import ProductHit, QueryPlan
from server.llm.planner import PlannerError, plan_query
from server.llm.reranker import RerankerError
from server.rag.retrieval.dispatcher import RetrievalError
from server.storage.catalog_repo import CatalogRepo
from server.tools._serializers import (
    product_card,
    product_summary_from_db,
    product_summary_from_ranked,
)
from server.tools.base import AgentDeps, Tool, ToolError, ToolResult

log = structlog.get_logger("shopmind.tools.search")


def _relax_plan(plan: QueryPlan, query: str) -> QueryPlan:
    """放宽 plan:摘掉 Planner 猜的开放类目(category/sub_category — 唯一会写错/挑错的硬约束),
    其余约束(price/brand/闭集 gender/suitable_skin...)原样保留,原 query 落 text_query 走语义召回。

    清掉类目后若 hard_constraints 全空,Dispatcher 自动降级 pure_semantic;否则 filtered_semantic。
    """
    relaxed_constraints = plan.hard_constraints.model_copy(
        update={"category": None, "sub_category": None}
    )
    return plan.model_copy(
        update={
            "query_type": "filtered_semantic",
            "hard_constraints": relaxed_constraints,
            "text_query": plan.text_query or query,
        }
    )


def _is_relaxed(plan: QueryPlan) -> bool:
    """已经是"无类目 + 有 text_query"形态 → 不必再放宽(防止无谓重跑)。"""
    return (
        plan.hard_constraints.category is None
        and plan.hard_constraints.sub_category is None
        and plan.text_query is not None
    )


class SearchProductsInput(BaseModel):
    """Claude 看到的 schema(不含 user_id)。"""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(
        description=(
            "用户的检索 query。可以是商品名、属性描述、场景需求。"
            "举例:'敏感肌洗面奶'、'500 元以内 iPhone 配件'、'三亚海边穿搭'。"
        )
    )


class SearchProductsTool(Tool):
    name: ClassVar[str] = "search_products"
    description: ClassVar[str] = (
        "搜商品。走完整 Adaptive Retrieval(Planner → Dispatcher → Reranker → DB enrich),"
        "返回 0-5 个最相关的商品(`products` 字段)。返回空 = no_match(诚实告知用户),"
        "**不允许** 因为想推荐就调多次。"
    )
    input_model: ClassVar[type[BaseModel]] = SearchProductsInput

    async def _run(
        self,
        *,
        user_id: str,
        deps: AgentDeps,
        validated_input: BaseModel,
    ) -> ToolResult:
        assert isinstance(validated_input, SearchProductsInput)
        query = validated_input.query.strip()
        if not query:
            raise ToolError("query 不能为空")

        # 1) Planner — 透传 profile + session(指代消解走 id_lookup,§4.2 上下文使用)
        try:
            plan = await plan_query(
                query,
                profile=deps.user_profile,
                session_state=deps.session_snapshot,
            )
        except PlannerError as e:
            log.warning("search_planner_failed", error=str(e))
            raise ToolError(f"无法理解 query:{e}") from e

        # 2) Dispatcher(adaptive retrieval)
        try:
            retrieval_result = await deps.dispatcher.dispatch(plan)
        except RetrievalError as e:
            log.warning("search_retrieval_failed", error=str(e))
            raise ToolError(f"检索失败:{e}") from e

        hits: list[ProductHit] = retrieval_result.products

        # 0 命中兜底(query relaxation):Planner 猜的 category/sub_category 是开放字符串,
        # 会写错(如"牛奶"→"牡奶")或挑错,进 SQL 精确硬过滤就把结果清零。这里**只在 0 命中时**
        # 摘掉这俩开放类目、保留用户明说/闭集的约束(price/brand/gender/suitable_skin...),
        # 用原 query 走语义召回再兜一次。铁律不破:候选仍是真实 DB 商品,且后面 rerank 阈值照常裁决。
        if not hits and not _is_relaxed(plan):
            relaxed = _relax_plan(plan, query)
            try:
                retrieval_result = await deps.dispatcher.dispatch(relaxed)
                hits = retrieval_result.products
            except RetrievalError as e:
                log.warning("search_relaxed_retrieval_failed", error=str(e))
            if hits:
                log.info(
                    "search_relaxed_recovered",
                    query=query,
                    strategy=retrieval_result.strategy,
                    n_hits=len(hits),
                )

        if not hits:
            return ToolResult(
                payload={
                    "products": [],
                    "strategy": retrieval_result.strategy,
                    "no_match": True,
                },
                meta={"plan": plan.model_dump(mode="json")},
            )

        # 2.5) 确定性结果旁路:hits 无 matched_chunks = 纯 SQL 命中(id_lookup /
        # structured / filtered_semantic 退化),候选即答案 → 只 enrich,不打分不阈值
        # (否则确定性命中被 Haiku 阈值误伤,吐 spurious no_match)。详见模块 docstring。
        if all(not h.matched_chunks for h in hits):
            return await self._present_deterministic(
                hits, deps=deps, plan=plan, query=query, strategy=retrieval_result.strategy
            )

        # 3) Reranker(LLM Haiku;阈值过滤 + DB enrich)
        try:
            ranked = await deps.reranker.rerank(query, hits)
        except RerankerError as e:
            log.warning("search_reranker_failed", error=str(e))
            raise ToolError(f"重排失败:{e}") from e

        if not ranked:
            # 铁律 2:阈值兜底,不硬推不相关商品
            return ToolResult(
                payload={
                    "products": [],
                    "strategy": retrieval_result.strategy,
                    "no_match": True,
                },
                meta={"plan": plan.model_dump(mode="json")},
            )

        # 4) 序列化:LLM payload(全字段)+ SSE card(lean)
        product_payloads: list[dict[str, Any]] = [
            product_summary_from_ranked(r, base_url=deps.base_url) for r in ranked
        ]
        cards: list[dict[str, Any]] = [product_card(p) for p in product_payloads]

        log.info(
            "search_done",
            query=query,
            strategy=retrieval_result.strategy,
            n_returned=len(product_payloads),
        )
        return ToolResult(
            payload={
                "products": product_payloads,
                "strategy": retrieval_result.strategy,
                "no_match": False,
            },
            cards=cards,
            meta={"plan": plan.model_dump(mode="json")},
        )

    async def _present_deterministic(
        self,
        hits: list[ProductHit],
        *,
        deps: AgentDeps,
        plan: Any,
        query: str,
        strategy: str,
    ) -> ToolResult:
        """确定性结果旁路:按 hit 顺序(= SQL / 引用顺序)从 DB 全字段 enrich,不走 rerank。

        hits 已经过策略层 `is_active=TRUE` 过滤;若 enrich 全落空(下架)→ no_match。
        """
        async with deps.session_factory() as session:
            products = []
            for hit in hits:
                p = await CatalogRepo.get_product_with_details(session, hit.product_id)
                if p is not None:
                    products.append(p)

        if not products:
            return ToolResult(
                payload={"products": [], "strategy": strategy, "no_match": True},
                meta={"plan": plan.model_dump(mode="json")},
            )

        product_payloads = [
            product_summary_from_db(p, base_url=deps.base_url) for p in products
        ]
        cards = [product_card(p) for p in product_payloads]
        log.info(
            "search_done",
            query=query,
            strategy=strategy,
            n_returned=len(product_payloads),
            reranked=False,
        )
        return ToolResult(
            payload={
                "products": product_payloads,
                "strategy": strategy,
                "no_match": False,
            },
            cards=cards,
            meta={"plan": plan.model_dump(mode="json")},
        )


__all__ = ["SearchProductsTool"]
