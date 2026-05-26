"""chunk_main 文本拼接(§4.5.3)。

来源:products(基础列)+ products.properties JSONB(任意类目特化字段)。
每商品 1 条,广义匹配用。

设计原则(跨类目通用,加新类目零改动):
- 遍历 properties 的所有 key,不写死任何字段名
- bool 字段跳过(交给 SQL filter,避免否定词污染 dense embedding)
- list / 标量(str / int / float)都进文本
- key 直接用 JSON 字段名(suitable_skin / cpu / material ...),检索靠 value
  token 命中,中文 label 与否对 BM25/Dense 召回无显著差异
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def build_main_chunk_text(
    *,
    title: str,
    brand: str,
    category: str,
    sub_category: str,
    properties: Mapping[str, Any],
    marketing_description: str | None,
) -> str:
    """拼 chunk_main:核心字段固定 + properties 全量遍历。

    properties: products.properties JSONB(任意结构,如
                {"suitable_skin": ["敏感肌"], "age_group": "25+",
                 "contains_alcohol": False, "cpu": "i7", ...})
    """
    lines: list[str] = [
        title.strip(),
        f"品牌: {brand}",
        f"类目: {category} > {sub_category}",
    ]

    # 遍历所有 properties,跨类目自动适配(不写死任何字段名)
    for key, value in properties.items():
        if isinstance(value, bool):
            # bool 字段不进 chunk(→ SQL filter 专属;否定词在 dense embedding
            # 里和肯定词太近,放进文本反而引入反向匹配噪声)
            continue
        if isinstance(value, list):
            joined = "、".join(str(v) for v in value if v not in (None, ""))
            if joined:
                lines.append(f"{key}: {joined}")
        elif value not in (None, ""):
            lines.append(f"{key}: {value}")

    if marketing_description:
        lines.append(f"描述: {marketing_description.strip()}")

    return "\n".join(lines)


__all__ = ["build_main_chunk_text"]
