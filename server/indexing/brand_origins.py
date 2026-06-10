"""品牌产地归属(数据治理,反选用)。

用户常按"产地/系别"反选——"不要日系""别给我韩系""只看国货"。产地不是商品的
结构化字段(dataset 里没有),也不能指望 Planner LLM 可靠枚举"哪些牌子是日系"。
所以这里维护一张**人工策展**的 品牌→产地 表:Planner 只需把"日系"抽进
`origin_exclude`(闭集),代码在检索前把它展开成具体 `brand_exclude` 走 SQL 硬过滤。

设计原则(对齐 brand_aliases.py):
- **Single Source of Truth**:产地归属只写这一处。
- **不让 LLM 做归类**:LLM 抽闭集"日系/韩系/欧美/国货",品牌枚举给代码。
- **策展、宁缺毋滥**:反选误伤(把不属于该产地的牌子排掉)比漏掉更伤信任。
  归属有争议的(台系 康师傅/统一、泰系 红牛)**不登记** → 不会被任何产地反选误杀。
- 键用**规范品牌名**(DB ingest 后的写法);展开结果下游仍过 normalize_brand,
  英文别名(Nike→耐克)无所谓。

产地闭集 = {日系, 韩系, 欧美, 国货}(中文电商最常用的反选轴)。
覆盖当前 ecommerce_agent_dataset 全量品牌(争议项除外)。
"""

from __future__ import annotations

from collections.abc import Iterable


# 规范品牌名 → 产地(每个品牌单一归属;争议品牌不登记)
_BRAND_ORIGIN: dict[str, str] = {
    # ── 日系 ──
    "SK-II": "日系",
    "资生堂": "日系",
    "安热沙": "日系",       # Anessa,资生堂旗下
    "珊珂": "日系",         # Senka,资生堂旗下
    "芳珂": "日系",         # Fancl
    "优衣库": "日系",       # Uniqlo
    "日清": "日系",         # Nissin
    # ── 韩系 ──
    "AHC": "韩系",
    # ── 欧美 ──
    "兰蔻": "欧美",          # Lancôme(法)
    "巴黎欧莱雅": "欧美",    # L'Oréal(法)
    "玉兰油": "欧美",        # Olay(美)
    "理肤泉": "欧美",        # La Roche-Posay(法)
    "科颜氏": "欧美",        # Kiehl's(美)
    "雅诗兰黛": "欧美",      # Estée Lauder(美)
    "The Ordinary": "欧美",  # Deciem(加)
    "Apple 苹果": "欧美",
    "耐克": "欧美",          # Nike(美)
    "阿迪达斯": "欧美",      # adidas(德)
    "北面": "欧美",          # The North Face(美)
    "始祖鸟": "欧美",        # Arc'teryx(加)
    "萨洛蒙": "欧美",        # Salomon(法)
    "迈乐": "欧美",          # Merrell(美)
    "迪卡侬": "欧美",        # Decathlon(法)
    "露露乐蒙": "欧美",      # lululemon(加)
    "HOKA": "欧美",
    "Osprey": "欧美",        # (美)
    "可口可乐": "欧美",      # Coca-Cola(美)
    "雀巢": "欧美",          # Nestlé(瑞士)
    # ── 国货 ──
    "完美日记": "国货",
    "方里": "国货",
    "珀莱雅": "国货",
    "花西子": "国货",
    "薇诺娜": "国货",
    "OPPO": "国货",
    "vivo": "国货",
    "华为": "国货",
    "小米": "国货",
    "联想": "国货",
    "安踏": "国货",
    "李宁": "国货",
    "特步": "国货",
    "三只松鼠": "国货",
    "三顿半": "国货",
    "东方树叶": "国货",
    "东鹏": "国货",
    "伊利": "国货",
    "元气森林": "国货",
    "农夫山泉": "国货",
    "李锦记": "国货",
    "海天": "国货",
    "百草味": "国货",
    "纯甄": "国货",
    "良品铺子": "国货",
    "蒙牛": "国货",
    "金典": "国货",
    "康师傅": "国货",
    "统一": "国货",
    "红牛": "国货",
}

# 产地 → 该产地全部品牌(反查,模块载入时一次性建好)
_ORIGIN_BRANDS: dict[str, list[str]] = {}
for _brand, _origin in _BRAND_ORIGIN.items():
    _ORIGIN_BRANDS.setdefault(_origin, []).append(_brand)


def brands_for_origins(origins: Iterable[str]) -> list[str]:
    """把产地列表展开成具体品牌名列表(去重保序)。

    未登记的产地 → 贡献空列表(下游照常,只是没东西可排)。
    """
    out: list[str] = []
    for origin in origins:
        out.extend(_ORIGIN_BRANDS.get(origin, []))
    return list(dict.fromkeys(out))


def known_brands() -> set[str]:
    """已登记的全部规范品牌名(供反选文本扫描做词表)。"""
    return set(_BRAND_ORIGIN)


__all__ = ["brands_for_origins", "known_brands"]
