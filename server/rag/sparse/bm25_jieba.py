"""jieba 中文分词 + fastembed BM25 实现(§4.5.2 Step 5)。

实现策略:
- jieba.lcut 把中文切成词列表
- 用空格拼接后喂给 fastembed Bm25(它的默认分词器按空白切)
- fastembed 是 sync,通过 asyncio.to_thread 包装成 async
- 模型懒加载(首次 encode 时下载到本地 cache,后续走本地)
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from typing import Any

import jieba

from server import config
from server.rag.sparse.protocol import SparseVector

# jieba 默认词典已经够用;若需自定义可在此 jieba.load_userdict(...)
_NON_TOKEN_RE = re.compile(r"[\s　]+")  # 多空格 / 全角空格


def _pre_tokenize(text: str) -> str:
    """jieba 分词后用单空格拼接 — 让 fastembed BM25 的空白分词器吃。"""
    tokens = [tok for tok in jieba.lcut(text) if tok and not _NON_TOKEN_RE.fullmatch(tok)]
    return " ".join(tokens)


class JiebaBM25Encoder:
    """中文 sparse encoder:jieba tokenize → fastembed BM25。"""

    def __init__(self, model_name: str | None = None) -> None:
        # 懒加载:避免 import 时阻塞 / 下载模型
        self._model_name: str = model_name or config.SPARSE_MODEL_NAME
        self._model: Any | None = None

    def _ensure_model(self) -> Any:
        if self._model is None:
            from fastembed import SparseTextEmbedding

            self._model = SparseTextEmbedding(model_name=self._model_name)
        return self._model

    def _encode_sync(self, texts: list[str]) -> list[SparseVector]:
        pre = [_pre_tokenize(t) for t in texts]
        model = self._ensure_model()
        out: list[SparseVector] = []
        for emb in model.embed(pre):
            # fastembed.SparseEmbedding has .indices (np.array) and .values (np.array)
            indices = [int(i) for i in emb.indices.tolist()]
            values = [float(v) for v in emb.values.tolist()]
            out.append(SparseVector(indices=indices, values=values))
        return out

    async def encode(self, texts: Sequence[str]) -> list[SparseVector]:
        if not texts:
            return []
        return await asyncio.to_thread(self._encode_sync, list(texts))
