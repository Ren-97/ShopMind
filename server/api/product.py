"""REST /product/{product_id} — 完整商品详情(§4.7.4 + §4.7.6 ProductDetailScreen)。

SSE chat 推的是 lean card(8 字段);客户端点卡片跳详情页时调本端点拿完整数据:
  product_id / title / brand / category / sub_category /
  image_url / base_price / marketing_description /
  skus[] / properties / faqs[] / reviews[] /
  caveats / in_stock / is_active

防幻觉:404 时不暴露"已下架"细节 — `is_active=FALSE` 直接当不存在(§4.4.1)。
"""

from __future__ import annotations

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server import config
from server.api.deps import get_session_factory
from server.storage.catalog_repo import CatalogRepo
from server.storage.models import Product
from server.tools._serializers import build_image_url

log = structlog.get_logger("shopmind.api.product")
router = APIRouter(prefix="/product", tags=["product"])


def _product_detail(product: Product) -> dict[str, Any]:
    """完整 ProductDetail(§4.7.4)。"""
    caveats_text = product.caveats.caveats_text if product.caveats else None
    return {
        "product_id": product.product_id,
        "title": product.title,
        "brand": product.brand,
        "category": product.category,
        "sub_category": product.sub_category,
        "base_price": product.base_price,
        "image_url": build_image_url(product.image_path, base_url=config.BASE_URL),
        "marketing_description": product.marketing_description,
        "in_stock": product.in_stock,
        "is_active": product.is_active,
        "properties": dict(product.properties or {}),
        "skus": [
            {
                "sku_id": s.sku_id,
                "properties": dict(s.properties or {}),
                "price": s.price,
            }
            for s in product.skus
        ],
        "faqs": [
            {
                "question": f.question,
                "answer": f.answer,
                "order_idx": f.order_idx,
            }
            for f in sorted(product.faqs, key=lambda x: x.order_idx)
        ],
        "reviews": [
            {
                "review_id": r.review_id,
                "nickname": r.nickname,
                "rating": r.rating,
                "content": r.content,
                "sentiment": r.sentiment,
                "aspects": list(r.aspects) if r.aspects else [],
            }
            for r in product.reviews
        ],
        "caveats": caveats_text,
    }


@router.get("/{product_id}")
async def get_product(
    session_factory: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_session_factory)
    ],
    product_id: str = Path(min_length=1),
) -> dict[str, Any]:
    """单品详情。不存在 / 已下架 → 404(防幻觉:对外不区分两种状态)。"""
    async with session_factory() as session:
        product = await CatalogRepo.get_product_with_details(session, product_id)
        if product is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"product_id={product_id} 不存在",
            )
        return _product_detail(product)


__all__ = ["router"]
