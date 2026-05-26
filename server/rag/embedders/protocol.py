"""Embedder Protocol(§4.1 第 5 原则:组件接口化)。

实现:
- `GeminiEmbedder`(V1 默认,`gemini-embedding-001`,3072 维,task_type 区分 doc / query)
- `OpenAIEmbedder`(备选 fallback,`text-embedding-3-large`,3072 维)

ingest 走 `embed_documents`,query 走 `embed_query`;Gemini 利用 task_type
显著提升检索质量,OpenAI 两方法内部走同一 API。
"""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable


@runtime_checkable
class Embedder(Protocol):
    """Dense 文本嵌入接口。"""

    dimension: int

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """批量编码"被检索的文档/chunk"。

        - 输入空列表 → 返回空列表
        - 调用方负责分片;实现内部再做 provider 级 batch 限制
        """
        ...

    async def embed_query(self, text: str) -> list[float]:
        """编码单条"用户查询"。"""
        ...
