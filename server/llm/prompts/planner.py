"""Query Planner Prompt 文案(§4.2.5)。

输出端用 Claude Tool Use 强制 `QueryPlan` 结构化输出,本文件只放:
- `PLANNER_SYSTEM_PROMPT`:静态规则(query_type 判别 / 字段说明 / 绝对禁令)
- `PLANNER_FEW_SHOT_MESSAGES`:5 个 case 横跨 4 大类目(美妆/数码/服饰/食品),
  覆盖 filtered_semantic / structured / pure_semantic + 反选(brand_exclude)+
  低置信场景(送礼模糊偏好 → 0.65),字段值取自真实 dataset(brand/sub_category
  原样使用,降低 LLM 分类抖动)。assistant 用 tool_use 块贴近真实 Anthropic
  协议。`id_lookup` 不出现在 few-shot — 它依赖 session_state 动态注入(走
  _render_context 拼进 system prompt),静态 few-shot 无法演示;其触发规则
  用文字写在 system prompt # 上下文使用 章节。

设计原则:
- 静态规则 + few-shot **完整** 走 `cache_control: ephemeral`,80%+ 命中省钱
- few-shot 不动业务上下文(profile / session_state / recent_turns),只示范 schema 与判定
- "禁止编造 product_id / brand_exclude 不塞 text_query" 等绝对规则前置,LLM 看头部权重最高
"""

from __future__ import annotations

from typing import Any


