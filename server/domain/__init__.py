"""共享 Pydantic 类型(QueryPlan / RetrievalResult / ProductHit 等)。

放在 `server/domain/` 而非各模块内,是因为 Planner(§4.2)/ Retrieval(§4.1)/
Reranker(§4.3)/ Agent(§4.6)都要引用,集中一处避免循环依赖 + schema 漂移。
"""

from server.domain.types import (
    ChunkHit,
    HardConstraints,
    MatchedChunk,
    ProductHit,
    QueryPlan,
    QueryType,
    RetrievalResult,
)

__all__ = [
    "ChunkHit",
    "HardConstraints",
    "MatchedChunk",
    "ProductHit",
    "QueryPlan",
    "QueryType",
    "RetrievalResult",
]
