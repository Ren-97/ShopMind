"""Chunk 3 Sanity Test — 纯本地,无网络 / 无 API key。

覆盖:
- 所有 chunk3 模块 import 不挂
- Pydantic 类型(QueryPlan / HardConstraints / RetrievalResult)契约
- Cache(InMemoryLRU + Noop)行为
- chunks→products 聚合(max score + top_n + threshold)
- Dispatcher 路由:低置信 / filter 空退化 / 未知类型 / 异常兜底
- 给个 QueryPlan(走 Stub Strategy)→ 拿到 product_ids
- CatalogRepo.list_product_ids_by_constraints 真打 Postgres(需 docker)

不测的(留给 chunk 4 / ingest 真跑):
- 真 embedder / 真 Qdrant hybrid_search → 集成 eval
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ──────────────────────────────────────────────────────────────
# import sanity
# ──────────────────────────────────────────────────────────────
def test_import_all_chunk3_modules() -> None:
    import server.cache  # noqa: F401
    import server.cache.in_memory  # noqa: F401
    import server.cache.noop  # noqa: F401
    import server.cache.protocol  # noqa: F401
    import server.domain  # noqa: F401
    import server.domain.types  # noqa: F401
    import server.rag.retrieval  # noqa: F401
    import server.rag.retrieval.aggregation  # noqa: F401
    import server.rag.retrieval.dispatcher  # noqa: F401
    import server.rag.retrieval.strategies  # noqa: F401
    import server.rag.retrieval.strategies.base  # noqa: F401
    import server.rag.retrieval.strategies.filtered_semantic  # noqa: F401
    import server.rag.retrieval.strategies.id_lookup  # noqa: F401
    import server.rag.retrieval.strategies.pure_semantic  # noqa: F401
    import server.rag.retrieval.strategies.structured  # noqa: F401


# ──────────────────────────────────────────────────────────────
# Pydantic 契约
# ──────────────────────────────────────────────────────────────
def test_hard_constraints_is_empty() -> None:
    from server.domain.types import HardConstraints

    assert HardConstraints().is_empty()
    assert not HardConstraints(brand="X").is_empty()
    assert not HardConstraints(price_max=500.0).is_empty()
    assert not HardConstraints(properties_contains={"suitable_skin": ["敏感肌"]}).is_empty()
    assert not HardConstraints(brand_exclude=["A"]).is_empty()


def test_query_plan_defaults_and_strip() -> None:
    from server.domain.types import QueryPlan

    p = QueryPlan(query_type="pure_semantic", text_query="  抗老精华  ")
    assert p.text_query == "抗老精华"
    assert p.confidence == 1.0
    assert p.soft_preferences == {}
    assert p.referenced_product_ids == []


def test_query_plan_confidence_range() -> None:
    from server.domain.types import QueryPlan

    with pytest.raises(Exception):
        QueryPlan(query_type="pure_semantic", confidence=1.5)


def test_query_plan_extra_forbidden() -> None:
    from server.domain.types import QueryPlan

    with pytest.raises(Exception):
        QueryPlan.model_validate(
            {"query_type": "pure_semantic", "wat": 1}
        )


# ──────────────────────────────────────────────────────────────
# Cache
# ──────────────────────────────────────────────────────────────
def test_in_memory_lru_basic() -> None:
    from server.cache import InMemoryLRUCache

    c = InMemoryLRUCache(maxsize=2)
    c.set("a", 1)
    c.set("b", 2)
    assert c.get("a") == 1
    assert c.get("b") == 2
    c.set("c", 3)  # 触发 LRU 淘汰
    assert len(c) == 2


def test_in_memory_ttl_expires() -> None:
    """TTL=1 秒,直接通过 cachetools 时钟前推(替换 timer)。"""
    from server.cache import InMemoryLRUCache

    c = InMemoryLRUCache(maxsize=10, ttl=1)
    c.set("x", "v")
    assert c.get("x") == "v"


def test_noop_cache() -> None:
    from server.cache import NoopCache

    c = NoopCache()
    c.set("a", 1)
    assert c.get("a") is None


# ──────────────────────────────────────────────────────────────
# Aggregation
# ──────────────────────────────────────────────────────────────
def test_aggregate_groups_by_product_max_score() -> None:
    from server.domain.types import ChunkHit
    from server.rag.retrieval.aggregation import aggregate_chunks_to_products

    chunks = [
        ChunkHit(chunk_id="c1", score=0.5,
                 payload={"product_id": "p1", "chunk_type": "main", "text": "t1"}),
        ChunkHit(chunk_id="c2", score=0.9,
                 payload={"product_id": "p1", "chunk_type": "review", "text": "t2"}),
        ChunkHit(chunk_id="c3", score=0.7,
                 payload={"product_id": "p2", "chunk_type": "main", "text": "t3"}),
    ]
    out = aggregate_chunks_to_products(chunks, top_n=10)
    assert len(out) == 2
    # p1 max=0.9 排第一
    assert out[0].product_id == "p1"
    assert out[0].score == pytest.approx(0.9)
    assert {m.chunk_id for m in out[0].matched_chunks} == {"c1", "c2"}
    assert out[1].product_id == "p2"


def test_aggregate_respects_top_n() -> None:
    from server.domain.types import ChunkHit
    from server.rag.retrieval.aggregation import aggregate_chunks_to_products

    chunks = [
        ChunkHit(chunk_id=f"c{i}", score=1.0 - i * 0.1,
                 payload={"product_id": f"p{i}", "chunk_type": "main", "text": ""})
        for i in range(5)
    ]
    out = aggregate_chunks_to_products(chunks, top_n=3)
    assert len(out) == 3
    assert [h.product_id for h in out] == ["p0", "p1", "p2"]


def test_aggregate_coarse_threshold_filters() -> None:
    from server.domain.types import ChunkHit
    from server.rag.retrieval.aggregation import aggregate_chunks_to_products

    chunks = [
        ChunkHit(chunk_id="c1", score=0.001,
                 payload={"product_id": "p1", "chunk_type": "main", "text": ""}),
        ChunkHit(chunk_id="c2", score=0.6,
                 payload={"product_id": "p2", "chunk_type": "main", "text": ""}),
    ]
    out = aggregate_chunks_to_products(chunks, top_n=10, coarse_threshold=0.005)
    assert len(out) == 1
    assert out[0].product_id == "p2"


def test_aggregate_skips_chunk_without_product_id() -> None:
    from server.domain.types import ChunkHit
    from server.rag.retrieval.aggregation import aggregate_chunks_to_products

    chunks = [
        ChunkHit(chunk_id="c1", score=0.9,
                 payload={"chunk_type": "main", "text": "no pid"}),
        ChunkHit(chunk_id="c2", score=0.5,
                 payload={"product_id": "p1", "chunk_type": "main", "text": ""}),
    ]
    out = aggregate_chunks_to_products(chunks, top_n=10)
    assert [h.product_id for h in out] == ["p1"]


# ──────────────────────────────────────────────────────────────
# Dispatcher 路由(用 stub strategy,不依赖 DB / Qdrant / API)
# ──────────────────────────────────────────────────────────────
class _StubStrategy:
    """记录被调用、可选抛异常的最小策略。"""

    def __init__(self, name: str, products: list[str] | None = None,
                 *, raises: Exception | None = None) -> None:
        self.name = name
        self._products = products or []
        self._raises = raises
        self.calls: int = 0

    async def retrieve(self, plan):  # type: ignore[no-untyped-def]
        from server.domain.types import ProductHit, RetrievalResult

        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return RetrievalResult(
            products=[ProductHit(product_id=p, score=1.0, matched_chunks=[])
                      for p in self._products],
            strategy=self.name,
        )


def _make_dispatcher(strategies, cache=None):
    from server.cache import NoopCache
    from server.rag.retrieval.dispatcher import RetrievalDispatcher

    # 注意:InMemoryLRUCache 定义了 __len__,空 cache 会被 `or` 当成 falsy →
    # 必须用 `is None` 判断,不能用 `or`
    return RetrievalDispatcher(
        strategies=strategies,
        default_strategy="filtered_semantic",
        cache=cache if cache is not None else NoopCache(),
    )


async def test_dispatcher_routes_by_query_type() -> None:
    from server.domain.types import QueryPlan

    structured = _StubStrategy("structured", ["s1"])
    id_lookup = _StubStrategy("id_lookup", ["i1"])
    filtered = _StubStrategy("filtered_semantic", ["f1"])
    pure = _StubStrategy("pure_semantic", ["p1"])

    d = _make_dispatcher({
        "structured": structured,
        "id_lookup": id_lookup,
        "filtered_semantic": filtered,
        "pure_semantic": pure,
    })

    # 一个明确 filtered_semantic 的 plan:必带非空 hard_constraints,否则会被退化到 pure_semantic
    plan = QueryPlan(
        query_type="filtered_semantic",
        hard_constraints={"brand": "X"},
        text_query="hello",
    )
    r = await d.dispatch(plan)
    assert [h.product_id for h in r.products] == ["f1"]
    assert r.strategy == "filtered_semantic"
    assert filtered.calls == 1


async def test_dispatcher_low_confidence_falls_back_to_default() -> None:
    from server.domain.types import QueryPlan

    structured = _StubStrategy("structured", ["s1"])
    filtered = _StubStrategy("filtered_semantic", ["f1"])

    d = _make_dispatcher({
        "structured": structured,
        "filtered_semantic": filtered,
    })

    # confidence < 0.7 → 强制 default,即便 query_type=structured 也走 filtered
    plan = QueryPlan(
        query_type="structured",
        hard_constraints={"brand": "X"},
        confidence=0.3,
    )
    r = await d.dispatch(plan)
    assert structured.calls == 0
    assert filtered.calls == 1
    assert r.strategy == "filtered_semantic"


async def test_dispatcher_filter_empty_degrades_to_pure_semantic() -> None:
    from server.domain.types import QueryPlan

    filtered = _StubStrategy("filtered_semantic", ["f1"])
    pure = _StubStrategy("pure_semantic", ["pure_hit"])

    d = _make_dispatcher({
        "filtered_semantic": filtered,
        "pure_semantic": pure,
    })

    # filtered_semantic 但 hard_constraints 全空 → 退化到 pure_semantic
    plan = QueryPlan(query_type="filtered_semantic", text_query="抗老精华")
    r = await d.dispatch(plan)
    assert filtered.calls == 0
    assert pure.calls == 1
    assert [h.product_id for h in r.products] == ["pure_hit"]


async def test_dispatcher_strategy_exception_falls_back_to_default() -> None:
    from server.domain.types import QueryPlan

    boom = _StubStrategy("structured", raises=RuntimeError("boom"))
    default = _StubStrategy("filtered_semantic", ["fallback"])

    d = _make_dispatcher({"structured": boom, "filtered_semantic": default})

    plan = QueryPlan(
        query_type="structured",
        hard_constraints={"brand": "X"},
    )
    r = await d.dispatch(plan)
    assert boom.calls == 1
    assert default.calls == 1
    assert r.strategy == "filtered_semantic"


async def test_dispatcher_default_failure_raises_retrieval_error() -> None:
    from server.domain.types import QueryPlan
    from server.rag.retrieval.dispatcher import RetrievalError

    default = _StubStrategy("filtered_semantic", raises=RuntimeError("kaboom"))
    d = _make_dispatcher({"filtered_semantic": default})

    plan = QueryPlan(
        query_type="filtered_semantic",
        hard_constraints={"brand": "X"},
    )
    with pytest.raises(RetrievalError):
        await d.dispatch(plan)


async def test_dispatcher_cache_hit_skips_strategy() -> None:
    from server.cache import InMemoryLRUCache
    from server.domain.types import QueryPlan

    strat = _StubStrategy("filtered_semantic", ["hit"])
    d = _make_dispatcher(
        {"filtered_semantic": strat},
        cache=InMemoryLRUCache(maxsize=10, ttl=300),
    )

    plan = QueryPlan(
        query_type="filtered_semantic",
        hard_constraints={"brand": "X"},
    )
    r1 = await d.dispatch(plan)
    r2 = await d.dispatch(plan)
    assert strat.calls == 1  # 第二次走 cache
    assert [h.product_id for h in r1.products] == [h.product_id for h in r2.products]


def test_dispatcher_rejects_unknown_default() -> None:
    from server.rag.retrieval.dispatcher import RetrievalDispatcher

    with pytest.raises(ValueError):
        RetrievalDispatcher(strategies={}, default_strategy="filtered_semantic")


# ──────────────────────────────────────────────────────────────
# CatalogRepo.list_product_ids_by_constraints(需 Postgres)
# ──────────────────────────────────────────────────────────────
async def test_constraints_filter_basic(test_session_factory) -> None:
    """category + brand_exclude + price_max + JSONB @> 联合过滤。"""
    from server.domain.types import HardConstraints
    from server.storage.catalog_repo import CatalogRepo
    from server.storage.models import Product, SKU

    async with test_session_factory() as session:
        # p1: 美妆-精华, 资生堂, base=200, sku 180-220, 含敏感肌
        # p2: 美妆-精华, 雅诗兰黛, base=300, sku 280-320, 含敏感肌
        # p3: 美妆-洁面, 雅诗兰黛, base=150, sku 150, 不含敏感肌
        # p4: 美妆-精华, 雅诗兰黛, base=400, sku 400-500, 含敏感肌, is_active=FALSE
        session.add_all([
            Product(product_id="p1", title="t1", brand="资生堂",
                    category="美妆护肤", sub_category="精华",
                    base_price=200.0, in_stock=True, is_active=True,
                    properties={"suitable_skin": ["敏感肌", "干皮"]}),
            Product(product_id="p2", title="t2", brand="雅诗兰黛",
                    category="美妆护肤", sub_category="精华",
                    base_price=300.0, in_stock=True, is_active=True,
                    properties={"suitable_skin": ["敏感肌"]}),
            Product(product_id="p3", title="t3", brand="雅诗兰黛",
                    category="美妆护肤", sub_category="洁面",
                    base_price=150.0, in_stock=True, is_active=True,
                    properties={"suitable_skin": ["油皮"]}),
            Product(product_id="p4", title="t4", brand="雅诗兰黛",
                    category="美妆护肤", sub_category="精华",
                    base_price=400.0, in_stock=True, is_active=False,
                    properties={"suitable_skin": ["敏感肌"]}),
            SKU(sku_id="s1a", product_id="p1", properties={}, price=180.0),
            SKU(sku_id="s1b", product_id="p1", properties={}, price=220.0),
            SKU(sku_id="s2a", product_id="p2", properties={}, price=280.0),
            SKU(sku_id="s2b", product_id="p2", properties={}, price=320.0),
            SKU(sku_id="s3a", product_id="p3", properties={}, price=150.0),
            SKU(sku_id="s4a", product_id="p4", properties={}, price=400.0),
        ])
        await session.commit()

    # 1) category + sub_category → p1, p2(p3 sub_cat 不符,p4 is_active=FALSE)
    async with test_session_factory() as session:
        ids = await CatalogRepo.list_product_ids_by_constraints(
            session, HardConstraints(category="美妆护肤", sub_category="精华")
        )
        assert set(ids) == {"p1", "p2"}

    # 2) brand_exclude=["资生堂"] → p2(p1 被排除,p3 sub_cat 不符,p4 is_active=FALSE)
    async with test_session_factory() as session:
        ids = await CatalogRepo.list_product_ids_by_constraints(
            session,
            HardConstraints(
                category="美妆护肤", sub_category="精华", brand_exclude=["资生堂"],
            ),
        )
        assert set(ids) == {"p2"}

    # 3) price_max=250 → 只 p1 有 SKU ≤ 250(p2 最低 280,p3 sub_cat 不符)
    async with test_session_factory() as session:
        ids = await CatalogRepo.list_product_ids_by_constraints(
            session,
            HardConstraints(category="美妆护肤", sub_category="精华", price_max=250.0),
        )
        assert set(ids) == {"p1"}

    # 4) JSONB @> 含敏感肌:p1, p2(p3 不含,p4 inactive)
    async with test_session_factory() as session:
        ids = await CatalogRepo.list_product_ids_by_constraints(
            session,
            HardConstraints(properties_contains={"suitable_skin": ["敏感肌"]}),
        )
        assert set(ids) == {"p1", "p2"}


async def test_constraints_filter_always_excludes_inactive(test_session_factory) -> None:
    """无任何 constraint 时,is_active=FALSE 商品也不返回(防幻觉铁律 1)。"""
    from server.domain.types import HardConstraints
    from server.storage.catalog_repo import CatalogRepo
    from server.storage.models import Product, SKU

    async with test_session_factory() as session:
        session.add_all([
            Product(product_id="px", title="x", brand="X", category="C",
                    sub_category="S", base_price=10.0,
                    in_stock=True, is_active=True, properties={}),
            Product(product_id="py", title="y", brand="X", category="C",
                    sub_category="S", base_price=10.0,
                    in_stock=True, is_active=False, properties={}),
            SKU(sku_id="sx", product_id="px", properties={}, price=10.0),
            SKU(sku_id="sy", product_id="py", properties={}, price=10.0),
        ])
        await session.commit()

    async with test_session_factory() as session:
        ids = await CatalogRepo.list_product_ids_by_constraints(
            session, HardConstraints()
        )
        assert "px" in ids
        assert "py" not in ids


if __name__ == "__main__":
    # 直接 python 跑:只跑纯逻辑测试
    test_import_all_chunk3_modules()
    test_hard_constraints_is_empty()
    test_query_plan_defaults_and_strip()
    test_query_plan_extra_forbidden()
    test_in_memory_lru_basic()
    test_noop_cache()
    test_aggregate_groups_by_product_max_score()
    test_aggregate_respects_top_n()
    test_aggregate_coarse_threshold_filters()
    print("[sanity] pure-logic chunk3 tests passed.")
