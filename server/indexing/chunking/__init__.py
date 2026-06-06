"""Chunking 子包:main / faq / review 三种 chunk 类型(§4.5.1 / §4.5.2)。

caveats 不切 chunk —— 负面摘要只存 SQL(review_summary),详情页 / 对比 / reranker
读 DB 字段,推荐侧不下发,杜绝缺点漏进 matched_chunks。
"""

from server.indexing.chunking.faq_chunker import build_faq_chunk_text
from server.indexing.chunking.main_chunker import build_main_chunk_text
from server.indexing.chunking.review_chunker import (
    build_review_chunk_text,
    is_quality_review,
)

__all__ = [
    "build_main_chunk_text",
    "build_faq_chunk_text",
    "build_review_chunk_text",
    "is_quality_review",
]
