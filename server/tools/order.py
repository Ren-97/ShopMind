"""place_order tool(§4.6.1 + §4.7.5 order card)。

V1 模拟下单(不真接支付):
- items 默认 = 当前购物车全部
- address 默认从 user_profile 取;profile 没有时返回 ToolError("地址缺失")
- 创建 orders 行 + 从购物车移除已下单的 sku_id(整行删,不按 qty 抵扣)
- items 字段写入下单时商品快照(后续 SKU 变价不影响订单)
"""

from __future__ import annotations

import uuid
from typing import Any, ClassVar

import structlog
from pydantic import BaseModel, ConfigDict, Field

from server.storage.catalog_repo import CatalogRepo
from server.storage.user_repo import UserRepo
from server.tools._serializers import build_image_url, order_card
from server.tools.base import AgentDeps, Tool, ToolError, ToolResult

log = structlog.get_logger("shopmind.tools.order")


class PlaceOrderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[dict[str, Any]] | None = Field(
        default=None,
        description=(
            "可选,不传则取购物车全部。每项 {sku_id: str, qty: int}。"
            "建议留空走默认。"
        ),
    )
    address: str | None = Field(
        default=None,
        description="可选,不传则取 user_profile.address。",
    )


class PlaceOrderTool(Tool):
    name: ClassVar[str] = "place_order"
    description: ClassVar[str] = (
        "下单。默认用当前购物车 + 用户档案里的地址,不带参数即可。"
        "如果地址缺失会报错,这时要先用 update_preference 写入地址再下单。"
    )
    input_model: ClassVar[type[BaseModel]] = PlaceOrderInput

    async def _run(
        self,
        *,
        user_id: str,
        deps: AgentDeps,
        validated_input: BaseModel,
    ) -> ToolResult:
        assert isinstance(validated_input, PlaceOrderInput)
        explicit_items = validated_input.items
        explicit_address = validated_input.address

        async with deps.session_factory() as session:
            profile = await UserRepo.get_profile(session, user_id)
            address = explicit_address or (profile.address if profile else None)
            if not address:
                raise ToolError("下单缺少地址:请告诉我寄到哪里")

            # 确定下单 items(sku_id + qty)
            order_lines: list[tuple[str, int]] = []
            if explicit_items:
                for it in explicit_items:
                    sku_id = it.get("sku_id")
                    qty = int(it.get("qty", 1))
                    if not sku_id or qty <= 0:
                        raise ToolError(f"非法 item: {it}")
                    order_lines.append((sku_id, qty))
            else:
                cart_items = await UserRepo.list_cart(session, user_id)
                if not cart_items:
                    raise ToolError("购物车是空的,先 add 商品再下单")
                order_lines = [(it.sku_id, int(it.qty)) for it in cart_items]

            # 拼快照(SELECT 完整字段,§4.6.7 铁律 3)
            sku_ids = [s for s, _ in order_lines]
            sku_to_product = await CatalogRepo.list_products_by_sku_ids(
                session, sku_ids, include_inactive=True
            )

            snapshot: list[dict[str, Any]] = []
            total = 0.0
            for sku_id, qty in order_lines:
                product = sku_to_product.get(sku_id)
                if product is None or not product.is_active:
                    raise ToolError(f"sku_id={sku_id} 已下架或不存在,无法下单")
                sku = next((s for s in product.skus if s.sku_id == sku_id), None)
                if sku is None:
                    raise ToolError(f"sku_id={sku_id} 不存在")
                unit_price = float(sku.price)
                subtotal = unit_price * qty
                total += subtotal
                snapshot.append(
                    {
                        "sku_id": sku_id,
                        "product_id": product.product_id,
                        "title": product.title,
                        "image_url": build_image_url(
                            product.image_path, base_url=deps.base_url
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
                recipient_name=(profile.recipient_name if profile else None),
                phone=(profile.phone if profile else None),
            )

            # 把这次下单的 sku_id 从购物车移除(不在的忽略,整行删)
            for sku_id in sku_ids:
                await UserRepo.remove_from_cart(session, user_id, sku_id)

            await session.commit()
            # commit 后 order.created_at 才是 DB 时间 — 再 refresh 一下
            await session.refresh(order)

            card = order_card(order)

        log.info(
            "order_done",
            user_id=user_id,
            order_id=order_id,
            total=total,
            n_items=len(snapshot),
        )
        return ToolResult(
            payload={
                "order_id": order_id,
                "status": "confirmed",
                "total_price": round(total, 2),
                "item_count": len(snapshot),
            },
            cards=[card],
        )

__all__ = ["PlaceOrderTool"]
