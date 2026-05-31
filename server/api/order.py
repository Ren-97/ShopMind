"""REST /order — 订单查询(§4.7.5 + §4.7.6 OrderConfirmScreen)。

只读端点(下单通过 chat 的 place_order tool,§4.6.1):
- GET /order        → 当前用户全部订单(倒序)
- GET /order/{id}   → 单订单详情

下单本身仍走 chat tool — 维持"用户用自然语言下单"的产品路径。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server.api.deps import get_current_user, get_session_factory
from server.storage.user_repo import UserRepo
from server.tools._serializers import order_card

router = APIRouter(prefix="/order", tags=["order"])


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
