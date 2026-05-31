"""为 Embedder 加查询级 LRU 缓存(设计见 design.md §4.8.3)。

Why 只缓存 query:
- query 在多轮对话里高度重复,命中率高
- ingest 文本几乎无重复,缓存只占内存不命中

Key 设计:md5(底层 Embedder 类名 + text)
- 含类名:切 provider 不会命中旧维度向量
- 无 TTL:同一 model + text 永远映射同一向量
任意 Embedder Protocol 实现均可包装。
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from server.cache.protocol import Cache
from server.rag.embedders.protocol import Embedder


class CachedEmbedder:
    """Embedder 装饰器:`embed_query` 走 LRU,`embed_documents` 透传。"""

    def __init__(self, inner: Embedder, cache: Cache, *, key_prefix: str = "emb") -> None:
        self._inner = inner
        self._cache = cache
        self._prefix = key_prefix

    @property
    def dimension(self) -> int:
        return self._inner.dimension

    def _key(self, text: str) -> str:
        # 包模型类名进 key — 防止切换 provider 后命中错维度向量
        model_tag = type(self._inner).__name__
        h = hashlib.md5(f"{model_tag}|{text}".encode("utf-8")).hexdigest()
        return f"{self._prefix}:{h}"

    async def embed_query(self, text: str) -> list[float]:
        key = self._key(text)
        cached = self._cache.get(key)
        if cached is not None:
            return cached  # type: ignore[no-any-return]
        vector = await self._inner.embed_query(text)
        self._cache.set(key, vector)
        return vector

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        # 批量 ingest 不走缓存:每条几乎都不同,缓存只占内存不命中
        return await self._inner.embed_documents(texts)


__all__ = ["CachedEmbedder"]
