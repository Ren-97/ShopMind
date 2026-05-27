"""RetrievalStrategy Protocol(§4.1.5)。

每个策略接收一个 QueryPlan,返回 RetrievalResult。
共享基础设施(catalog / vector_index / embedder / sparse)在策略构造时注入。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from server.domain.types import QueryPlan, RetrievalResult


@runtime_checkable
class RetrievalStrategy(Protocol):
    """单一职责:消费 QueryPlan → 返回 RetrievalResult。"""

    name: str  # observability(命中策略名写进 RetrievalResult.strategy)

    async def retrieve(self, plan: QueryPlan) -> RetrievalResult:
        ...
