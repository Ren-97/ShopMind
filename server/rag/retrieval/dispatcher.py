"""RetrievalDispatcher(§4.1.4)。

派发规则:
1. plan.confidence < 0.7 → 强制走 default(filtered_semantic),用户无感知
2. filtered_semantic 但 hard_constraints 全空 → 进一步退化为 pure_semantic
   (design §4.1.4 "filter 为空时进一步退化为 Pure Semantic")
3. strategies[plan.query_type] 不存在 → default
4. 策略执行抛异常 → default 再试一次,仍失败 → 抛 RetrievalError 由上游兜底

Retrieval Cache(§4.8.4):
- key = retrieval:{md5(QueryPlan.model_dump_json(sort=True))}
- ttl 300s
- 命中直接返回,绕开策略执行
"""

from __future__ import annotations

import hashlib
from typing import Mapping

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server import config
from server.cache.in_memory import InMemoryLRUCache
from server.cache.noop import NoopCache
from server.cache.protocol import Cache
from server.domain.types import QueryPlan, RetrievalResult
from server.rag.embedders.protocol import Embedder
from server.rag.retrieval.strategies.base import RetrievalStrategy
from server.rag.sparse.protocol import SparseEncoder
from server.storage.vector_index import VectorIndex

log = structlog.get_logger("shopmind.retrieval")


# 置信度低于该阈值 → 强制走 default 策略(§4.1.4)
_CONFIDENCE_FLOOR: float = 0.7


class RetrievalError(RuntimeError):
    """检索 Hard Fail — default 策略也炸了。上游转兜底文案。"""


class RetrievalDispatcher:
    """按 query_type 派发到策略,带兜底 + retrieval cache。"""

    def __init__(
        self,
        strategies: Mapping[str, RetrievalStrategy],
        *,
        default_strategy: str = "filtered_semantic",
        cache: Cache | None = None,
    ) -> None:
        if default_strategy not in strategies:
            raise ValueError(f"default_strategy '{default_strategy}' 不在 strategies 中")
        self._strategies: dict[str, RetrievalStrategy] = dict(strategies)
        self._default: str = default_strategy
        self._cache: Cache = cache if cache is not None else NoopCache()

    def _cache_key(self, plan: QueryPlan) -> str:
        # Pydantic v2 model_dump_json 默认按字段定义顺序输出,稳定可复现
        raw = plan.model_dump_json()
        return f"retrieval:{hashlib.md5(raw.encode('utf-8')).hexdigest()}"

    def _resolve_strategy_name(self, plan: QueryPlan) -> str:
        """决定最终走哪个策略(§4.1.4 + design §4.1.4 末尾的 filter 空退化)。"""
        # 1) 低置信 → default
        if plan.confidence < _CONFIDENCE_FLOOR:
            return self._default
        # 2) Filtered Semantic 但 filter 全空 → 退化为 Pure Semantic
        if (
            plan.query_type == "filtered_semantic"
            and plan.hard_constraints.is_empty()
            and "pure_semantic" in self._strategies
        ):
            return "pure_semantic"
        # 3) 未知 query_type → default
        if plan.query_type not in self._strategies:
            return self._default
        return plan.query_type

    async def dispatch(self, plan: QueryPlan) -> RetrievalResult:
        cache_key = self._cache_key(plan)
        cached = self._cache.get(cache_key)
        if cached is not None:
            log.info(
                "retrieval_cache_hit",
                query_type=plan.query_type,
                confidence=plan.confidence,
            )
            return cached  # type: ignore[no-any-return]

        chosen = self._resolve_strategy_name(plan)
        strategy = self._strategies[chosen]
        try:
            result = await strategy.retrieve(plan)
        except Exception as e:
            log.error(
                "retrieval_strategy_failed",
                strategy=chosen,
                error=str(e),
                exc_info=True,
            )
            # 已经在 default 上炸了 → 不再循环重试,抛 RetrievalError
            if chosen == self._default:
                raise RetrievalError(f"default 策略 '{self._default}' 失败: {e}") from e
            # 用 default 再试一次
            try:
                result = await self._strategies[self._default].retrieve(plan)
            except Exception as e2:
                log.error(
                    "retrieval_default_failed",
                    default=self._default,
                    error=str(e2),
                    exc_info=True,
                )
                raise RetrievalError(
                    f"策略 '{chosen}' 失败 + default '{self._default}' 失败"
                ) from e2

        log.info(
            "retrieval_ok",
            requested=plan.query_type,
            chosen=chosen,
            confidence=plan.confidence,
            n_products=len(result.products),
        )

        self._cache.set(cache_key, result, ttl=config.RETRIEVAL_CACHE_TTL_SECONDS)
        return result


# ─────────────────────────────────────────────────────────────
# Factory:把现成基础设施组装成一个 Dispatcher
# ─────────────────────────────────────────────────────────────
def build_default_dispatcher(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    vector_index: VectorIndex,
    embedder: Embedder,
    sparse_encoder: SparseEncoder,
    cache: Cache | None = None,
) -> RetrievalDispatcher:
    """业务入口:四策略 + retrieval LRU 缓存。

    cache=None → 默认起一个 InMemoryLRUCache(maxsize, ttl=300)。传 NoopCache 可关。
    """
    from server.rag.retrieval.strategies.filtered_semantic import FilteredSemanticStrategy
    from server.rag.retrieval.strategies.id_lookup import IDLookupStrategy
    from server.rag.retrieval.strategies.pure_semantic import PureSemanticStrategy
    from server.rag.retrieval.strategies.structured import StructuredStrategy

    strategies = {
        "structured": StructuredStrategy(session_factory=session_factory),
        "id_lookup": IDLookupStrategy(session_factory=session_factory),
        "filtered_semantic": FilteredSemanticStrategy(
            session_factory=session_factory,
            vector_index=vector_index,
            embedder=embedder,
            sparse_encoder=sparse_encoder,
        ),
        "pure_semantic": PureSemanticStrategy(
            vector_index=vector_index,
            embedder=embedder,
            sparse_encoder=sparse_encoder,
        ),
    }
    if cache is None:
        cache = InMemoryLRUCache(
            maxsize=config.RETRIEVAL_CACHE_SIZE,
            ttl=config.RETRIEVAL_CACHE_TTL_SECONDS,
        )
    return RetrievalDispatcher(
        strategies=strategies, default_strategy="filtered_semantic", cache=cache
    )


__all__ = ["RetrievalDispatcher", "RetrievalError", "build_default_dispatcher"]
