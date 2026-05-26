"""Sparse Encoder(§4.5.2 Step 5:jieba 中文分词 + fastembed BM25)。"""

from server.rag.sparse.bm25_jieba import JiebaBM25Encoder
from server.rag.sparse.protocol import SparseEncoder, SparseVector

__all__ = ["SparseEncoder", "SparseVector", "JiebaBM25Encoder"]
