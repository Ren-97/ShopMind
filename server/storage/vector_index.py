"""
Qdrant 适配层(对应 docs/design.md §4.1.7 + §4.4.3 + §4.5)。

V1 使用 Qdrant 嵌入式模式(本地文件,无独立 server),通过 AsyncQdrantClient 异步访问。

Collection schema(单 collection,named vectors 同时存 dense + sparse):
- dense vector: name="dense", size=EMBEDDING_DIMENSION(3072), distance=COSINE
- sparse vector: name="sparse"(BM25 在 ingest 时本地编码后传入)

Chunk 1 范围:client 单例 + ensure_collection() 骨架。
真正的 upsert / hybrid search 在 Chunk 3 RAG 时填充。
"""

from __future__ import annotations

from qdrant_client import AsyncQdrantClient, models as qmodels

from server import config


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
