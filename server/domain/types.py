"""跨模块共享的 Pydantic 类型(§4.1 / §4.2 / §4.3 共用)。

设计原则:
- `HardConstraints` 全部字段 first-class + `Literal` 闭集 — Schema-constrained
  decoding,LLM 无法编造闭集外的值(防 key/value drift,§4.2)。
- 开放词表(effects / scene / style / recipient / taste 等)放 `soft_preferences`,
  走 hybrid retrieval + reranker 软打分,**不进 SQL**。
- 类目特化闭集字段:仅相关类目用(美妆用 suitable_skin/contains_alcohol/...,
  服饰用 gender 等),其它类目应该留 None。
- 层级关系(gender "男"→{"男","通用"}、age_group "25+"→{"25+","通用"})在
  Repo SQL 拼装时展开,不进 schema(避免污染 Planner 输出)。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ─────────────────────────────────────────────────────────────
# 闭集 enum(Schema-constrained 防 drift,LLM token-level 强制)
# 这些值由真实 ecommerce_agent_dataset 全量扫描得到(详见 chunk4 实施记录)
# ─────────────────────────────────────────────────────────────
SuitableSkin = Literal["敏感肌", "干皮", "油皮", "混油皮", "中性肌"]
Gender = Literal["男", "女", "通用"]
AgeGroup = Literal["20+", "25+", "30+", "通用"]
# 品牌产地/系别(反选用):闭集只锁"轴",具体品牌枚举在 brand_origins.py 由代码展开
Origin = Literal["日系", "韩系", "欧美", "国货"]


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
    """SQL 可过滤的硬约束(first-class 字段 + Literal 闭集,防幻觉铁律 1)。

    所有闭集字段走 Literal,LLM 输出 token-level 强制 → 不可能漂到闭集外值。
    新增闭集字段流程:本类加一个 first-class field + 闭集 Literal → 改 Repo
    SQL → 改 Planner prompt 说明。**不要**用开放 dict(properties_contains),
    那会重新打开 drift surface。
    """

    model_config = ConfigDict(extra="forbid")

    # ── 通用骨架(所有类目) ──
    category: str | None = None
    sub_category: str | None = None
    brand: str | None = None
    brand_exclude: list[str] = Field(default_factory=list)
    # 按产地/系别反选(如"不要日系"):检索前由 brand_origins 展开并入 brand_exclude
    origin_exclude: list[Origin] = Field(default_factory=list)
    price_min: float | None = None
    price_max: float | None = None
    in_stock: bool | None = None

    # ── 美妆护肤特化闭集 ──
    suitable_skin: list[SuitableSkin] = Field(default_factory=list)
    contains_alcohol: bool | None = None
    contains_fragrance: bool | None = None
    age_group: AgeGroup | None = None

    # ── 服饰运动特化闭集 ──
    gender: Gender | None = None

    def is_empty(self) -> bool:
        """无任何过滤条件 → Dispatcher 退化为 Pure Semantic。"""
        return (
            self.category is None
            and self.sub_category is None
            and self.brand is None
            and not self.brand_exclude
            and not self.origin_exclude
            and self.price_min is None
            and self.price_max is None
            and self.in_stock is None
            and not self.suitable_skin
            and self.contains_alcohol is None
            and self.contains_fragrance is None
            and self.age_group is None
            and self.gender is None
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
    chunk_type: str  # main / faq / review
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
    # Planner schema
    "QueryType",
    "SuitableSkin",
    "Gender",
    "AgeGroup",
    "HardConstraints",
    "QueryPlan",
    # 检索输出
    "ChunkHit",
    "MatchedChunk",
    "ProductHit",
    "RetrievalResult",
]
