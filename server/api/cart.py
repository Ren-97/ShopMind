"""REST /cart — 客户端购物车页面(§4.7.6 CartScreen)。

V1 端点:
- GET    /cart                 → cart card(JSON,同 SSE schema)
- POST   /cart                 → 加购 {sku_id, qty} → cart card
- PATCH  /cart/{sku_id}        → 更新数量 {qty} → cart card
- DELETE /cart/{sku_id}        → 移除单品 → cart card
- DELETE /cart                 → 清空购物车 → cart card

业务规则跟 `manage_cart` tool 对齐(§4.6.1 + §4.7.5):
- add 校验商品 is_active + in_stock,缺货 409
- update qty ≥ 1
- 跨用户隔离走 UserRepo(SQL WHERE user_id=?)
"""

from __future__ import annotations

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server.api.deps import get_current_user, get_session_factory
from server.storage.catalog_repo import CatalogRepo
from server.storage.user_repo import UserRepo
from server.tools._serializers import cart_card

log = structlog.get_logger("shopmind.api.cart")
router = APIRouter(prefix="/cart", tags=["cart"])


# ──────────────────────────────────────────────────────────────
# I/O models
# ──────────────────────────────────────────────────────────────
class CartAddBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku_id: str = Field(min_length=1)
    qty: int = Field(default=1, ge=1)


class CartUpdateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    qty: int = Field(ge=1)


# ──────────────────────────────────────────────────────────────
# 内部:写后读最新 cart card(避免每个端点重复)
# ──────────────────────────────────────────────────────────────
async def _snapshot_cart(
    session: AsyncSession, user_id: str, base_url: str
) -> dict[str, Any]:
    from server import config as _config

    # session autoflush=False(db.py):先 flush 让本请求里刚写的 cart 改动可见,
    # 否则 list_cart 读到的是改动前的旧快照(GET 无 pending 写 → flush 是 no-op)。
    await session.flush()
    items = await UserRepo.list_cart(session, user_id)
    sku_ids = [it.sku_id for it in items]
    sku_to_product = await CatalogRepo.list_products_by_sku_ids(
        session, sku_ids, include_inactive=True
    )
    return cart_card(items, sku_to_product=sku_to_product, base_url=base_url or _config.BASE_URL)


# ──────────────────────────────────────────────────────────────
# 端点
# ──────────────────────────────────────────────────────────────
@router.get("")
async def get_cart(
    user_id: Annotated[str, Depends(get_current_user)],
    session_factory: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_session_factory)
    ],
) -> dict[str, Any]:
    from server import config

    async with session_factory() as session:
        return await _snapshot_cart(session, user_id, config.BASE_URL)


@router.post("", status_code=status.HTTP_200_OK)
async def add_to_cart(
    body: CartAddBody,
    user_id: Annotated[str, Depends(get_current_user)],
    session_factory: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_session_factory)
    ],
) -> dict[str, Any]:
    from server import config

    async with session_factory() as session:
        products = await CatalogRepo.list_products_by_sku_ids(
            session, [body.sku_id], include_inactive=True
        )
        product = products.get(body.sku_id)
        if product is None or not product.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"sku_id={body.sku_id} 不存在或商品已下架",
            )
        if not product.in_stock:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"{product.title} 暂时缺货",
            )
        await UserRepo.add_to_cart(session, user_id, body.sku_id, body.qty)
        card = await _snapshot_cart(session, user_id, config.BASE_URL)
        await session.commit()
    log.info("cart_added", user_id=user_id, sku_id=body.sku_id, qty=body.qty)
    return card


@router.patch("/{sku_id}")
async def update_cart_qty(
    body: CartUpdateBody,
    user_id: Annotated[str, Depends(get_current_user)],
    session_factory: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_session_factory)
    ],
    sku_id: str = Path(min_length=1),
) -> dict[str, Any]:
    from server import config

    async with session_factory() as session:
        updated = await UserRepo.update_cart_qty(session, user_id, sku_id, body.qty)
        if updated is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"购物车没有 sku_id={sku_id}",
            )
        card = await _snapshot_cart(session, user_id, config.BASE_URL)
        await session.commit()
    return card


@router.delete("/{sku_id}")
async def remove_from_cart(
    user_id: Annotated[str, Depends(get_current_user)],
    session_factory: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_session_factory)
    ],
    sku_id: str = Path(min_length=1),
) -> dict[str, Any]:
    from server import config

    async with session_factory() as session:
        removed = await UserRepo.remove_from_cart(session, user_id, sku_id)
        if not removed:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"购物车没有 sku_id={sku_id}",
            )
        card = await _snapshot_cart(session, user_id, config.BASE_URL)
        await session.commit()
    return card


@router.delete("")
async def clear_cart(
    user_id: Annotated[str, Depends(get_current_user)],
    session_factory: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_session_factory)
    ],
) -> dict[str, Any]:
    from server import config

    async with session_factory() as session:
        await UserRepo.clear_cart(session, user_id)
        card = await _snapshot_cart(session, user_id, config.BASE_URL)
        await session.commit()
    return card


__all__ = ["router"]
