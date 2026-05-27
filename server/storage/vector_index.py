"""
Qdrant 适配层(对应 docs/design.md §4.1.7 + §4.1.9 + §4.4.3 + §4.5)。

V1 使用 Qdrant 嵌入式模式(本地文件,无独立 server),通过 AsyncQdrantClient 异步访问。

Collection schema(单 collection,named vectors 同时存 dense + sparse):
- dense vector: name="dense", size=EMBEDDING_DIMENSION(3072), distance=COSINE
- sparse vector: name="sparse"(BM25 在 ingest 时本地编码后传入)

职责:
- Collection 生命周期:ensure_collection / delete_by_product / count_points
- Hybrid 检索:dense + sparse prefetch + RRF 融合(query_points + FusionQuery)
"""

from __future__ import annotations

from collections.abc import Sequence

from qdrant_client import AsyncQdrantClient, models as qmodels

from server import config
from server.domain.types import ChunkHit
from server.rag.sparse.protocol import SparseVector


class VectorIndex:
    """Qdrant 嵌入式适配。"""

    def __init__(
        self,
        path: str | None = None,
        collection_name: str | None = None,
        dimension: int | None = None,
    ) -> None:
        self._path: str = path or str(config.QDRANT_PATH_ABS)
        self.collection_name: str = collection_name or config.QDRANT_COLLECTION_NAME
        self.dimension: int = dimension or config.EMBEDDING_DIMENSION
        self._client: AsyncQdrantClient | None = None

    @property
    def client(self) -> AsyncQdrantClient:
        if self._client is None:
            # 嵌入式模式:path= 本地目录,无 host
            self._client = AsyncQdrantClient(path=self._path)
        return self._client

    async def ensure_collection(self) -> None:
        """启动期幂等建 collection — 若不存在则创建。"""
        existing = await self.client.get_collections()
        names = {c.name for c in existing.collections}
        if self.collection_name in names:
            return

        await self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config={
                "dense": qmodels.VectorParams(
                    size=self.dimension,
                    distance=qmodels.Distance.COSINE,
                ),
            },
            sparse_vectors_config={
                "sparse": qmodels.SparseVectorParams(
                    index=qmodels.SparseIndexParams(on_disk=False),
                ),
            },
        )

    async def hybrid_search(
        self,
        *,
        dense_vector: list[float],
        sparse_vector: SparseVector,
        product_id_whitelist: Sequence[str] | None = None,
        dense_limit: int | None = None,
        sparse_limit: int | None = None,
        fused_limit: int | None = None,
    ) -> list[ChunkHit]:
        """Hybrid 检索(§4.1.9):dense + sparse 双路 prefetch → Qdrant 原生 RRF 融合。

        - `product_id_whitelist=None` → Pure Semantic(全库)
        - 非空列表 → Filtered Semantic(payload.product_id IN whitelist)
        - 空列表 `[]` → 直接返回 `[]`(SQL 过滤后无候选,跳过 Qdrant 调用)

        返回 ChunkHit 列表(已按 RRF 分排序,最多 `fused_limit` 条)。
        """
        if product_id_whitelist is not None and len(product_id_whitelist) == 0:
            return []

        d_limit = dense_limit if dense_limit is not None else config.RETRIEVAL_DENSE_LIMIT
        s_limit = sparse_limit if sparse_limit is not None else config.RETRIEVAL_SPARSE_LIMIT
        r_limit = fused_limit if fused_limit is not None else config.RETRIEVAL_RRF_LIMIT

        prefetch_filter: qmodels.Filter | None = None
        if product_id_whitelist is not None:
            prefetch_filter = qmodels.Filter(
                must=[
                    qmodels.FieldCondition(
                        key="product_id",
                        match=qmodels.MatchAny(any=list(product_id_whitelist)),
                    )
                ]
            )

        sparse_q = qmodels.SparseVector(
            indices=sparse_vector.indices, values=sparse_vector.values
        )

        resp = await self.client.query_points(
            collection_name=self.collection_name,
            prefetch=[
                qmodels.Prefetch(
                    query=dense_vector, using="dense",
                    limit=d_limit, filter=prefetch_filter,
                ),
                qmodels.Prefetch(
                    query=sparse_q, using="sparse",
                    limit=s_limit, filter=prefetch_filter,
                ),
            ],
            query=qmodels.FusionQuery(fusion=qmodels.Fusion.RRF),
            limit=r_limit,
            with_payload=True,
        )

        hits: list[ChunkHit] = []
        for p in resp.points:
            payload = dict(p.payload or {})
            hits.append(
                ChunkHit(
                    # Qdrant point id 由 ingest 时 uuid5(chunk_id) 派生 — 稳定 + 唯一,
                    # 业务侧用作 chunk 级 dedup / 引用句柄(MatchedChunk.chunk_id)
                    chunk_id=str(p.id),
                    score=float(p.score),
                    payload=payload,
                )
            )
        return hits

    async def delete_by_product(self, product_id: str) -> None:
        """删除某 product 的全部 chunks(ingest 全量重建该商品时用)。"""
        await self.client.delete(
            collection_name=self.collection_name,
            points_selector=qmodels.FilterSelector(
                filter=qmodels.Filter(
                    must=[
                        qmodels.FieldCondition(
                            key="product_id",
                            match=qmodels.MatchValue(value=product_id),
                        )
                    ]
                )
            ),
        )

    async def count_points(self) -> int:
        result = await self.client.count(
            collection_name=self.collection_name, exact=True
        )
        return int(result.count)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None


# ─── 模块级单例(lifespan 初始化时创建一次,业务全程共用)──
_global_index: VectorIndex | None = None


def get_vector_index() -> VectorIndex:
    """FastAPI Depends + 业务模块共用的 VectorIndex 单例。"""
    global _global_index
    if _global_index is None:
        _global_index = VectorIndex()
    return _global_index


__all__ = ["VectorIndex", "get_vector_index"]
