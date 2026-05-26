"""OpenAI Embedder(`text-embedding-3-large`, 3072 维)。

- 异步:用 AsyncOpenAI 客户端,与 FastAPI / ingest 主流程一致
- 批处理:单次最多 `EMBEDDING_BATCH_SIZE`(防 OpenAI request size 限制)
- SDK 已内置重试,不再手写 retry 循环(对齐 CLAUDE.md §5)
"""

from __future__ import annotations

from collections.abc import Sequence

from openai import AsyncOpenAI

from server import config


class OpenAIEmbedder:
    """`text-embedding-3-large` 的 async 实现。"""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        dimension: int | None = None,
        batch_size: int | None = None,
    ) -> None:
        key = api_key if api_key is not None else config.OPENAI_API_KEY
        if not key:
            raise RuntimeError(
                "OPENAI_API_KEY 未配置,无法初始化 OpenAIEmbedder。"
                "在项目根目录创建 .env 并填入 OPENAI_API_KEY。"
            )
        self._client = AsyncOpenAI(
            api_key=key,
            base_url=base_url if base_url is not None else config.OPENAI_BASE_URL,
        )
        self.model: str = model or config.EMBEDDING_MODEL
        self.dimension: int = dimension or config.EMBEDDING_DIMENSION
        self._batch_size: int = batch_size or config.EMBEDDING_BATCH_SIZE

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        out: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = list(texts[start : start + self._batch_size])
            resp = await self._client.embeddings.create(model=self.model, input=batch)
            out.extend([d.embedding for d in resp.data])
        return out
