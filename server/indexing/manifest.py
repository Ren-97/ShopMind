"""增量 ingest 的 hash 计算 + per-record diff(§4.5.5)。

设计原则:
- **identity 按内容 hash**:不依赖 JSON 里的 review_id / faq_id(可能不稳定),
  用 sha256(关键字段拼接)。两条内容完全一样的 review → 同一身份。
- product content_hash:对**规范化后的 JSON**算 sha256,顺序不敏感(dict key 排序)。
- diff helpers:返回 NEW / REMOVED / UNCHANGED 三类索引,主流程据此决定 SQL + Qdrant 动作。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Generic, TypeVar


# ─────────────────────────────────────────────────────────────
# 内容 hash:整个 product
# ─────────────────────────────────────────────────────────────
def compute_product_content_hash(raw: Mapping) -> str:
    """对 product JSON 算稳定 sha256(dict key 排序 + UTF-8 + no whitespace)。

    保证:同样的 JSON 内容(无论 key 顺序、缩进)→ 同样的 hash。
    """
    payload = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ─────────────────────────────────────────────────────────────
# 内容 hash:单条 review / faq(identity)
# ─────────────────────────────────────────────────────────────
def review_identity_hash(*, nickname: str, rating: int, content: str) -> str:
    """review identity = sha256(nickname|rating|content);跨次 ingest 稳定。"""
    raw = f"{nickname.strip()}|{rating}|{content.strip()}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def compute_main_chunk_hash(main_text: str) -> str:
    """main chunk 文本 sha256 — 短路 re-embed(整文件 hash 变但 main 字段未变时)。

    main_text 由 title / brand / category / sub_category / properties /
    marketing_description 拼出,因此 hash 稳定即可断定这些字段全部未变。
    """
    return hashlib.sha256(main_text.encode("utf-8")).hexdigest()


# ─────────────────────────────────────────────────────────────
# 通用 diff 结果
# ─────────────────────────────────────────────────────────────
T = TypeVar("T")


@dataclass(slots=True)
class DiffResult(Generic[T]):
    """NEW / REMOVED / UNCHANGED 三组项。

    new:  新 JSON 里有、SQL 里没有 → 需要插入 + chunk + embed + upsert
    removed: SQL 里有、新 JSON 里没有 → 需要 delete SQL + delete Qdrant chunk
    unchanged: 两边都有 → 零开销,跳过
    """

    new: list[T] = field(default_factory=list)
    removed: list[T] = field(default_factory=list)
    unchanged: list[T] = field(default_factory=list)

    @property
    def change_count(self) -> int:
        return len(self.new) + len(self.removed)


def diff_by_identity(
    old_ids: Sequence[str], new_ids: Sequence[str]
) -> DiffResult[str]:
    """按 identity hash 做集合差分,保持顺序去重。"""
    old_set = set(old_ids)
    new_set = set(new_ids)
    seen_new: set[str] = set()
    seen_removed: set[str] = set()
    seen_unchanged: set[str] = set()
    result: DiffResult[str] = DiffResult()
    for ident in new_ids:
        if ident in seen_new or ident in seen_unchanged:
            continue
        if ident in old_set:
            result.unchanged.append(ident)
            seen_unchanged.add(ident)
        else:
            result.new.append(ident)
            seen_new.add(ident)
    for ident in old_ids:
        if ident in seen_removed or ident in new_set:
            continue
        result.removed.append(ident)
        seen_removed.add(ident)
    return result


__all__ = [
    "compute_product_content_hash",
    "compute_main_chunk_hash",
    "review_identity_hash",
    "DiffResult",
    "diff_by_identity",
]
