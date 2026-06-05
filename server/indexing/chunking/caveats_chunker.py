"""chunk_caveats 文本拼接(§4.5.3)。

caveats 走 LLM 离线抽取(server.llm.review_summarizer),
本模块只负责"已抽出的文本"→"chunk text"的字符串拼装。
"""

from __future__ import annotations


def build_caveats_chunk_text(caveats_text: str) -> str:
    return f"⚠️ 注意: {caveats_text.strip()}"


__all__ = ["build_caveats_chunk_text"]
