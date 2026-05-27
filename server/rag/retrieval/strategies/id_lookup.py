"""ID Lookup 策略(§4.1.3):按 referenced_product_ids 直接取。

触发场景:Planner 识别出"那款"/"刚刚说的 A 和 B"等上下文指代时填充
`referenced_product_ids`。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server.domain.types import ProductHit, QueryPlan, RetrievalResult
from server.storage.catalog_repo import CatalogRepo


class IDLookupStrategy:
    """直接按 ID 取,跳过 embedding / Qdrant。"""

    name = "id_lookup"

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def retrieve(self, plan: QueryPlan) -> RetrievalResult:
        ids = plan.referenced_product_ids
        if not ids:
            return RetrievalResult(products=[], strategy=self.name)

        async with self._session_factory() as session:
            # 走 Repo 默认 is_active=TRUE — 已下架商品在这里就被过滤,
            # Agent 拿不到的商品由上层 Tool 转成"已下架"提示
            rows = await CatalogRepo.list_products_by_ids(session, ids)
            available = {r.product_id for r in rows}

        # 按用户提的顺序保留(referenced_product_ids 反映对话中的引用先后)
        products = [
            ProductHit(product_id=pid, score=1.0, matched_chunks=[])
            for pid in ids
            if pid in available
        ]
        return RetrievalResult(products=products, strategy=self.name)
