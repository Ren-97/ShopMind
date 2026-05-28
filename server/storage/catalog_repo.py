"""
Catalog Repository(对应 docs/design.md §4.4.1 + §4.6.7 + 防幻觉铁律 1)。

设计原则:
- **永远带 `WHERE is_active=TRUE`**:防幻觉铁律 1,下架商品永不出现
- `in_stock` 按需过滤(用户明确说"在售/还有的"才加)
- 所有方法 async,SQLAlchemy v2 select 风格
- 此层不暴露 LLM,只负责"商品事实"的 DB 真相

Chunk 1 范围:基础骨架 + 关键查询。复杂检索(filter / sku-price-match)在 Chunk 3 RAG 时扩展。
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from server.domain.types import HardConstraints
from server.storage.models import (
    Product,
    ProductCaveats,
    ProductFAQ,
    ProductReview,
    SKU,
)


# 闭集单值字段的"用户值 → DB 允许 value 集合"展开表(§4.2 hard 层级)
# - 含义:用户/Planner 写 "男" → SQL 也应匹配 "通用"(同义包含)
# - 仅对 scalar 单值闭集字段做(gender / age_group);
#   list 闭集字段(suitable_skin)用 JSONB @> contains 语义本身就是子集匹配,无需展开
_PROPERTY_HIERARCHY: dict[str, dict[str, list[str]]] = {
    "gender": {
        "男": ["男", "通用"],
        "女": ["女", "通用"],
    },
    "age_group": {
        "20+": ["20+", "通用"],
        "25+": ["25+", "通用"],
        "30+": ["30+", "通用"],
    },
}


def _expand_hierarchy(field: str, value: str) -> list[str]:
    """查 _PROPERTY_HIERARCHY,返回 value 应展开匹配的全部 DB 值。

    未在表里登记的字段/值 → 仅匹配原值本身(默认严格匹配)。
    """
    return _PROPERTY_HIERARCHY.get(field, {}).get(value, [value])


class CatalogRepo:
    """商品族 Repo — 全部方法默认 `is_active=TRUE` 过滤。"""

    # ──────────────────────────────────────────────
    # 单品查询
    # ──────────────────────────────────────────────
    @staticmethod
    async def get_product(
        session: AsyncSession,
        product_id: str,
        *,
        include_inactive: bool = False,
    ) -> Product | None:
        stmt = select(Product).where(Product.product_id == product_id)
        if not include_inactive:
            stmt = stmt.where(Product.is_active.is_(True))
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def get_product_with_details(
        session: AsyncSession,
        product_id: str,
        *,
        include_inactive: bool = False,
    ) -> Product | None:
        """单品 + skus/faqs/reviews/caveats 全量(给 Tool 拼 ProductSummary 用)。

        类目特化属性已经在 product.properties JSONB,不需要 selectinload。
        """
        stmt = (
            select(Product)
            .where(Product.product_id == product_id)
            .options(
                selectinload(Product.skus),
                selectinload(Product.faqs),
                selectinload(Product.reviews),
                selectinload(Product.caveats),
            )
        )
        if not include_inactive:
            stmt = stmt.where(Product.is_active.is_(True))
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_products_by_ids(
        session: AsyncSession,
        product_ids: Sequence[str],
        *,
        include_inactive: bool = False,
    ) -> list[Product]:
        if not product_ids:
            return []
        stmt = (
            select(Product)
            .where(Product.product_id.in_(product_ids))
            .options(selectinload(Product.skus))
        )
        if not include_inactive:
            stmt = stmt.where(Product.is_active.is_(True))
        result = await session.execute(stmt)
        return list(result.scalars().all())

    # ──────────────────────────────────────────────
    # 写入(ingest 调用)
    # ──────────────────────────────────────────────
    @staticmethod
    async def upsert_product(session: AsyncSession, product: Product) -> None:
        """UPSERT product(merge → flush,主键冲突自动 update)。"""
        await session.merge(product)

    @staticmethod
    async def replace_skus(
        session: AsyncSession, product_id: str, skus: list[SKU]
    ) -> None:
        """删除该 product 的全部 SKU,再 bulk insert 新 SKU(ingest 用)。"""
        await session.execute(
            SKU.__table__.delete().where(SKU.product_id == product_id)
        )
        for sku in skus:
            session.add(sku)

    @staticmethod
    async def replace_faqs(
        session: AsyncSession, product_id: str, faqs: list[ProductFAQ]
    ) -> None:
        await session.execute(
            ProductFAQ.__table__.delete().where(ProductFAQ.product_id == product_id)
        )
        for faq in faqs:
            session.add(faq)

    @staticmethod
    async def replace_reviews(
        session: AsyncSession, product_id: str, reviews: list[ProductReview]
    ) -> None:
        await session.execute(
            ProductReview.__table__.delete().where(
                ProductReview.product_id == product_id
            )
        )
        for review in reviews:
            session.add(review)

    @staticmethod
    async def upsert_caveats(
        session: AsyncSession,
        product_id: str,
        caveats_text: str | None,
    ) -> None:
        caveats = ProductCaveats(product_id=product_id, caveats_text=caveats_text)
        await session.merge(caveats)

    # ──────────────────────────────────────────────
    # 列表 / 计数(eval + 调试用)
    # ──────────────────────────────────────────────
    @staticmethod
    async def count_active_products(session: AsyncSession) -> int:
        from sqlalchemy import func

        result = await session.execute(
            select(func.count(Product.product_id)).where(Product.is_active.is_(True))
        )
        return int(result.scalar_one())

    # ──────────────────────────────────────────────
    # 硬约束过滤(§4.1.3 Filtered Semantic / Structured 入口)
    # ──────────────────────────────────────────────
    @staticmethod
    async def list_product_ids_by_constraints(
        session: AsyncSession,
        constraints: HardConstraints,
        *,
        limit: int | None = None,
    ) -> list[str]:
        """按 HardConstraints 拼 SQL → 返回 product_id 白名单(防幻觉铁律 1)。

        - 永远带 `is_active=TRUE`(不论 constraints 是否给)
        - 价格过滤走 SKU EXISTS(§4.4.1 业务行为约定:"只要有一个 SKU 在区间就保留")
        - list 闭集(suitable_skin)走 JSONB `@>` contains 子集匹配
        - bool 闭集(contains_alcohol / contains_fragrance)走 JSONB `@>`
        - scalar 闭集(gender / age_group)走 OR 展开层级表(_PROPERTY_HIERARCHY)
        - `limit=None` 不截断(Filtered Semantic 把这个列表喂给 Qdrant filter,
          100-1000 商品级别可接受;后续上量再加默认 limit)
        """
        stmt = select(Product.product_id).where(Product.is_active.is_(True))

        if constraints.category is not None:
            stmt = stmt.where(Product.category == constraints.category)
        if constraints.sub_category is not None:
            stmt = stmt.where(Product.sub_category == constraints.sub_category)
        if constraints.brand is not None:
            stmt = stmt.where(Product.brand == constraints.brand)
        if constraints.brand_exclude:
            stmt = stmt.where(Product.brand.notin_(constraints.brand_exclude))
        if constraints.in_stock is True:
            stmt = stmt.where(Product.in_stock.is_(True))

        # 价格范围:EXISTS(SELECT 1 FROM skus WHERE product_id=... AND price BETWEEN ...)
        if constraints.price_min is not None or constraints.price_max is not None:
            sku_q = select(SKU.sku_id).where(SKU.product_id == Product.product_id)
            if constraints.price_min is not None:
                sku_q = sku_q.where(SKU.price >= constraints.price_min)
            if constraints.price_max is not None:
                sku_q = sku_q.where(SKU.price <= constraints.price_max)
            stmt = stmt.where(exists(sku_q))

        # ── 闭集 list 字段(suitable_skin):JSONB @> 子集匹配 ──
        # Planner ["敏感肌"] → 匹配 product.properties.suitable_skin 包含 敏感肌
        if constraints.suitable_skin:
            stmt = stmt.where(
                Product.properties.contains(
                    {"suitable_skin": list(constraints.suitable_skin)}
                )
            )

        # ── 闭集 bool 字段(contains_alcohol / contains_fragrance) ──
        if constraints.contains_alcohol is not None:
            stmt = stmt.where(
                Product.properties.contains(
                    {"contains_alcohol": constraints.contains_alcohol}
                )
            )
        if constraints.contains_fragrance is not None:
            stmt = stmt.where(
                Product.properties.contains(
                    {"contains_fragrance": constraints.contains_fragrance}
                )
            )

        # ── 闭集 scalar 字段 + 层级展开(gender / age_group) ──
        # 例:Planner gender="男" → SQL 匹配 "男" OR "通用"
        if constraints.gender is not None:
            allowed = _expand_hierarchy("gender", constraints.gender)
            stmt = stmt.where(
                or_(*[Product.properties["gender"].astext == v for v in allowed])
            )
        if constraints.age_group is not None:
            allowed = _expand_hierarchy("age_group", constraints.age_group)
            stmt = stmt.where(
                or_(*[Product.properties["age_group"].astext == v for v in allowed])
            )

        if limit is not None:
            stmt = stmt.limit(limit)

        result = await session.execute(stmt)
        return [row[0] for row in result.all()]

    @staticmethod
    async def list_all_product_ids(
        session: AsyncSession, *, include_inactive: bool = False
    ) -> list[str]:
        stmt = select(Product.product_id)
        if not include_inactive:
            stmt = stmt.where(Product.is_active.is_(True))
        result = await session.execute(stmt)
        return [row[0] for row in result.all()]


__all__ = ["CatalogRepo"]
