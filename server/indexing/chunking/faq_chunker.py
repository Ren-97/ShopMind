"""chunk_faq 文本拼接(§4.5.3,每条 FAQ 独立)。"""

from __future__ import annotations


def build_faq_chunk_text(question: str, answer: str) -> str:
    return f"问: {question.strip()}\n答: {answer.strip()}"


__all__ = ["build_faq_chunk_text"]
