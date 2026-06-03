"""
SQLAlchemy v2 ORM 模型(对应 docs/design.md §4.4)。

D1 商品族:products(含 JSONB properties)/ skus / product_faqs /
            product_reviews / product_caveats
D2 用户态:users / user_profile / cart_items / orders / chat_history
D3 索引衍生:ingest_manifest

约定:
- 所有"用户态"表(D2)主键 / 外键含 user_id;Repo 层 SQL 永远 WHERE user_id=?
- products.is_active / in_stock 是防幻觉铁律 1 的硬过滤字段
- chat_history.session_id 由前端生成,不另建 sessions 表
- 类目特化属性(suitable_skin / cpu / gender / ...)进 products.properties JSONB,
  GIN 索引支持 `@>` 操作符 contains 查询
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from server.storage.db import Base


# ═════════════════════════════════════════════════════════════
# D1 商品族(只读,从 JSON ingest)
# ═════════════════════════════════════════════════════════════
class Product(Base):
    """商品主表。

    类目特化属性(suitable_skin / contains_alcohol / gender / cpu_model / ...)
    全部进 `properties` JSONB 字段,加新类目零 schema 改动。
    JSONB + GIN 索引支持快速 @> 查询(标量等值 + 数组 contains 都走索引)。
    """

    __tablename__ = "products"

    product_id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    brand: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False)
    sub_category: Mapped[str] = mapped_column(String, nullable=False)
    base_price: Mapped[float] = mapped_column(Float, nullable=False)
    image_path: Mapped[str | None] = mapped_column(String, nullable=True)
    in_stock: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    marketing_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 类目特化属性总仓库(JSONB,GIN 索引)
    # 例:{"suitable_skin": ["敏感肌"], "contains_alcohol": false, "age_group": "25+",
    #      "gender": "通用", "cpu": "i7", ...}
    properties: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False
    )

    skus: Mapped[list[SKU]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )
    faqs: Mapped[list[ProductFAQ]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )
    reviews: Mapped[list[ProductReview]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )
    caveats: Mapped[ProductCaveats | None] = relationship(
        back_populates="product", cascade="all, delete-orphan", uselist=False
    )

    __table_args__ = (
        Index("idx_products_brand", "brand"),
        Index("idx_products_category", "category", "sub_category"),
        Index("idx_products_price", "base_price"),
        Index(
            "idx_product_filter",
            "category", "sub_category", "base_price", "in_stock", "is_active",
        ),
        # GIN 索引让 properties @> '{...}' 查询走索引(标量 + 数组 contains 都生效)
        Index("idx_products_properties", "properties", postgresql_using="gin"),
    )


class SKU(Base):
    __tablename__ = "skus"

    sku_id: Mapped[str] = mapped_column(String, primary_key=True)
    product_id: Mapped[str] = mapped_column(
        ForeignKey("products.product_id", ondelete="CASCADE"), nullable=False
    )
    properties: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)

    product: Mapped[Product] = relationship(back_populates="skus")

    __table_args__ = (
        Index("idx_skus_product", "product_id"),
        Index("idx_skus_price", "price"),
    )


class ProductFAQ(Base):
    __tablename__ = "product_faqs"

    faq_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[str] = mapped_column(
        ForeignKey("products.product_id", ondelete="CASCADE"), nullable=False
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    order_idx: Mapped[int] = mapped_column(Integer, nullable=False)

    product: Mapped[Product] = relationship(back_populates="faqs")

    __table_args__ = (
        Index("idx_faqs_product", "product_id"),
    )


class ProductReview(Base):
    """用户评论 — 原始字段 + LLM 派生信号(sentiment / aspects)同表存放。

    架构定位:
    - SQL 是单一真相源(含 LLM 派生信号)
    - Qdrant payload 复刻 sentiment / aspects 仅为查询性能(零 round-trip 过滤)
    - 删 Qdrant 重建时,LLM 0 重调,从 SQL 直接读
    sentiment / aspects 为 NULL 表示该 review 尚未走 Haiku 分类
    (规则过滤未通过 / 错误降级 / 旧数据未迁移)。
    """

    __tablename__ = "product_reviews"

    review_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[str] = mapped_column(
        ForeignKey("products.product_id", ondelete="CASCADE"), nullable=False
    )
    nickname: Mapped[str] = mapped_column(String, nullable=False)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # LLM 派生信号(§4.5.2 Step 2)
    sentiment: Mapped[float | None] = mapped_column(Float, nullable=True)
    aspects: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    product: Mapped[Product] = relationship(back_populates="reviews")

    __table_args__ = (
        CheckConstraint("rating BETWEEN 1 AND 5", name="ck_reviews_rating_range"),
        CheckConstraint(
            "sentiment IS NULL OR (sentiment BETWEEN -1.0 AND 1.0)",
            name="ck_reviews_sentiment_range",
        ),
        Index("idx_reviews_product", "product_id"),
    )


class ProductCaveats(Base):
    """LLM 离线抽出的 caveats(每商品 1 条,可空表示无负面信号)。"""

    __tablename__ = "product_caveats"

    product_id: Mapped[str] = mapped_column(
        ForeignKey("products.product_id", ondelete="CASCADE"), primary_key=True
    )
    caveats_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False
    )

    product: Mapped[Product] = relationship(back_populates="caveats")


# ═════════════════════════════════════════════════════════════
# D2 用户态(全部带 user_id)
# ═════════════════════════════════════════════════════════════
class User(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False
    )

    profile: Mapped[UserProfile | None] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )


class UserProfile(Base):
    __tablename__ = "user_profile"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), primary_key=True
    )
    # 基础人口学
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gender: Mapped[str | None] = mapped_column(String, nullable=True)  # male/female
    height_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 消费倾向(soft 排序信号)
    consumption_tier: Mapped[str | None] = mapped_column(String, nullable=True)
    # 收货
    recipient_name: Mapped[str | None] = mapped_column(String, nullable=True)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 类目特化偏好(JSON 扩展)
    preferences: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="profile")


class CartItem(Base):
    __tablename__ = "cart_items"

    cart_item_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    sku_id: Mapped[str] = mapped_column(
        ForeignKey("skus.sku_id"), nullable=False
    )
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    added_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("qty > 0", name="ck_cart_qty_positive"),
        UniqueConstraint("user_id", "sku_id", name="uq_cart_user_sku"),
        Index("idx_cart_user", "user_id"),
    )


class Order(Base):
    """订单 — 下单时**快照**写入,后续 user_profile 改动不影响历史订单。

    收货三件套(address / recipient_name / phone)都是快照 — 用户改名 / 改电话 / 改地址
    后,旧订单卡片显示的应该仍是下单当时的值。
    """

    __tablename__ = "orders"

    order_id: Mapped[str] = mapped_column(String, primary_key=True)  # UUID
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String, nullable=False)  # pending/confirmed/cancelled
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    recipient_name: Mapped[str | None] = mapped_column(String, nullable=True)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    total_price: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False
    )

    __table_args__ = (
        Index("idx_orders_user", "user_id", "created_at"),
    )


class ChatHistory(Base):
    __tablename__ = "chat_history"

    msg_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)  # user/assistant/tool
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tool_calls: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    # Card 引用(B+:历史里渲染卡片用 — 不存完整 JSON 快照,只存 ID,实时拉最新数据)
    # 结构:{"products": ["p_x", ...], "compare": ["p_a", ...], "order": "ord-xxx"}
    card_refs: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False
    )

    __table_args__ = (
        Index("idx_chat_user_session", "user_id", "session_id", "created_at"),
    )


# ═════════════════════════════════════════════════════════════
# D3 索引衍生
# ═════════════════════════════════════════════════════════════
class IngestManifest(Base):
    """增量 ingest 账本(per-product hash + Qdrant chunk 数)。

    两级 hash:
    - content_hash:整文件 sha256,决定是否进入 _sync_product(粗筛)
    - main_chunk_hash:main chunk 文本 sha256,在 _sync_product 内决定是否重 embed main
      (与 review identity-diff / faq 列表对比 一同构成 per-chunk 细粒度短路)
    """

    __tablename__ = "ingest_manifest"

    product_id: Mapped[str] = mapped_column(
        ForeignKey("products.product_id", ondelete="CASCADE"), primary_key=True
    )
    content_hash: Mapped[str] = mapped_column(String, nullable=False)  # sha256(json)
    main_chunk_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False)
    last_ingested_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.current_timestamp(), nullable=False
    )


__all__ = [
    # D1
    "Product", "SKU", "ProductFAQ", "ProductReview", "ProductCaveats",
    # D2
    "User", "UserProfile", "CartItem", "Order", "ChatHistory",
    # D3
    "IngestManifest",
]
