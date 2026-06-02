"""Tool 共享:Product / Cart / Order 序列化(§4.6.7 ProductSummary + §4.7 Card)。

LLM-facing payload(给 Claude 看的)和 SSE card(给前端看的)分两套:
- payload 是 ProductSummary 全字段(含 matched_chunks 用户评论原文,L3 证据)
- card 是 lean schema(8 字段),走 SSE 推前端(§4.7.4)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from server import config
from server.domain.types import MatchedChunk
from server.rag.reranking.protocol import RankedProduct
from server.storage.models import CartItem, Order, Product


def _truncate(text: str, limit: int | None = None) -> str:
    cap = limit if limit is not None else config.MATCHED_CHUNK_TEXT_MAX_CHARS
    if len(text) <= cap:
        return text
    return text[: cap - 1] + "…"


def build_image_url(image_path: str | None, *, base_url: str) -> str | None:
    """商品相对 image_path → 完整 URL(§4.6.2)。image_path 为 None 返回 None。"""
    if not image_path:
        return None
    if image_path.startswith(("http://", "https://")):
        return image_path
    # /static 挂载到 STATIC_FILES_DIR;Product.image_path 是相对 dataset 路径
    safe = image_path.lstrip("/")
    return f"{base_url.rstrip('/')}/static/{safe}"


def _chunk_info(chunk: MatchedChunk) -> dict[str, Any]:
    """给 LLM 看的简化 chunk(只保留 type + 截断 text)。"""
    return {"chunk_type": chunk.chunk_type, "text": _truncate(chunk.text)}


# ──────────────────────────────────────────────────────────────────────
# Product:LLM-facing payload(ProductSummary)
# ──────────────────────────────────────────────────────────────────────
def product_summary_from_ranked(
    ranked: RankedProduct,
    *,
    base_url: str,
) -> dict[str, Any]:
    """RankedProduct(已经过 DB enrich)→ LLM payload(§4.6.7)。"""
    return {
        "product_id": ranked.product_id,
        "title": ranked.title,
        "brand": ranked.brand,
        "category": ranked.category,
        "sub_category": ranked.sub_category,
        "base_price": ranked.base_price,
        "image_url": build_image_url(ranked.image_path, base_url=base_url),
        "in_stock": ranked.in_stock,
        "is_active": True,  # RankedProduct 已经过 is_active=TRUE 过滤
        "properties": ranked.properties,  # L1 商家结构化(suitable_skin / contains_alcohol / ...)
        "caveats": ranked.caveats_text,  # L4
        "matched_chunks": [_chunk_info(c) for c in ranked.matched_chunks],
        "relevance_score": ranked.relevance_score,
        "reason": ranked.reason,
    }


def product_summary_from_db(
    product: Product,
    *,
    base_url: str,
) -> dict[str, Any]:
    """从 ORM Product 直接拼 payload(compare_products 用 — 不走 retrieval)。

    matched_chunks 留空(compare 是按 id 取,没有检索证据)。
    """
    caveats_text = product.caveats.caveats_text if product.caveats else None
    return {
        "product_id": product.product_id,
        "title": product.title,
        "brand": product.brand,
        "category": product.category,
        "sub_category": product.sub_category,
        "base_price": product.base_price,
        "image_url": build_image_url(product.image_path, base_url=base_url),
        "in_stock": product.in_stock,
        "is_active": product.is_active,
        "properties": dict(product.properties or {}),
        "marketing_description": product.marketing_description,
        "caveats": caveats_text,
        "skus": [
            {
                "sku_id": s.sku_id,
                "properties": dict(s.properties or {}),
                "price": s.price,
            }
            for s in product.skus
        ],
        "matched_chunks": [],
    }


# ──────────────────────────────────────────────────────────────────────
# SSE Card:product / compare_table / cart / order(§4.7.4-5)
# ──────────────────────────────────────────────────────────────────────
# bool 字段 → chip 文案映射(§4.7.4)。
# 不在表里的 bool 字段直接跳过(语义不明确,不渲染 chip)。
_BOOL_CHIP_LABELS: dict[str, tuple[str, str]] = {
    # field_name: (true_label, false_label)
    "contains_alcohol":   ("含酒精", "无酒精"),
    "contains_fragrance": ("含香精", "无香精"),
}

# 不进 chip 池的 properties 字段(负向语义,不当卖点展示)。
_PROPERTY_FIELDS_BLACKLIST: frozenset[str] = frozenset({
    "not_suitable_skin",  # "不适合 X" 不是卖点
    "allergens",          # 过敏原,负向
})


def _collect_chip_candidates(properties: dict[str, Any]) -> list[str]:
    """从 properties 收集 chip 候选(§4.7.4)。

    规则:
      - str 值              → 直接进
      - list[str] 值        → 每个元素进
      - bool 值             → 查 _BOOL_CHIP_LABELS 映射;不在表里的跳过
      - 其他类型(int/float/dict/list[非str]) → 跳过
      - _PROPERTY_FIELDS_BLACKLIST 字段一律跳过
      - 去重保序(dataset 输入顺序,商家通常已从重要到次要)
    """
    pool: list[str] = []
    seen: set[str] = set()

    def _add(chip: str) -> None:
        chip = chip.strip()
        if chip and chip not in seen:
            pool.append(chip)
            seen.add(chip)

    for field, value in (properties or {}).items():
        if field in _PROPERTY_FIELDS_BLACKLIST:
            continue
        if isinstance(value, bool):
            labels = _BOOL_CHIP_LABELS.get(field)
            if labels is not None:
                _add(labels[0] if value else labels[1])
        elif isinstance(value, str):
            _add(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    _add(item)
        # 其它类型(数值 / dict / 嵌套结构):跳过
    return pool


def product_card(product_payload: dict[str, Any]) -> dict[str, Any]:
    """lean product card(§4.7.4)。caveats / marketing_description / skus 不进卡片。

    `tags_candidates`:把 `properties` 里所有可展示值摊平成一个候选池(跨字段、跨类目),
    bool 字段用 _BOOL_CHIP_LABELS 做友好文案。**不排序、不截断** — 顺序保留 dataset
    原序(商家通常已经从重要到次要排好),前端 Material 3 FlowRow 按可用宽度自适应
    截行,溢出 "+N more"。
    """
    pool = _collect_chip_candidates(product_payload.get("properties") or {})

    return {
        "type": "product",
        "data": {
            "product_id": product_payload["product_id"],
            "title": product_payload["title"],
            "brand": product_payload["brand"],
            "image_url": product_payload.get("image_url"),
            "base_price": product_payload["base_price"],
            "default_sku_id": None,  # 详情走 /product/{id};lean card 不带 sku
            "sku_count": len(product_payload.get("skus") or []),
            "tags_candidates": pool,  # 候选池,前端自适应截
            "in_stock": product_payload["in_stock"],
        },
    }


def cart_card(
    cart_items: list[CartItem],
    *,
    sku_to_product: dict[str, Product],
    base_url: str,
) -> dict[str, Any]:
    """cart card(§4.7.5)。sku_to_product 由 cart tool 预 JOIN 拼好传入。"""
    items_data: list[dict[str, Any]] = []
    total = 0.0
    for it in cart_items:
        product = sku_to_product.get(it.sku_id)
        sku = next(
            (s for s in (product.skus if product else []) if s.sku_id == it.sku_id),
            None,
        )
        unit_price = float(sku.price) if sku is not None else 0.0
        subtotal = unit_price * int(it.qty)
        total += subtotal
        items_data.append(
            {
                "sku_id": it.sku_id,
                "product_id": product.product_id if product else None,
                "title": product.title if product else "",
                "image_url": (
                    build_image_url(product.image_path, base_url=base_url)
                    if product
                    else None
                ),
                "qty": int(it.qty),
                "unit_price": unit_price,
                "subtotal": subtotal,
                "in_stock": bool(product.in_stock) if product else False,
            }
        )
    return {
        "type": "cart",
        "data": {
            "items": items_data,
            "total_price": round(total, 2),
            "item_count": len(items_data),
        },
    }


def checkout_card(
    cart_items: list[CartItem],
    *,
    sku_to_product: dict[str, Product],
    address: str,
    recipient_name: str | None,
    phone: str | None,
    base_url: str,
) -> dict[str, Any]:
    """checkout card(§4.7.5)。start_checkout tool 返回 — 准备下单的快照。

    与 cart card 的区别:多了 address / recipient_name / phone,表达 "准备寄到哪里"。
    前端收到后渲染 [去结算] 按钮 → OrderConfirmScreen → POST /order 真下单。

    sku_to_product 由 caller 预 JOIN 拼好传入,本函数不再做 DB 访问。
    """
    items_data: list[dict[str, Any]] = []
    total = 0.0
    for it in cart_items:
        product = sku_to_product.get(it.sku_id)
        sku = next(
            (s for s in (product.skus if product else []) if s.sku_id == it.sku_id),
            None,
        )
        unit_price = float(sku.price) if sku is not None else 0.0
        subtotal = unit_price * int(it.qty)
        total += subtotal
        items_data.append(
            {
                "sku_id": it.sku_id,
                "product_id": product.product_id if product else None,
                "title": product.title if product else "",
                "image_url": (
                    build_image_url(product.image_path, base_url=base_url)
                    if product
                    else None
                ),
                "qty": int(it.qty),
                "unit_price": unit_price,
                "subtotal": subtotal,
            }
        )
    return {
        "type": "checkout",
        "data": {
            "items": items_data,
            "address": address,
            "recipient_name": recipient_name,
            "phone": phone,
            "total_price": round(total, 2),
            "item_count": len(items_data),
        },
    }


def order_card(order: Order) -> dict[str, Any]:
    """order card(§4.7.5)。全字段从 Order 快照读 — 不再依赖 user_profile 实时值。"""
    items = list(order.items or [])
    return {
        "type": "order",
        "data": {
            "order_id": order.order_id,
            "status": order.status,
            "items": items,
            "address": order.address,
            "recipient_name": order.recipient_name,
            "phone": order.phone,
            "total_price": float(order.total_price),
            "created_at": (
                order.created_at.isoformat() + "Z"
                if isinstance(order.created_at, datetime)
                else str(order.created_at)
            ),
        },
    }


__all__ = [
    "build_image_url",
    "cart_card",
    "checkout_card",
    "order_card",
    "product_card",
    "product_summary_from_db",
    "product_summary_from_ranked",
]
