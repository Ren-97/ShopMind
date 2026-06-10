"""Pure Semantic 策略(§4.1.3):无约束 Hybrid。

触发场景:抽象语义诉求,无可结构化的约束。
例:"想要个能让我夜班撑得住的护肤品"、"送女朋友礼物"。
"""

from __future__ import annotations

import asyncio

from server import config
from server.domain.types import QueryPlan, RetrievalResult
from server.rag.embedders.protocol import Embedder
from server.rag.retrieval.aggregation import aggregate_chunks_to_products
from server.rag.sparse.protocol import SparseEncoder
from server.storage.vector_index import VectorIndex


class PureSemanticStrategy:
    name = "pure_semantic"

    def __init__(
        self,
        *,
        vector_index: VectorIndex,
        embedder: Embedder,
        sparse_encoder: SparseEncoder,
        top_n: int | None = None,
        coarse_threshold: float | None = None,
    ) -> None:
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
        text = plan.text_query
        if not text:
            # Pure Semantic 没有 text 没法做,返回空走 no_match
            return RetrievalResult(products=[], strategy=self.name)

        # dense + sparse 并行:dense 是网络往返、sparse 是本地 BM25,两者独立
        dense_vec, sparse_vecs = await asyncio.gather(
            self._embedder.embed_query(text),
            self._sparse_encoder.encode([text]),
        )
        sparse_vec = sparse_vecs[0]

        chunks = await self._vector_index.hybrid_search(
            dense_vector=dense_vec,
            sparse_vector=sparse_vec,
            product_id_whitelist=None,  # 全库
        )

        products = aggregate_chunks_to_products(
            chunks, top_n=self._top_n, coarse_threshold=self._coarse_threshold
        )
        return RetrievalResult(products=products, strategy=self.name)
