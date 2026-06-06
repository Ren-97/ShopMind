"""Reranker 接口 + 输出契约(§4.3)。

接口抽象,V1 实现 `LLMReranker`,V2 可换 Cross-Encoder / CascadingReranker,
业务层零改动(`Reranker.rerank(query, hits) -> list[RankedProduct]`)。

`RankedProduct` 是 Reranker 阶段后的"权威"商品结构 — 字段全部来自 Catalog DB enrich,
LLM 在生成阶段只能复述这些字段,**不读 matched_chunks 原文回答属性**(防幻觉铁律 3)。
matched_chunks 仍保留作为命中证据 / 引用(可在生成时标注 "根据用户评论 ...")。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from server.domain.types import MatchedChunk, ProductHit


class RankedProduct(BaseModel):
    """Reranker 输出的单个商品 — DB enrich + LLM 评分 + 命中证据。"""

    model_config = ConfigDict(extra="forbid")

    # ── 主键 + 评分 ──
    product_id: str
    relevance_score: float = Field(ge=0.0, le=1.0)
    reason: str | None = None

    # ── DB enrich(权威字段,防幻觉) ──
    title: str
    brand: str
    category: str
    sub_category: str
    base_price: float
    in_stock: bool
    image_path: str | None = None
    # 类目特化属性总仓库(JSONB):suitable_skin / contains_alcohol / cpu / ...
    properties: dict[str, Any] = Field(default_factory=dict)
    # 离线 LLM 抽出的客观警示信号(可为 null = 无负面信号)
    caveats_text: str | None = None
    # SKU 列表 — Agent 加购时按 properties(尺码/容量/颜色)反查 sku_id
    skus: list[dict[str, Any]] = Field(default_factory=list)

    # ── 命中证据(原始 chunks,用于"根据用户评论..."类引用) ──
    matched_chunks: list[MatchedChunk] = Field(default_factory=list)


@runtime_checkable
class Reranker(Protocol):
    """Reranker 接口:消费 (query, ProductHit 列表) → 阈值过滤后的 RankedProduct 列表。

    返回为空 = no_match → 上游 Agent 触发 "没找到符合的" 兜底文案(§4.3.5)。
    """

    name: str  # observability

    async def rerank(
        self,
        query: str,
        hits: list[ProductHit],
        *,
        profile: dict[str, Any] | None = None,
    ) -> list[RankedProduct]:
        ...


__all__ = ["Reranker", "RankedProduct"]
