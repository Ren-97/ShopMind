"""REST /order — 订单创建 + 查询(§4.7.5 + §4.7.6 OrderConfirmScreen)。

V1 端点:
- POST   /order        → 真下单(从购物车快照,清购物车,创建 Order 记录)
- GET    /order        → 当前用户全部订单(倒序)
- GET    /order/{id}   → 单订单详情

下单的产品路径(方案 C):
- Agent 调 start_checkout tool → 只返回 checkout card(items + 地址 + 总价 快照)
- 用户在客户端确认页点 [确认下单] → POST /order → 真下单
- 购物车页 [去下单] → 同样 POST /order
- **单一下单路径**,Agent 自身不能跳过 UI 确认创建订单

预检规则与 start_checkout 对齐(§4.6.1):购物车非空 / 地址有 /
所有 SKU is_active + in_stock。任意不通过 → 409 / 422,前端兜底提示。
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server import config
from server.api.deps import get_current_user, get_session_factory
from server.storage.catalog_repo import CatalogRepo
from server.storage.user_repo import UserRepo
from server.tools._serializers import build_image_url, order_card

log = structlog.get_logger("shopmind.api.order")
router = APIRouter(prefix="/order", tags=["order"])


# ──────────────────────────────────────────────────────────────
# I/O models
# ──────────────────────────────────────────────────────────────
class PlaceOrderBody(BaseModel):
    """OrderConfirmScreen 提交。地址三件套可选 — 不传则取 profile;
    传了只用这次,**不**同步回 profile(改 profile 走 PATCH /profile)。"""

    model_config = ConfigDict(extra="forbid")

    address: str | None = Field(default=None, min_length=1, max_length=500)
    recipient_name: str | None = Field(default=None, min_length=1, max_length=100)
    phone: str | None = Field(default=None, min_length=1, max_length=50)


# ──────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────
@router.post("", status_code=status.HTTP_200_OK)
async def place_order(
    body: PlaceOrderBody,
    user_id: Annotated[str, Depends(get_current_user)],
    session_factory: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_session_factory)
    ],
) -> dict[str, Any]:
    """真下单。items 取购物车全部,地址三件套优先用 body,缺省回退 profile。

    异常:
    - 422:地址缺失(profile 没填且 body 也没传)
    - 409:购物车空 / 商品下架 / 缺货
    """
    async with session_factory() as session:
        cart_items = await UserRepo.list_cart(session, user_id)
        if not cart_items:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="购物车是空的",
            )

        profile = await UserRepo.get_profile(session, user_id)
        address = body.address or (profile.address if profile else None)
        if not address:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="收货地址缺失",
            )
        recipient_name = body.recipient_name or (
            profile.recipient_name if profile else None
        )
        phone = body.phone or (profile.phone if profile else None)

        # 拼快照 + 库存预检(再做一次,防止 checkout 后到下单期间下架)
        sku_ids = [it.sku_id for it in cart_items]
        sku_to_product = await CatalogRepo.list_products_by_sku_ids(
            session, sku_ids, include_inactive=True
        )

        snapshot: list[dict[str, Any]] = []
        total = 0.0
        for it in cart_items:
            product = sku_to_product.get(it.sku_id)
            if product is None or not product.is_active:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"{product.title if product else it.sku_id} 已下架,请从购物车移除后再下单",
                )
            if not product.in_stock:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"{product.title} 暂时缺货",
                )
            sku = next((s for s in product.skus if s.sku_id == it.sku_id), None)
            if sku is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"sku_id={it.sku_id} 不存在",
                )
            unit_price = float(sku.price)
            qty = int(it.qty)
            subtotal = unit_price * qty
            total += subtotal
            snapshot.append(
                {
                    "sku_id": it.sku_id,
                    "product_id": product.product_id,
                    "title": product.title,
                    "image_url": build_image_url(
                        product.image_path, base_url=config.BASE_URL
                    ),
                    "qty": qty,
                    "unit_price": unit_price,
                    "subtotal": subtotal,
                }
            )

        order_id = f"ord-{uuid.uuid4().hex[:12]}"
        order = await UserRepo.create_order(
            session,
            user_id,
            order_id=order_id,
            items=snapshot,
            address=address,
            total_price=round(total, 2),
            status="confirmed",
            recipient_name=recipient_name,
            phone=phone,
        )

        # 清购物车(按下单的 sku_id 整行删,保留未下单的)
        for sku_id in sku_ids:
            await UserRepo.remove_from_cart(session, user_id, sku_id)

        await session.commit()
        await session.refresh(order)
        card = order_card(order)

    log.info(
        "order_placed",
        user_id=user_id,
        order_id=order_id,
        total=total,
        n_items=len(snapshot),
    )
    return card


@router.get("")
async def list_orders(
    user_id: Annotated[str, Depends(get_current_user)],
    session_factory: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_session_factory)
    ],
    limit: int = 50,
) -> dict[str, Any]:
    async with session_factory() as session:
        orders = await UserRepo.list_orders(session, user_id, limit=limit)
        return {"orders": [order_card(o) for o in orders]}


@router.get("/{order_id}")
async def get_order(
    user_id: Annotated[str, Depends(get_current_user)],
    session_factory: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_session_factory)
    ],
    order_id: str = Path(min_length=1),
) -> dict[str, Any]:
    async with session_factory() as session:
        order = await UserRepo.get_order(session, user_id, order_id)
        if order is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"order_id={order_id} 不存在",
            )
        return order_card(order)


__all__ = ["router"]
