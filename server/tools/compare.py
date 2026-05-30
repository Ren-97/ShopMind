"""compare_products tool(§4.6.1 + §4.7.5 compare_table card)。

行为:
  - 入参:2-5 个 product_id(下界由 COMPARE_MIN_ITEMS 控,上界 COMPARE_MAX_ITEMS)
  - DB SELECT 完整字段(防幻觉铁律 3)
  - 已下架 / 不存在的 id → 直接跳过,只对剩下的对比
  - 自动 compute_highlight(纯代码,无 LLM):price=winner, contains_*=warning
"""

from __future__ import annotations

from typing import Any, ClassVar

import structlog
from pydantic import BaseModel, ConfigDict, Field

from server import config
from server.storage.catalog_repo import CatalogRepo
from server.storage.models import Product
from server.tools._serializers import (
    build_image_url,
    product_summary_from_db,
)
from server.tools.base import AgentDeps, Tool, ToolError, ToolResult

log = structlog.get_logger("shopmind.tools.compare")


class CompareProductsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_ids: list[str] = Field(
        min_length=config.COMPARE_MIN_ITEMS,
        max_length=config.COMPARE_MAX_ITEMS,
        description=(
            f"要对比的商品 ID 列表,数量必须在 "
            f"{config.COMPARE_MIN_ITEMS}-{config.COMPARE_MAX_ITEMS} 之间。"
        ),
    )


# ──────────────────────────────────────────────────────────────────────
# compute_highlight 规则(§4.7.5,纯代码 0 LLM)
#
# 设计原则:**只对客观事实高亮**,主观判断("含酒精对你不好"/"这肤质更适合你")
# 交给 LLM 文本回复 — 文本回复有 query / profile / chat 上下文,能做"基于你的
# 需求"的判断;表格没上下文,做价值判断容易冒犯不同偏好的用户。
#
# 保留的高亮(客观):
#   - 价格 winner = 最低(社会共识强,几乎所有购物场景便宜≈好)
#   - 库存 warning = 缺货(`in_stock=False`,纯客观:不能下单)
# 不做的高亮:
#   - 含酒精 / 含香精 = true 是不是 warning,**取决于用户肤质**(敏感肌怕,
#     油皮可能正面),所以只展示"是/否",不染色
#   - 适合肤质 / 类目 等主观项 → 永远 null
# ──────────────────────────────────────────────────────────────────────
def _highlight_min(values: list[float]) -> dict[str, Any] | None:
    """数值最小者标 winner。全同 → None(无对比价值)。"""
    if not values:
        return None
    if len(set(values)) == 1:
        return None
    min_v = min(values)
    return {"type": "winner", "indices": [i for i, v in enumerate(values) if v == min_v]}


def _highlight_out_of_stock(stocks: list[bool]) -> dict[str, Any] | None:
    """缺货标 warning。全在售 / 全缺货 → None(无对比价值)。"""
    out_idx = [i for i, in_stock in enumerate(stocks) if not in_stock]
    if not out_idx or len(out_idx) == len(stocks):
        return None
    return {"type": "warning", "indices": out_idx}


def _build_rows(products: list[Product]) -> list[dict[str, Any]]:
    """根据多商品的 properties / 价格 / 库存自动选行。"""
    rows: list[dict[str, Any]] = []

    # 价格行(客观 winner)
    prices = [float(p.base_price) for p in products]
    rows.append(
        {
            "attr_label": "起售价",
            "values": [f"¥{int(v) if v.is_integer() else v}" for v in prices],
            "highlight": _highlight_min(prices),
        }
    )

    # 库存行(客观 warning:缺货 = 不能下单)
    stocks = [bool(p.in_stock) for p in products]
    rows.append(
        {
            "attr_label": "库存",
            "values": ["在售" if s else "缺货" for s in stocks],
            "highlight": _highlight_out_of_stock(stocks),
        }
    )

    # 类目行(主观,无 highlight)
    rows.append(
        {
            "attr_label": "类目",
            "values": [f"{p.category}/{p.sub_category}" for p in products],
            "highlight": None,
        }
    )

    # 美妆专项:含酒精 / 含香精 / 适合肤质 — 只展示,不高亮(主观)
    if any((p.properties or {}).get("contains_alcohol") is not None for p in products):
        rows.append(
            {
                "attr_label": "含酒精",
                "values": [
                    "是" if (p.properties or {}).get("contains_alcohol") else "否"
                    for p in products
                ],
                "highlight": None,
            }
        )
    if any((p.properties or {}).get("contains_fragrance") is not None for p in products):
        rows.append(
            {
                "attr_label": "含香精",
                "values": [
                    "是" if (p.properties or {}).get("contains_fragrance") else "否"
                    for p in products
                ],
                "highlight": None,
            }
        )
    if any((p.properties or {}).get("suitable_skin") for p in products):
        rows.append(
            {
                "attr_label": "适合肤质",
                "values": [
                    "/".join((p.properties or {}).get("suitable_skin") or []) or "—"
                    for p in products
                ],
                "highlight": None,
            }
        )

    return rows


class CompareProductsTool(Tool):
    name: ClassVar[str] = "compare_products"
    description: ClassVar[str] = (
        "对比 2-5 个商品。返回一个 compare_table 卡片(自动高亮 winner / warning),"
        "以及全字段 ProductSummary 列表供回复引用。"
        "已下架 / 不存在的 ID 会被丢弃。"
    )
    input_model: ClassVar[type[BaseModel]] = CompareProductsInput

    async def _run(
        self,
        *,
        user_id: str,
        deps: AgentDeps,
        validated_input: BaseModel,
    ) -> ToolResult:
        assert isinstance(validated_input, CompareProductsInput)
        product_ids = list(dict.fromkeys(validated_input.product_ids))  # dedup 保序

        async with deps.session_factory() as session:
            products: list[Product] = []
            missing: list[str] = []
            for pid in product_ids:
                p = await CatalogRepo.get_product_with_details(session, pid)
                if p is None:
                    missing.append(pid)
                else:
                    products.append(p)

        if len(products) < config.COMPARE_MIN_ITEMS:
            raise ToolError(
                f"可对比商品不足 {config.COMPARE_MIN_ITEMS} 个(下架或不存在:{missing})"
            )

        payloads = [product_summary_from_db(p, base_url=deps.base_url) for p in products]

        compare_table_card = {
            "type": "compare_table",
            "data": {
                "headers": [
                    {
                        "product_id": p.product_id,
                        "title": p.title,
                        "image_url": build_image_url(p.image_path, base_url=deps.base_url),
                        "base_price": float(p.base_price),
                    }
                    for p in products
                ],
                "rows": _build_rows(products),
            },
        }

        log.info(
            "compare_done",
            requested=len(product_ids),
            compared=len(products),
            missing=missing,
        )
        return ToolResult(
            payload={"products": payloads, "missing_product_ids": missing},
            cards=[compare_table_card],
        )


__all__ = ["CompareProductsTool"]
