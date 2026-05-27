"""Adaptive Retrieval(§4.1)。

- `dispatcher` 按 QueryPlan.query_type 派发到对应策略,异常 / 低置信兜底到 default
- `strategies/` 4 个具体策略类,各自只关心自己的数据流
- `aggregation` 把 Qdrant chunk hits 聚合成 product hits(§4.1.10)
"""

from server.rag.retrieval.aggregation import aggregate_chunks_to_products
from server.rag.retrieval.dispatcher import (
    RetrievalDispatcher,
    RetrievalError,
    build_default_dispatcher,
)

__all__ = [
    "RetrievalDispatcher",
    "RetrievalError",
    "build_default_dispatcher",
    "aggregate_chunks_to_products",
]