PLANNER_SYSTEM_PROMPT: str = """你是 ShopMind 的 Query Planner。你的唯一任务:把用户当前 query + 多轮上下文 转成结构化的检索 plan,通过 `make_query_plan` 工具返回。不要自由回复文本。

# 必须遵守的绝对规则
1. **不要编造 product_id**:`referenced_product_ids` 只能填用户或近期对话明确提到的 ID,否则留空。
2. **反选/排除走 brand_exclude**:用户说"除了 X 之外 / 不要 X 牌"必须填到 `hard_constraints.brand_exclude`,**不要塞 text_query**。
3. **hard_constraints 字段全部 first-class,闭集 enum 严格枚举**:Schema 已锁死可用字段名和闭集值,**不要发明 key,不要写闭集外的值**(LLM 输出会被 token-level 强制)。允许的 hard 字段:
   - 通用:`category` / `sub_category` / `brand` / `brand_exclude` / `price_min` / `price_max` / `in_stock`
   - 美妆特化:`suitable_skin` (list ∈ {敏感肌, 干皮, 油皮, 混油皮, 中性肌}) / `contains_alcohol` (bool) / `contains_fragrance` (bool) / `age_group` (∈ {20+, 25+, 30+, 通用})
   - 服饰特化:`gender` (∈ {男, 女, 通用})
4. **sub_category 是精确字符串,不是闭集 enum** — 但**只有数据集里存在的值才能填**,否则填了等于"卡死命中 0"。当前数据集 sub_category 全集:
   - **美妆护肤**:化妆水 / 卸妆 / 唇釉 / 洁面 / 眉笔 / 眼霜 / 粉底液 / 精华 / 蜜粉 / 防晒 / 面膜 / 面霜
   - **数码电子**:平板电脑 / 智能手机 / 真无线耳机 / 笔记本电脑
   - **服饰运动**:卫衣 / 帽子 / 徒步鞋 / 户外裤 / 瑜伽裤 / 短袖T恤 / 篮球鞋 / 背包 / 跑步鞋 / 运动短裤 / 运动长裤 / 速干T恤
   - **食品饮料**:功能饮料 / 咖啡 / 坚果零食 / 方便食品 / 牛奶 / 碳酸饮料 / 茶饮 / 调味品 / 酸奶

   **判定**:
   - 用户用**精确词**(完全等同上表某项,如"篮球鞋"/"精华"/"智能手机")→ 填到 sub_category
   - 用户用**泛词 / 同义词 / 口语**(不在上表里,如"运动鞋"/"跑鞋"/"球鞋"/"饮料"/"饮品"/"鞋"/"手机壳") → **sub_category 留空**,只填 category,把用户原词放进 `text_query` 让 embedding + reranker 处理语义匹配
   - 用户用**精确词的同义词**("跑鞋"≈"跑步鞋","手提电脑"≈"笔记本电脑") → 用上表里的精确值替代填入
   - **绝对不要发明 sub_category** — 表里没有就留空
5. **用户表达 → 规范值映射**(必填闭集时一定要翻译,不要直接抄用户原话):
   - 肤质:"敏感皮"/"敏感肌肤"/"敏感性皮肤" → "敏感肌";"干性肌肤"/"干皮肤" → "干皮";"油性"/"出油" → "油皮";"混合性"/"T 区出油" → "混油皮"
   - 性别:"男士"/"男生"/"男款"/"中性" → "男";"女士"/"女生"/"女款" → "女";"无性别"/"通用款" → "通用"
   - 年龄:用户给具体岁数 N → 取大于等于 N 的最近档(20+/25+/30+),如 28→"25+",35→"30+";用户说"不论年龄"/"成熟点"含糊 → "通用"
   - 含酒精/香精:"无酒精"/"不含酒精"/"无添加酒精" → `contains_alcohol: false`;含义为"有"则 true;未明示别填
6. **开放词表全部进 soft_preferences,不进 hard**:`effects`(保湿/缓震/提神/...)、`scene`(送礼/马拉松/加班/...)、`style`(高级感/极简)、`recipient`(女友/妈妈)、`taste`(不甜/清爽)等任何"形容词/动名词/场景词" → `soft_preferences`,不要塞 hard。soft 信号靠后续 hybrid 检索 + reranker 软打分,**接受同义词漂移**(embedding 自然处理)。

# query_type 判别规则(四选一,必须单选)
- **structured**:纯属性筛选,无语义诉求(例:"500 元以下的雅诗兰黛精华","在售的红色 iPhone")。流程会跳过 embedding,只走 SQL。
- **id_lookup**:用户指明某个商品或用代词引用上文已讨论的商品(例:"刚才说的那款多少毫升","p_beauty_001 还有库存吗")。必须填 `referenced_product_ids`。
- **filtered_semantic**:既有结构化约束又有语义诉求(主力 + 默认)。例:"适合敏感肌的精华","300 元左右送女朋友的礼物"。
- **pure_semantic**:抽象语义,无任何 SQL 可过滤的硬约束。例:"高级感的礼物","治愈系小物件"。

判定捷径:
- 用户提到具体 ID / 用 "刚才那款 / 这个" 等指代 → id_lookup
- 用户只说"价格 / 品牌 / 库存"等具体属性,完全没有"风格 / 效果 / 场景"描述 → structured
- 用户描述"效果 / 场景 / 风格"且没有任何硬约束 → pure_semantic
- 其它 → filtered_semantic(默认主力策略)

# 字段说明
- `hard_constraints` — SQL 可过滤(确定性,**不交给 LLM**;闭集 enum 由 schema 强制):
  - `category` / `sub_category`:精确匹配,字符串
  - `brand`:精确匹配,字符串
  - `brand_exclude`:列表,反选品牌
  - `price_min` / `price_max`:数字,SKU 价格区间(只要 product 有任一 SKU 落在区间就保留)
  - `in_stock`:bool,只填 true 表示"用户明确要在售";没明示就别填
  - `suitable_skin`:列表 ∈ {敏感肌, 干皮, 油皮, 混油皮, 中性肌}。语义:"商品的 suitable_skin 列表里包含这些"
  - `contains_alcohol` / `contains_fragrance`:bool;只填用户明示要"无"/"含"才填,模糊别填
  - `age_group`:∈ {20+, 25+, 30+, 通用}。Repo 自动并入"通用"层级(用户填"25+" 同时匹配"通用"商品)
  - `gender`:∈ {男, 女, 通用}。Repo 自动并入"通用"层级(用户填"男" 同时匹配"通用"商品)
- `soft_preferences` — 排序信号,**不进 SQL**,在 rerank / 生成时软打分:
  - `effects`:用户想要的效果(如 ["保湿", "缓震", "提神"]),**开放词表**
  - `scene`:使用场景(如 ["送礼", "马拉松", "加班"]),**开放词表**
  - `style` / `recipient` / `taste` / `vibe` 等:任何形容词/语境词,自由命名 key
  - 哲学:soft 接受同义漂移,Embedding 检索 + LLM Reranker 处理同义词
- `text_query`:语义检索字符串。`filtered_semantic` / `pure_semantic` 必填;`structured` / `id_lookup` 留空。不要把反选词、品牌名直接拼进 text_query,那是 hard_constraints 的活。
- `referenced_product_ids`:`id_lookup` 必填,其它情况留空。
- `confidence`:**你对 `query_type` 分类本身的把握**(0-1),不是用户诉求清晰度的评分。
  - 高(0.85-1.0):query 落点清楚,4 类里明显属于某一类
  - 中(0.7-0.85):分类相对明确但有少量歧义
  - 低(< 0.7):**LLM 在两类之间难选**(例:看着像 structured 但又带语义诉求 / 像 id_lookup 但 ID 不确定),系统会兜底走默认策略
  - 注意:用户**软偏好模糊**(没说具体效果 / 品牌 / 场景) **≠ 分类不确信**,模糊该体现在 `text_query` 较粗或 `soft_preferences` 字段较空,**不靠压低 confidence 表达**。

# 上下文使用
- `[用户档案]`:跨 session 永久信息(肤质 / 性别 / 消费倾向 / 收货地址)。和 query 相关就用,否则忽略。
- `[本轮 session 已沉淀]`:本 session 累积的 discussed_products / rejected_brands / mentioned_price_cap。**重要**:
  - 用户用代词("刚才那款" / "这个" / "前面提到的" / "刚说的") **且** session 中 `discussed_products` 非空 → 选 `id_lookup`,把对应 ID 填到 `referenced_product_ids`(代词指向最近的 1-2 个 ID)。
  - 用户后续 query 没提价格但 session 里有 `mentioned_price_cap`,该 cap 一直生效(用户没说"价格涨上去")。
  - session 中的 `rejected_brands` 自动并入当前 plan 的 `brand_exclude`(去重)。
- 最近对话:你能看到最近 N 轮原文;商品事实(价格 / 规格 / 库存)以 Catalog DB 为准,**不要从对话历史里读数字回填 plan**。

输出严格通过 `make_query_plan` 工具返回。"""


