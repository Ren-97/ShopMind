"""Sparse Encoder Protocol(§4.1 + §4.5.2 Step 5)。

V1 = jieba(中文分词)+ fastembed `Qdrant/bm25`。
  - fastembed 输出的 weight 是"IDF × TF"合并值,IDF 用 fastembed 通用语料冻结的统计
  - 优势:开箱即用,增量 ingest 友好(每个 chunk 的稀疏向量独立,加新 chunk 不动旧的)
  - 代价:IDF 是通用语料统计,跟电商域可能微偏(V1 100 商品 sample 阶段差异可忽略)

V2 升级路径(本接口预留,业务代码零改动):

  ① **Qdrant `Modifier.IDF`(域内动态 IDF)**
     - 自写一个 sparse encoder 只输出 **raw TF**(`{token_id: count}`)
     - Qdrant collection 配 `SparseVectorParams(modifier=Modifier.IDF)`
     - Qdrant 服务端维护 document frequency,query 时实时算 IDF
     - 收益:IDF 100% 贴合电商域(如"雅诗兰黛"在美妆类目实际 DF)+ 仍然增量友好
     - 切换成本:~30-50 行(jieba 分词 → Counter → mmh3 哈希 token→ID → SparseVector);
       注意 token→ID 必须跨进程确定性,query/index 两路对称

  ② **SPLADE / ColBERT 类神经稀疏模型**
     - 用神经网络做 token expansion(把"敏感肌"映射出 ["敏感","肌肤","刺激","泛红",...] 的加权稀疏)
     - 比 BM25 召回质量高一档,但需要 GPU 推理 + 增量复杂
     - 一般 10k+ 商品 / 检索质量瓶颈在 BM25 时才上

  ③ **自训 IDF**(BM25 corpus-trained)
     - 按本商城历史 chunks 全量统计 DF → 算 IDF 表 → 写一个 corpus-IDF encoder
     - 比 Modifier.IDF 更可控(可周期 retrain),但失去增量性 — 加新 chunk 后旧 chunk 的 IDF 概念变化
     - 一般离线批处理跑

切换时机:跑完 eval 看 sparse 召回质量瓶颈在哪 — 域内罕见词召不到 → ①;长尾语义 → ②。
不是 V1 必做项,数据驱动调优(CLAUDE.md §5)。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(slots=True)
class SparseVector:
    """Qdrant sparse vector 表示(indices + values 对应位置非零项)。"""

    indices: list[int]
    values: list[float]


@runtime_checkable
class SparseEncoder(Protocol):
    """BM25 风格的稀疏文本编码接口。"""

    async def encode(self, texts: Sequence[str]) -> list[SparseVector]:
        """批量编码 → 每条文本一个 SparseVector。空列表 → 空返回。"""
        ...
