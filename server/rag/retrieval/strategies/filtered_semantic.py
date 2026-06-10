"""Filtered Semantic 策略(§4.1.3,主力 + 默认兜底)。

流程:
  1. SQL hard_constraints → product_id 白名单(防幻觉铁律 1)
  2. dense + sparse 编码 query
  3. Qdrant query_points + RRF,prefetch 带 product_id MatchAny filter
  4. 应用层 group by product_id → max score → top_n

边缘退化:
- 白名单为空(SQL 没命中) → 直接返回空(no_match,§4.2.7 检索层)
- text_query 为空 → 退化为 Structured(只走 SQL,无语义)
"""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server import config
from server.domain.types import ProductHit, QueryPlan, RetrievalResult
from server.rag.embedders.protocol import Embedder
from server.rag.retrieval.aggregation import aggregate_chunks_to_products
from server.rag.sparse.protocol import SparseEncoder
from server.storage.catalog_repo import CatalogRepo
from server.storage.vector_index import VectorIndex


class FilteredSemanticStrategy:
    name = "filtered_semantic"

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        vector_index: VectorIndex,
        embedder: Embedder,
        sparse_encoder: SparseEncoder,
        top_n: int | None = None,
        coarse_threshold: float | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._vector_index = vector_index
        self._embedder = embedder
        self._sparse_encoder = sparse_encoder
        self._top_n = top_n or config.RETRIEVAL_PRODUCT_TOP_N
        self._coarse_threshold = (
            coarse_threshold
            if coarse_threshold is not None
            else config.COARSE_THRESHOLD
        )

    async def retrieve(self, plan: QueryPlan) -> RetrievalResult:
        # ── 1) SQL 硬过滤 ──
        async with self._session_factory() as session:
            id_whitelist = await CatalogRepo.list_product_ids_by_constraints(
                session, plan.hard_constraints
            )

        if not id_whitelist:
            return RetrievalResult(products=[], strategy=self.name)

        # text_query 为空 → 退化为 Structured(纯 SQL,同分)
        text = plan.text_query
        if not text:
            products = [
                ProductHit(product_id=pid, score=1.0, matched_chunks=[])
                for pid in id_whitelist[: self._top_n]
            ]
            return RetrievalResult(products=products, strategy=self.name)

        # ── 2) 编码 query(dense + sparse 并行) ──
        # dense 是 embedding provider 网络往返、sparse 是本地 BM25,两者独立 → gather 并发
        dense_vec, sparse_vecs = await asyncio.gather(
            self._embedder.embed_query(text),
            self._sparse_encoder.encode([text]),
        )
        sparse_vec = sparse_vecs[0]

        # ── 3) Hybrid 检索(Qdrant 原生 RRF + 白名单 filter) ──
        chunks = await self._vector_index.hybrid_search(
            dense_vector=dense_vec,
            sparse_vector=sparse_vec,
            product_id_whitelist=id_whitelist,
        )

        # ── 4) 聚合到 product ──
        products = aggregate_chunks_to_products(
            chunks, top_n=self._top_n, coarse_threshold=self._coarse_threshold
        )
        return RetrievalResult(products=products, strategy=self.name)