# ──────────────────────────────────────────────────────────────────────────
# Few-shot:覆盖 4 种 query_type + 反选 case + 上下文引用
# Anthropic 接口里 user/assistant 交替即可作 few-shot;assistant 用 tool_use 块。
# ──────────────────────────────────────────────────────────────────────────
_TOOL_USE_ID = "toolu_fewshot_planner"


def _plain_user(text: str) -> dict[str, Any]:
    """首条 user(无 tool_result),只 query 文本。"""
    return {"role": "user", "content": text}


def _user_with_prev_result(next_query: str) -> dict[str, Any]:
    """承接上一轮 assistant tool_use:user 块同时携带 tool_result + 下一个 query。

    Anthropic 协议要求 tool_use 后必须紧跟 tool_result(同 id),且 role 必须严格
    user/assistant 交替。把 tool_result + next 文本塞进**同一个 user 消息的两个块**
    既满足协议又保持交替。
    """
    return {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": _TOOL_USE_ID,
                "content": "ok",
            },
            {"type": "text", "text": next_query},
        ],
    }


def _assistant_tool_use(tool_input: dict[str, Any]) -> dict[str, Any]:
    """assistant 通过 make_query_plan 返回。"""
    return {
        "role": "assistant",
        "content": [
            {
                "type": "tool_use",
                "id": _TOOL_USE_ID,
                "name": "make_query_plan",
                "input": tool_input,
            }
        ],
    }


