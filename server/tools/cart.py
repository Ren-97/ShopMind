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
from server.tools._serializers import (
    cart_card,
    compute_sku_dimensions,
    sku_selector_card,
)
from server.tools.base import AgentDeps, Tool, ToolError, ToolResult

log = structlog.get_logger("shopmind.tools.cart")


CartAction = Literal["list", "add", "remove", "update"]


class ManageCartInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: CartAction = Field(
        description="list=查看 / add=加购 / remove=移除 / update=改数量"
    )
    sku_id: str | None = Field(
        default=None,
        description="SKU ID(remove/update 必填;add 时可不传,改传 product_id)",
    )
    product_id: str | None = Field(
        default=None,
        description="商品 ID(仅 add 用)。传它后端会判断:单规格直接加,多规格弹规格选择卡让用户点选,你无需自己挑规格。",
    )
    qty: int | None = Field(
        default=None,
        ge=1,
        description="数量(add 默认 1;update 必填 ≥1;list/remove 忽略)",
    )


class ManageCartTool(Tool):
    name: ClassVar[str] = "manage_cart"
    description: ClassVar[str] = (
        "购物车操作。返回最新购物车快照(cart card)。"
        "add 加购:传 product_id 即可(从搜索/对比结果里拿),后端会判断——"
        "单规格直接加,多规格返回一张规格选择卡让用户点选,**你不要自己挑规格、不要编 sku_id**。"
        "remove/update 用购物车里已有的 sku_id。"
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
        product_id = validated_input.product_id
        qty = validated_input.qty

        async with deps.session_factory() as session:
            if action == "add":
                # sku_id 没给则用 product_id 解析:单规格直接锁定唯一 sku,
                # 多规格不动购物车,返回规格选择卡让用户在客户端点选(铁律 1/3:不让 LLM 挑 sku)。
                if not sku_id:
                    if not product_id:
                        raise ToolError("action=add 需要 sku_id 或 product_id")
                    matched = await CatalogRepo.list_products_by_ids(
                        session, [product_id], include_inactive=True
                    )
                    product = matched[0] if matched else None
                    if product is None or not product.is_active:
                        raise ToolError(f"product_id={product_id} 不存在或商品已下架")
                    if not product.in_stock:
                        raise ToolError(f"product_id={product_id} 暂时缺货")
                    sku_payloads = [
                        {"sku_id": s.sku_id, "properties": dict(s.properties or {}), "price": s.price}
                        for s in product.skus
                    ]
                    if len(product.skus) > 1 and compute_sku_dimensions(sku_payloads):
                        card = sku_selector_card(product, base_url=deps.base_url)
                        log.info(
                            "cart_sku_selection_required",
                            product_id=product_id,
                            user_id=user_id,
                        )
                        return ToolResult(
                            payload={
                                "action": "add",
                                "sku_selection_required": True,
                                "product_id": product_id,
                                "dimensions": card["data"]["dimensions"],
                            },
                            cards=[card],
                        )
                    sku_id = product.skus[0].sku_id

                products = await CatalogRepo.list_products_by_sku_ids(
                    session, [sku_id], include_inactive=True
                )
                product = products.get(sku_id)
                if product is None or not product.is_active:
                    raise ToolError(f"sku_id={sku_id} 不存在或商品已下架")
                if not product.in_stock:
                    raise ToolError(f"sku_id={sku_id} 暂时缺货")
                await UserRepo.add_to_cart(session, user_id, sku_id, qty=qty or 1)

            elif action == "list":
                pass  # list 不需要校验

            elif not sku_id:
                raise ToolError(f"action={action} 需要 sku_id")

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

            # 写后读最新快照(session autoflush=False,先 flush 让刚写的改动可见)
            await session.flush()
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
