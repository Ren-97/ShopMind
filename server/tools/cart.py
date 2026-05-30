"""manage_cart tool(§4.6.1)。

支持 4 个 action:list / add / remove / update。
全部走 UserRepo(user_id 过滤,跨用户隔离,§4.6.8)。
add 时校验 SKU 存在(防 LLM 编 sku_id,铁律 1/3)+ 商品 is_active + in_stock。
注:in_stock 是 Product 级字段,SKU 表无独立库存,缺货粒度到商品族。
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field

from server.storage.catalog_repo import CatalogRepo
from server.storage.user_repo import UserRepo
from server.tools._serializers import cart_card
from server.tools.base import AgentDeps, Tool, ToolError, ToolResult

log = structlog.get_logger("shopmind.tools.cart")


CartAction = Literal["list", "add", "remove", "update"]


class ManageCartInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: CartAction = Field(
        description="list=查看 / add=加购 / remove=移除 / update=改数量"
    )
    sku_id: str | None = Field(default=None, description="SKU ID(action≠list 必填)")
    qty: int | None = Field(
        default=None,
        ge=1,
        description="数量(add 默认 1;update 必填 ≥1;list/remove 忽略)",
    )


class ManageCartTool(Tool):
    name: ClassVar[str] = "manage_cart"
    description: ClassVar[str] = (
        "购物车操作。返回最新购物车快照(cart card)。"
        "add 前如果用户没明确说 sku_id,要先调 search_products / compare_products "
        "拿到具体 sku_id,**不要凭空编 sku_id**。"
    )
    input_model: ClassVar[type[BaseModel]] = ManageCartInput

    async def _run(
        self,
        *,
        user_id: str,
        deps: AgentDeps,
        validated_input: BaseModel,
    ) -> ToolResult:
        assert isinstance(validated_input, ManageCartInput)
        action = validated_input.action
        sku_id = validated_input.sku_id
        qty = validated_input.qty

        async with deps.session_factory() as session:
            if action == "list":
                pass  # list 不需要校验
            else:
                if not sku_id:
                    raise ToolError(f"action={action} 需要 sku_id")

            if action == "add":
                products = await CatalogRepo.list_products_by_sku_ids(
                    session, [sku_id], include_inactive=True  # type: ignore[list-item]
                )
                product = products.get(sku_id)  # type: ignore[arg-type]
                if product is None or not product.is_active:
                    raise ToolError(f"sku_id={sku_id} 不存在或商品已下架")
                if not product.in_stock:
                    raise ToolError(f"sku_id={sku_id} 暂时缺货")
                await UserRepo.add_to_cart(session, user_id, sku_id, qty=qty or 1)  # type: ignore[arg-type]

            elif action == "update":
                if qty is None:
                    raise ToolError("action=update 需要 qty")
                updated = await UserRepo.update_cart_qty(
                    session, user_id, sku_id, qty  # type: ignore[arg-type]
                )
                if updated is None:
                    raise ToolError(f"购物车没有 sku_id={sku_id},无法 update")

            elif action == "remove":
                removed = await UserRepo.remove_from_cart(session, user_id, sku_id)  # type: ignore[arg-type]
                if not removed:
                    raise ToolError(f"购物车没有 sku_id={sku_id},无法 remove")

            # 写后读最新快照
            items = await UserRepo.list_cart(session, user_id)
            sku_ids = [it.sku_id for it in items]
            sku_to_product = await CatalogRepo.list_products_by_sku_ids(
                session, sku_ids, include_inactive=True
            )

            await session.commit()

        card = cart_card(items, sku_to_product=sku_to_product, base_url=deps.base_url)

        payload: dict[str, Any] = {
            "action": action,
            "item_count": card["data"]["item_count"],
            "total_price": card["data"]["total_price"],
            "items": card["data"]["items"],
        }
        log.info(
            "cart_done",
            action=action,
            user_id=user_id,
            item_count=payload["item_count"],
        )
        return ToolResult(payload=payload, cards=[card])

__all__ = ["ManageCartTool"]
