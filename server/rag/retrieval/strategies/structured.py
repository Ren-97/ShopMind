"""Structured 策略(§4.1.3):纯 SQL,无 embedding。

触发场景:用户给出纯属性筛选意图(无语义诉求)。
例:"查一下还有什么资生堂面霜",或"500 以内的洗面奶"。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server import config
from server.domain.types import ProductHit, QueryPlan, RetrievalResult
from server.storage.catalog_repo import CatalogRepo


class StructuredStrategy:
    """仅 SQL — Repo 层硬过滤,product_id 即结果。无 matched_chunks。"""

    name = "structured"

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        top_n: int | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._top_n = top_n or config.RETRIEVAL_PRODUCT_TOP_N

    async def retrieve(self, plan: QueryPlan) -> RetrievalResult:
        async with self._session_factory() as session:
            ids = await CatalogRepo.list_product_ids_by_constraints(
                session, plan.hard_constraints, limit=self._top_n
            )
        # 无语义信号 → 同分(后续 Rerank 用 matched_chunks=空 也能跑,只是无证据)
        products = [ProductHit(product_id=pid, score=1.0, matched_chunks=[]) for pid in ids]
        return RetrievalResult(products=products, strategy=self.name)
