"""Reranker(§4.3)。

- `Reranker` Protocol + `RankedProduct` 输出契约
- V1 默认 `LLMReranker`(Claude Haiku 4.5 + Tool Use,DB enrich + 阈值过滤)
- 返回为空 = no_match,上游 Agent 触发 "没找到符合的" 文案(§4.3.5)
"""

from server.rag.reranking.llm_reranker import LLMReranker
from server.rag.reranking.protocol import RankedProduct, Reranker

__all__ = ["Reranker", "RankedProduct", "LLMReranker"]
