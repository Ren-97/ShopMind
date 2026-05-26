"""chunk_review:规则过滤(§4.5.2 Step 1)+ chunk 文本拼接(Step 3)。

LLM sentiment 标注(Step 2)在 server.llm.review_sentiment;
真正的 embed + upsert 由 ingest 主流程串联。
"""

from __future__ import annotations

import re
from collections import Counter

from server import config

# CJK 中文:基本汉字(U+4E00–U+9FFF)+ 扩展 A(U+3400–U+4DBF);re.search 找到首个匹配即返回
_CHINESE_REGEX = re.compile(r"[一-鿿㐀-䶿]")


def _has_chinese(text: str) -> bool:
    return bool(_CHINESE_REGEX.search(text))


def _dup_char_ratio(text: str) -> float:
    """单字符最高占比(去空白)。0.0 表示无重复主导,1.0 表示全是同一个字符。"""
    stripped = "".join(ch for ch in text if not ch.isspace())
    if not stripped:
        return 1.0
    counter = Counter(stripped)
    top_count = counter.most_common(1)[0][1]
    return top_count / len(stripped)


def is_quality_review(content: str) -> bool:
    """§4.5.2 Step 1 规则:长度 ≥ MIN / 单字符占比 < MAX / 含汉字。

    用 config 值,不写死阈值。
    """
    text = (content or "").strip()
    if len(text) < config.REVIEW_MIN_LENGTH:
        return False
    if _dup_char_ratio(text) >= config.REVIEW_DUP_CHAR_RATIO_MAX:
        return False
    if not _has_chinese(text):
        return False
    return True


def build_review_chunk_text(*, rating: int, nickname: str, content: str) -> str:
    """§4.5.2 Step 3 — 拼 chunk 文本。"""
    return f"[{rating}星] {nickname.strip()}: {content.strip()}"


__all__ = ["is_quality_review", "build_review_chunk_text"]
