"""search_products tool(§4.6.1 + §4.6.7)。

完整链路:
  user_query → Planner → Dispatcher → Reranker → DB enrich → ProductSummary

防幻觉链路:
  - Planner 把硬约束抽到 hard_constraints,Dispatcher 走 SQL hard filter(铁律 1)
  - Reranker 阈值过滤 < RERANK_THRESHOLD → 返回空(no_match,铁律 2)
  - 全字段从 DB enrich(铁律 3),LLM 只能复述
"""

from __future__ import annotations

from typing import Any, ClassVar

import structlog
from pydantic import BaseModel, ConfigDict, Field

from server.domain.types import ProductHit
from server.llm.planner import PlannerError, plan_query
from server.llm.reranker import RerankerError
from server.rag.retrieval.dispatcher import RetrievalError
from server.tools._serializers import product_card, product_summary_from_ranked
from server.tools.base import AgentDeps, Tool, ToolError, ToolResult

log = structlog.get_logger("shopmind.tools.search")


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

        # 1) Planner
        try:
            plan = await plan_query(query)
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
        if not hits:
            return ToolResult(
                payload={
                    "products": [],
                    "strategy": retrieval_result.strategy,
                    "no_match": True,
                },
                meta={"plan": plan.model_dump(mode="json")},
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


__all__ = ["SearchProductsTool"]
