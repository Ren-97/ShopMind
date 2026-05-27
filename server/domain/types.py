"""跨模块共享的 Pydantic 类型(§4.1 / §4.2 / §4.3 共用)。

设计偏离 design.md 说明:
- design.md §4.1.2 把 `hard_constraints` 写成松散 `dict`。本实现改为强类型
  `HardConstraints` 模型,理由:
    1. SQL 过滤字段(category/brand/price_*/in_stock)是 enum-like 集合,
       Pydantic 校验比手撸 dict 解析更稳;
    2. 防止 Planner LLM 编造未知字段名(strict `extra="forbid"`);
    3. 类目特化属性集中在 `properties_contains`(走 product.properties JSONB
       `@>` 操作符),与 first-class 列字段分层清晰。
  Planner prompt 在 Chunk 4 实施时按这个 schema 给 LLM tool_input_schema。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ─────────────────────────────────────────────────────────────
# Planner 输出
# ─────────────────────────────────────────────────────────────
QueryType = Literal[
    "structured",          # 纯结构化筛选,不调 embedding
    "id_lookup",           # 指名/上下文引用,直接按 ID 取
    "filtered_semantic",   # SQL 硬过滤 + Hybrid(默认兜底)
    "pure_semantic",       # 无约束 Hybrid
]


class HardConstraints(BaseModel):
    """SQL 可过滤的硬约束。

    实现"防幻觉铁律 1":检索 Repo 层用这些字段拼 SQL WHERE,**不交给 LLM**。
    """

    model_config = ConfigDict(extra="forbid")

    # First-class columns(products 表)
    category: str | None = None
    sub_category: str | None = None
    brand: str | None = None
    brand_exclude: list[str] = Field(default_factory=list)
    price_min: float | None = None
    price_max: float | None = None
    in_stock: bool | None = None
    # 类目特化属性 → product.properties JSONB(`@>` GIN 索引)
    # 例:{"suitable_skin": ["敏感肌"], "contains_alcohol": False}
    properties_contains: dict[str, Any] = Field(default_factory=dict)

    def is_empty(self) -> bool:
        """无任何过滤条件 → 退化为 Pure Semantic。"""
        return (
            self.category is None
            and self.sub_category is None
            and self.brand is None
            and not self.brand_exclude
            and self.price_min is None
            and self.price_max is None
            and self.in_stock is None
            and not self.properties_contains
        )


class QueryPlan(BaseModel):
    """Query Planner 输出(§4.1.2)。Chunk 4 由 Haiku 4.5 + Tool Use 产生。"""

    model_config = ConfigDict(extra="forbid")

    query_type: QueryType
    hard_constraints: HardConstraints = Field(default_factory=HardConstraints)
    soft_preferences: dict[str, Any] = Field(default_factory=dict)
    text_query: str | None = None
    referenced_product_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator("text_query")
    @classmethod
    def _strip_text_query(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v if v else None


# ─────────────────────────────────────────────────────────────
# 检索输出(策略 → Dispatcher → Reranker)
# ─────────────────────────────────────────────────────────────
class ChunkHit(BaseModel):
    """单 chunk 命中(Qdrant query_points 返回的最小单位)。"""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    score: float
    payload: dict[str, Any]


class MatchedChunk(BaseModel):
    """聚合到 product 后保留的 chunk 证据(给 Rerank / 生成用)。"""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    chunk_type: str  # main / faq / review / caveats
    text: str
    score: float


class ProductHit(BaseModel):
    """聚合后的 product 命中(score = max chunk score)。"""

    model_config = ConfigDict(extra="forbid")

    product_id: str
    score: float
    matched_chunks: list[MatchedChunk] = Field(default_factory=list)


class RetrievalResult(BaseModel):
    """RetrievalDispatcher 的最终输出。"""

    model_config = ConfigDict(extra="forbid")

    products: list[ProductHit]
    strategy: str  # 实际命中的策略名(observability)


__all__ = [
    "QueryType",
    "HardConstraints",
    "QueryPlan",
    "ChunkHit",
    "MatchedChunk",
    "ProductHit",
    "RetrievalResult",
]
