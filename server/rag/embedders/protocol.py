"""Embedder Protocol(§4.1 第 5 原则:组件接口化)。

V1 唯一实现 OpenAIEmbedder;接口预留 V2 切换(本地模型 / Cohere / ...)零业务改动。
"""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable


@runtime_checkable
class Embedder(Protocol):
    """Dense 文本嵌入接口。"""

    dimension: int

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """批量编码 → 每条文本一个 dimension 维向量。

        - 输入空列表 → 返回空列表
        - 单条文本也走批接口(简化调用方,内部自行优化)
        """
        ...
