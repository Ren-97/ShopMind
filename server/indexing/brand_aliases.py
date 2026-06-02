"""Brand 别名归一化(数据治理)。

dataset 自身有命名不一致(Nike vs 耐克 / The North Face vs 北面 / 苹果 vs Apple 苹果),
若不规范化,SQL `WHERE brand IN / NOT IN` 会半生效:
  例:用户说"不要 Nike",Planner 抽 brand_exclude=["Nike"],SQL 漏掉 brand="耐克" 的 4 个商品。

设计原则:
- **Single Source of Truth**:同一个 _BRAND_ALIAS_MAP 同时给 Ingest (L1) 和 SQL 拼接 (L3) 用
- **不让 LLM 做 normalize**:Planner LLM 只老实抽用户原话,代码层做硬归一
  (规则化任务给 code,意图理解给 LLM)
- 没在 map 里的品牌按原值返回(开放扩展,不影响新品牌)

规范名选取原则:dataset 中商品数最多的写法(让数据迁移成本最低)。
"""

from __future__ import annotations


_BRAND_ALIAS_MAP: dict[str, str] = {
    # Nike 系列 → 耐克(dataset 4 个商品已用规范名,Nike 2 个英文别名归一)
    "Nike": "耐克",
    "NIKE": "耐克",
    "nike": "耐克",
    # The North Face 系列 → 北面(2 个商品各占一半,选中文电商习惯用语)
    "The North Face": "北面",
    "TNF": "北面",
    # Apple 系列 → Apple 苹果(dataset 8 个用此混合形式,1 个用"苹果")
    "苹果": "Apple 苹果",
    "Apple": "Apple 苹果",
    "APPLE": "Apple 苹果",
    "apple": "Apple 苹果",
    # adidas → 阿迪达斯(dataset 已统一,但用户可能输英文别名)
    "adidas": "阿迪达斯",
    "Adidas": "阿迪达斯",
    "ADIDAS": "阿迪达斯",
}


def normalize_brand(raw: str | None) -> str | None:
    """归一品牌名到规范形式。

    用法:
    - Ingest 时:DB 写入前 normalize(L1,一次性数据治理)
    - SQL 拼接前:Planner 输出的 brand / brand_exclude normalize(L3 兜底)

    raw=None / 空字符串 → 原样返回(下游决定怎么处理 null)
    map 命中 → 返回规范名
    map 未命中 → 返回原值(开放扩展)
    """
    if not raw:
        return raw
    return _BRAND_ALIAS_MAP.get(raw, raw)


__all__ = ["normalize_brand"]