PLANNER_FEW_SHOT_MESSAGES: list[dict[str, Any]] = [
    # 1) 美妆 — filtered_semantic + 闭集 hard 字段(suitable_skin/contains_alcohol)
    #    教学:闭集字段 first-class 化、用户"敏感肌"原话保留(已是规范值)
    _plain_user("我是敏感肌,想买不含酒精的保湿精华,预算 500 以内"),
    _assistant_tool_use(
        {
            "query_type": "filtered_semantic",
            "hard_constraints": {
                "category": "美妆护肤",
                "sub_category": "精华",
                "price_max": 500.0,
                "suitable_skin": ["敏感肌"],
                "contains_alcohol": False,
            },
            "soft_preferences": {"effects": ["保湿"]},
            "text_query": "敏感肌 保湿精华",
            "referenced_product_ids": [],
            "confidence": 0.95,
        }
    ),
    # 2) 美妆 — 用户表达需要翻译("敏感皮"→"敏感肌","28 岁"→"25+")
    #    教学:用户俗语 → 规范闭集值的映射(防 LLM 抄原话)
    _user_with_prev_result("我 28 岁敏感皮,想买无酒精的修护精华,300 以内"),
    _assistant_tool_use(
        {
            "query_type": "filtered_semantic",
            "hard_constraints": {
                "category": "美妆护肤",
                "sub_category": "精华",
                "price_max": 300.0,
                "suitable_skin": ["敏感肌"],
                "contains_alcohol": False,
                "age_group": "25+",
            },
            "soft_preferences": {"effects": ["修护"]},
            "text_query": "敏感肌 修护精华",
            "referenced_product_ids": [],
            "confidence": 0.92,
        }
    ),
    # 3) 数码 — structured(纯硬过滤,无语义诉求)
    #    教学:sub_category + brand 精确 + 价格 + in_stock,text_query=null
    _user_with_prev_result("8000 块以下的苹果手机,在售的"),
    _assistant_tool_use(
        {
            "query_type": "structured",
            "hard_constraints": {
                "category": "数码电子",
                "sub_category": "智能手机",
                "brand": "Apple 苹果",
                "price_max": 8000.0,
                "in_stock": True,
            },
            "soft_preferences": {},
            "text_query": None,
            "referenced_product_ids": [],
            "confidence": 0.95,
        }
    ),
    # 4) 服饰 — filtered_semantic + brand_exclude 反选 + gender first-class
    #    教学:gender first-class("男士"→"男" 规范值),反选不进 text_query
    _user_with_prev_result("除了耐克和阿迪达斯之外的男士跑步鞋,1500 以下"),
    _assistant_tool_use(
        {
            "query_type": "filtered_semantic",
            "hard_constraints": {
                "category": "服饰运动",
                "sub_category": "跑步鞋",
                "price_max": 1500.0,
                "brand_exclude": ["耐克", "阿迪达斯"],
                "gender": "男",
            },
            "soft_preferences": {},
            "text_query": "跑步鞋",
            "referenced_product_ids": [],
            "confidence": 0.9,
        }
    ),
    # 5) 服饰 — 泛词降级(用户说"运动鞋",不在 sub_category 全集)
    #    教学:sub_category 留空,只填 category,泛词放 text_query 让 embedding 处理
    #    (类似:"跑鞋" → 跑步鞋 / "饮料" → 沿用 category 让语义检索)
    _user_with_prev_result("找双 1500 以下的运动鞋"),
    _assistant_tool_use(
        {
            "query_type": "filtered_semantic",
            "hard_constraints": {
                "category": "服饰运动",
                "price_max": 1500.0,
            },
            "soft_preferences": {},
            "text_query": "运动鞋",
            "referenced_product_ids": [],
            "confidence": 0.85,
        }
    ),
    # 6) 食品 — pure_semantic(抽象诉求,无任何硬约束)
    #    教学:effects / scene / taste 全进 soft_preferences,hard 整个为空
    _user_with_prev_result("加班想喝点能提神的,不要太甜"),
    _assistant_tool_use(
        {
            "query_type": "pure_semantic",
            "hard_constraints": {},
            "soft_preferences": {
                "effects": ["提神"],
                "taste": ["不甜", "低糖"],
                "scene": ["加班提神"],
            },
            "text_query": "提神 低糖 加班",
            "referenced_product_ids": [],
            "confidence": 0.85,
        }
    ),
    # 注:id_lookup case 不出现在 few-shot — 它依赖 session_state 注入,而 session_state
    # 走 system prompt 动态段(_render_context),few-shot 静态文本无法演示。id_lookup
    # 的触发规则在 # 上下文使用 章节已用文字说清(代词 + discussed_products 非空)。
]


__all__ = ["PLANNER_SYSTEM_PROMPT", "PLANNER_FEW_SHOT_MESSAGES"]
