"""chunk hits → product hits 聚合(§4.1.10)。

策略:max chunk score 作为 product score,保留所有命中 chunks 给后续 Rerank / 生成。
"""

from __future__ import annotations

from collections.abc import Iterable

from server.domain.types import ChunkHit, MatchedChunk, ProductHit


def aggregate_chunks_to_products(
    chunks: Iterable[ChunkHit],
    *,
    top_n: int,
    coarse_threshold: float = 0.0,
) -> list[ProductHit]:
    """按 product_id group → max score 排序 → 取 top_n。

    - `coarse_threshold` 过滤融合分极低的尾部噪声 chunk(送 LLM 重排前的成本兜底),0 时不过滤
    - 保留 chunk 出现顺序(Qdrant 已按融合分排序),便于后续按 chunk_type 加权
    """
    by_product: dict[str, ProductHit] = {}
    for c in chunks:
        if c.score < coarse_threshold:
            continue
        payload = c.payload
        pid = payload.get("product_id")
        if not pid:
            # 防御:payload 缺 product_id 的 chunk 不可用,跳过
            continue
        matched = MatchedChunk(
            chunk_id=c.chunk_id,
            chunk_type=str(payload.get("chunk_type", "")),
            text=str(payload.get("text", "")),
            score=c.score,
        )
        hit = by_product.get(pid)
        if hit is None:
            by_product[pid] = ProductHit(
                product_id=pid, score=c.score, matched_chunks=[matched]
            )
        else:
            hit.matched_chunks.append(matched)
            if c.score > hit.score:
                hit.score = c.score

    return sorted(by_product.values(), key=lambda h: -h.score)[:top_n]


__all__ = ["aggregate_chunks_to_products"]
