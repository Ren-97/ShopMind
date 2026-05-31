"""FastAPI dependencies(§4.6.8 + §4.7)。

- `get_current_user`:`X-User-Id` header → user_id(V1 无 auth,默认 demo_user_1)
- `get_orchestrator`:进程内单例 — 装配 Embedder(含 LRU cache)+ Dispatcher
  + Reranker + ToolRegistry + SessionStore,构造一次后跨请求共享
- `get_session_factory`:暴露全局 AsyncSessionLocal 给 REST 路由直接用

CachedEmbedder 包装位置:**Orchestrator 单例内**(query 侧)。ingest 路径
另起原生 Embedder(`get_embedder()`),互不干扰。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import Header
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server import config
from server.agent.orchestrator import Orchestrator
from server.agent.session_state import SessionStateStore
from server.cache.in_memory import InMemoryLRUCache
from server.rag.embedders import get_embedder
from server.rag.embedders.cached import CachedEmbedder
from server.rag.retrieval.dispatcher import build_default_dispatcher
from server.rag.reranking.llm_reranker import LLMReranker
from server.rag.sparse import JiebaBM25Encoder
from server.storage.db import AsyncSessionLocal
from server.storage.vector_index import get_vector_index
from server.tools.base import AgentDeps
from server.tools import build_tool_registry


# ──────────────────────────────────────────────────────────────
# user_id 注入(§4.6.8)
# ──────────────────────────────────────────────────────────────
async def get_current_user(
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
) -> str:
    """从 `X-User-Id` header 取 user_id;缺失则用 config.DEFAULT_USER_ID。

    V1 不做认证。Header 校验放在 Pydantic / Repo 层(SQL WHERE user_id=? 已隔离)。
    """
    return (x_user_id or config.DEFAULT_USER_ID).strip() or config.DEFAULT_USER_ID


# ──────────────────────────────────────────────────────────────
# 单例:Orchestrator + AgentDeps
# ──────────────────────────────────────────────────────────────
@lru_cache(maxsize=1)
def _build_orchestrator() -> Orchestrator:
    """进程内一次性装配。Lazy:第一次请求或 startup 触发。"""
    # Embedding LRU(§4.8.3,无 TTL)
    embedding_cache = InMemoryLRUCache(maxsize=config.EMBEDDING_CACHE_SIZE, ttl=None)
    embedder = CachedEmbedder(get_embedder(), embedding_cache)

    sparse_encoder = JiebaBM25Encoder()
    vector_index = get_vector_index()

    # Retrieval LRU(§4.8.4,TTL=300s)在 dispatcher 内部建,这里传 None 让其自建
    dispatcher = build_default_dispatcher(
        session_factory=AsyncSessionLocal,
        vector_index=vector_index,
        embedder=embedder,
        sparse_encoder=sparse_encoder,
        cache=None,
    )

    reranker = LLMReranker(session_factory=AsyncSessionLocal)

    deps = AgentDeps(
        session_factory=AsyncSessionLocal,
        dispatcher=dispatcher,
        reranker=reranker,
        base_url=config.BASE_URL,
    )

    return Orchestrator(
        deps=deps,
        tool_registry=build_tool_registry(),
        session_store=SessionStateStore(),
    )


def get_orchestrator() -> Orchestrator:
    return _build_orchestrator()


# ──────────────────────────────────────────────────────────────
# REST 路由直接用的 session factory(不经 Orchestrator)
# ──────────────────────────────────────────────────────────────
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    return AsyncSessionLocal


__all__ = [
    "get_current_user",
    "get_orchestrator",
    "get_session_factory",
]
