"""Dense Embedder 抽象 + 实现 + factory(§4.5.2 Step 4)。

V1 默认 Gemini(`gemini-embedding-001`),`EMBEDDING_PROVIDER=openai` 可切换 fallback。
"""

from __future__ import annotations

from server import config
from server.rag.embedders.gemini_embedder import GeminiEmbedder
from server.rag.embedders.openai_embedder import OpenAIEmbedder
from server.rag.embedders.protocol import Embedder


def get_embedder() -> Embedder:
    """根据 `EMBEDDING_PROVIDER` 返回对应实现。"""
    provider = (config.EMBEDDING_PROVIDER or "gemini").lower()
    if provider == "gemini":
        return GeminiEmbedder()
    if provider == "openai":
        return OpenAIEmbedder()
    raise ValueError(
        f"未知 EMBEDDING_PROVIDER='{provider}',支持 'gemini' | 'openai'"
    )


__all__ = ["Embedder", "GeminiEmbedder", "OpenAIEmbedder", "get_embedder"]
