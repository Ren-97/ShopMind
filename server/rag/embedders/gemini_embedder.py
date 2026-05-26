"""Gemini Embedder(`gemini-embedding-001`, 3072 维)— V1 默认实现。

亮点:
- `task_type` 区分 RETRIEVAL_DOCUMENT(ingest 时)/ RETRIEVAL_QUERY(检索时),
  同段文本两种模式向量略不同,显著提升 retrieval 质量
- Matryoshka 维度可配(768/1536/3072);默认 3072,跟 Qdrant collection 对齐
- Gemini embed_content 单次最多 100 条文本(2025-01 限制),实现内部分片
"""

from __future__ import annotations

from collections.abc import Sequence

from google import genai
from google.genai import types as genai_types

from server import config


class GeminiEmbedder:
    """`gemini-embedding-001` 的 async 实现。"""

    # Gemini embed_content 单 request 最多 100 条
    _PROVIDER_BATCH_LIMIT = 100

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        dimension: int | None = None,
        batch_size: int | None = None,
    ) -> None:
        key = api_key if api_key is not None else config.GEMINI_API_KEY
        if not key:
            raise RuntimeError(
                "GEMINI_API_KEY 未配置,无法初始化 GeminiEmbedder。"
                "在项目根目录的 .env 填入 GEMINI_API_KEY。"
            )
        self._client = genai.Client(api_key=key)
        self.model: str = model or config.GEMINI_EMBEDDING_MODEL
        self.dimension: int = dimension or config.EMBEDDING_DIMENSION
        requested = batch_size or config.EMBEDDING_BATCH_SIZE
        self._batch_size: int = min(requested, self._PROVIDER_BATCH_LIMIT)

    async def _embed_with_task(
        self, texts: Sequence[str], task_type: str
    ) -> list[list[float]]:
        if not texts:
            return []
        out: list[list[float]] = []
        cfg = genai_types.EmbedContentConfig(
            task_type=task_type,
            output_dimensionality=self.dimension,
        )
        for start in range(0, len(texts), self._batch_size):
            batch = list(texts[start : start + self._batch_size])
            resp = await self._client.aio.models.embed_content(
                model=self.model, contents=batch, config=cfg
            )
            out.extend([e.values for e in resp.embeddings])
        return out

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return await self._embed_with_task(texts, task_type="RETRIEVAL_DOCUMENT")

    async def embed_query(self, text: str) -> list[float]:
        result = await self._embed_with_task([text], task_type="RETRIEVAL_QUERY")
        return result[0]
