"""start_checkout tool(§4.6.1 + §4.7.5 checkout card)。

V1 设计:Agent 有发起结算的能力,但**不能直接下单** — 必须把用户引导到
OrderConfirmScreen,由用户在屏幕上点 [确认下单] 触发 REST POST /order 真下单。

start_checkout 的职责:
- 读购物车 + 读 profile 地址,做库存 / 地址 / 商品有效性预检
- 通过 → 返回 checkout card(items 快照 + 地址 + 收件人 + 总价)给前端
- 失败 → ToolError,Agent 据此告知用户(地址缺失 / 缺货 / 购物车空)

**绝不**创建 order 记录,**绝不**清购物车 — 这两件事只由 POST /order 做。
"""

from __future__ import annotations

from typing import ClassVar

import structlog
from pydantic import BaseModel, ConfigDict

from server.storage.catalog_repo import CatalogRepo
from server.storage.user_repo import UserRepo
from server.tools._serializers import checkout_card
from server.tools.base import AgentDeps, Tool, ToolError, ToolResult

log = structlog.get_logger("shopmind.tools.start_checkout")


class StartCheckoutInput(BaseModel):
    """无参数。items 始终取当前购物车,address 始终取 profile —
    避免 LLM 编造 sku_id / 地址,所有真值走 DB。"""

    model_config = ConfigDict(extra="forbid")


class StartCheckoutTool(Tool):
    name: ClassVar[str] = "start_checkout"
    description: ClassVar[str] = (
        "启动结算流程。读取当前购物车 + 用户档案地址,做库存/有效性预检,"
        "返回 checkout 卡片(items 快照 + 地址 + 总价)给客户端。"
        "**不真下单** — 用户必须在客户端确认页点 [确认下单] 才真创建订单。"
        "购物车为空 / 地址缺失 / 有缺货商品时会报错,需先用 manage_cart "
        "或 update_preference 处理。"
    )
    input_model: ClassVar[type[BaseModel]] = StartCheckoutInput

    async def _run(
        self,
        *,
        user_id: str,
        deps: AgentDeps,
        validated_input: BaseModel,
    ) -> ToolResult:
        async with deps.session_factory() as session:
            cart_items = await UserRepo.list_cart(session, user_id)
            if not cart_items:
                raise ToolError("购物车是空的,先把要买的商品加进购物车再结算")

            profile = await UserRepo.get_profile(session, user_id)
            address = profile.address if profile else None
            if not address:
                raise ToolError("还没填收货地址 — 请告诉我寄到哪里,或者去个人资料页填")

            sku_ids = [it.sku_id for it in cart_items]
            sku_to_product = await CatalogRepo.list_products_by_sku_ids(
                session, sku_ids, include_inactive=True
            )

            # 预检:已下架 / 缺货 / sku 不存在 一律拦截
            inactive_titles: list[str] = []
            out_of_stock_titles: list[str] = []
            for it in cart_items:
                product = sku_to_product.get(it.sku_id)
                if product is None or not product.is_active:
                    inactive_titles.append(
                        product.title if product else f"sku={it.sku_id}"
                    )
                elif not product.in_stock:
                    out_of_stock_titles.append(product.title)

            if inactive_titles:
                raise ToolError(
                    f"以下商品已下架,无法下单:{', '.join(inactive_titles)}。"
                    "请从购物车移除后再结算"
                )
            if out_of_stock_titles:
                raise ToolError(
                    f"以下商品暂时缺货:{', '.join(out_of_stock_titles)}。"
                    "请从购物车移除后再结算"
                )

            card = checkout_card(
                cart_items,
                sku_to_product=sku_to_product,
                address=address,
                recipient_name=profile.recipient_name if profile else None,
                phone=profile.phone if profile else None,
                base_url=deps.base_url,
            )

        total_price = card["data"]["total_price"]
        item_count = card["data"]["item_count"]
        log.info(
            "checkout_started",
            user_id=user_id,
            total=total_price,
            n_items=item_count,
        )
        return ToolResult(
            payload={
                "ready_to_checkout": True,
                "total_price": total_price,
                "item_count": item_count,
                "address": address,
            },
            cards=[card],
        )


__all__ = ["StartCheckoutTool"]
