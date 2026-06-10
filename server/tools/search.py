"""search_products tool(§4.6.1 + §4.6.7)。

完整链路:
  user_query → Planner → Dispatcher → Reranker → DB enrich → ProductSummary

防幻觉链路:
  - Planner 把硬约束抽到 hard_constraints,Dispatcher 走 SQL hard filter(铁律 1)
  - Reranker 阈值过滤 < RERANK_THRESHOLD → 返回空(no_match,铁律 2)
  - 全字段从 DB enrich(铁律 3),LLM 只能复述

排除约束(反选)三来源统一在 `_apply_exclusions` 代码层合并进 hard_constraints
(当前 query brand_exclude + session.rejected_brands + profile.brand_exclude +
origin_exclude 展开),不再单独依赖 Planner / prompt 把它们抓全。出 card 前再过一道
确定性 `_drop_excluded` guard:card 由工具 payload 直接 emit、Agent 删不掉,所以被
排除的商品必须在进 payload 前就剔干净(全剔光 → no_match,诚实告知而非硬塞 + 嘴上道歉)。

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
from server.indexing.brand_aliases import normalize_brand
from server.indexing.brand_origins import brands_for_origins
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


def _apply_exclusions(
    plan: QueryPlan,
    *,
    profile: dict[str, Any] | None,
    session_snapshot: dict[str, Any] | None,
) -> tuple[QueryPlan, set[str]]:
    """把所有反选来源**代码层**合并进 hard_constraints.brand_exclude,并返回归一化后的
    被排除品牌集(供出 card 前的 guard 复用)。

    四个来源(去重、normalize 别名,最终都落到 SQL `brand NOT IN`):
    1. 当前 query 的 `brand_exclude`(Planner 抽,"不要 Nike")
    2. 当前 query 的 `origin_exclude`(Planner 抽闭集"日系"→ brand_origins 展开成具体品牌)
    3. session `rejected_brands`(本 session 累积的拒绝,代码兜底——不再只靠 prompt 让 LLM 并)
    4. profile `preferences.brand_exclude`(画像里长期厌恶的品牌)

    其余画像软偏好(肤质 / 品牌偏好 / 年龄 / 性别 / 护肤诉求…)**不在这里**——由 reranker
    的确定性 fit 重排(`_profile_fit_boost`)在阈值过滤后做 ±微调,是软偏好的唯一 owner。
    """
    prefs = (profile or {}).get("preferences") or {}
    snap = session_snapshot or {}

    raw: list[str] = [
        *plan.hard_constraints.brand_exclude,
        *brands_for_origins(plan.hard_constraints.origin_exclude),
        *(snap.get("rejected_brands") or []),
        *(prefs.get("brand_exclude") or []),
    ]
    # normalize 别名(Nike→耐克)+ 去重保序
    excluded: list[str] = list(
        dict.fromkeys(normalize_brand(b) for b in raw if b)
    )

    # **当前 query 明确点名要某品牌 → 本轮放行它**,盖过长期(profile)/临时(session)的排除。
    # 例:profile 长期不买 Nike,但这次"买 Nike 的鞋送朋友" → 本轮不能把 Nike 排掉
    #(否则 SQL 出现 `brand=耐克 AND brand NOT IN(耐克)` → 自相矛盾零结果)。
    # "送朋友"这类代购意图本就不会写进本人 profile,所以这里只需放行、不动档案。
    requested = normalize_brand(plan.hard_constraints.brand)
    if requested:
        excluded = [b for b in excluded if b != requested]

    if not excluded:
        return plan, set()

    hc = plan.hard_constraints.model_copy(update={"brand_exclude": excluded})
    return plan.model_copy(update={"hard_constraints": hc}), set(excluded)


def _drop_excluded(
    payloads: list[dict[str, Any]],
    *,
    excluded_brands: set[str],
    plan: QueryPlan,
) -> list[dict[str, Any]]:
    """出 card 前的确定性 guard:剔掉任何违反排除约束的商品。

    SQL 硬过滤理论上已经挡掉这些,但 card 由工具 payload 直接 emit、Agent 无法删卡——
    这是"被排除商品绝不进 card"的最后一道代码保证(防任何上游路径漏过)。检查:
    - 品牌 ∈ excluded_brands(normalize 后比较)
    - 属性硬约束(contains_alcohol / contains_fragrance):商品值与约束不符即剔
      (字段缺失视为不符,从严——本就不该通过 SQL @> 到这里)
    """
    hc = plan.hard_constraints
    kept: list[dict[str, Any]] = []
    for p in payloads:
        if excluded_brands and normalize_brand(p.get("brand")) in excluded_brands:
            continue
        props = p.get("properties") or {}
        if hc.contains_alcohol is not None and props.get("contains_alcohol") != hc.contains_alcohol:
            continue
        if hc.contains_fragrance is not None and props.get("contains_fragrance") != hc.contains_fragrance:
            continue
        kept.append(p)
    return kept


def _no_match(plan: QueryPlan, strategy: str) -> ToolResult:
    """统一的 no_match 返回(空 products + meta 带 plan,供上游兜底文案)。"""
    return ToolResult(
        payload={"products": [], "strategy": strategy, "no_match": True},
        meta={"plan": plan.model_dump(mode="json")},
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

        # 1) Planner — 只透传 session(指代消解走 id_lookup,§4.2 上下文使用)。
        #    **不传 profile**:画像不参与检索 plan —— 硬排除由 _apply_exclusions 代码合并,
        #    软偏好由 reranker 的 _profile_fit_boost 独占,Planner 不再两头折叠(职责单一)。
        try:
            plan = await plan_query(
                query,
                session_state=deps.session_snapshot,
            )
        except PlannerError as e:
            log.warning("search_planner_failed", error=str(e))
            raise ToolError(f"无法理解 query:{e}") from e

        # 1.5) 统一合并所有反选来源(query / origin / session.rejected / profile)→ hard 过滤
        plan, excluded_brands = _apply_exclusions(
            plan, profile=deps.user_profile, session_snapshot=deps.session_snapshot
        )

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
            return _no_match(plan, retrieval_result.strategy)

        # 2.5) 确定性结果旁路:hits 无 matched_chunks = 纯 SQL 命中(id_lookup /
        # structured / filtered_semantic 退化),候选即答案 → 只 enrich,不打分不阈值
        # (否则确定性命中被 Haiku 阈值误伤,吐 spurious no_match)。详见模块 docstring。
        if all(not h.matched_chunks for h in hits):
            return await self._present_deterministic(
                hits,
                deps=deps,
                plan=plan,
                query=query,
                strategy=retrieval_result.strategy,
                excluded_brands=excluded_brands,
            )

        # 3) Reranker(LLM Haiku;阈值过滤 + DB enrich)
        # rerank query 只评 query↔商品相关性;画像个性化由 reranker 内部的确定性 fit 重排(profile)
        # 在阈值过滤**之后**做 ±微调,绝不淘汰(§4.3)—— 浏览型 query 不会被收成 1 个。
        try:
            ranked = await deps.reranker.rerank(
                plan.text_query or query, hits, profile=deps.user_profile
            )
        except RerankerError as e:
            log.warning("search_reranker_failed", error=str(e))
            raise ToolError(f"重排失败:{e}") from e

        if not ranked:
            # 铁律 2:阈值兜底,不硬推不相关商品
            return _no_match(plan, retrieval_result.strategy)

        # 4) 序列化:LLM payload(全字段)+ SSE card(lean)
        product_payloads: list[dict[str, Any]] = [
            product_summary_from_ranked(r, base_url=deps.base_url) for r in ranked
        ]
        # 出 card 前 guard:被排除的商品绝不进 payload(全剔光 → 诚实 no_match)
        product_payloads = _drop_excluded(
            product_payloads, excluded_brands=excluded_brands, plan=plan
        )
        if not product_payloads:
            return _no_match(plan, retrieval_result.strategy)
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
        excluded_brands: set[str],
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
            return _no_match(plan, strategy)

        product_payloads = [
            product_summary_from_db(p, base_url=deps.base_url) for p in products
        ]
        # 出 card 前 guard:确定性旁路同样要剔被排除项(全剔光 → no_match)
        product_payloads = _drop_excluded(
            product_payloads, excluded_brands=excluded_brands, plan=plan
        )
        if not product_payloads:
            return _no_match(plan, strategy)
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
