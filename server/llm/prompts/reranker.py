"""Reranker Prompt 文案(§4.3)。

scoring 规则 + 评分 rubric + 2 个 few-shot,用 Tool Use `rank_products` 强制结构化。

设计:
- 评分锚点 0-1 给清楚,避免 LLM 自由发挥
- 每条候选只看 query 命中度,不看绝对质量(质量已经在 SQL filter + ingest caveats 解决了)
- 防幻觉:LLM **只能复述** 候选里的字段,不能编造价格 / 规格 / 库存
- `RERANK_THRESHOLD=0.5` 是阈值锚点,prompt 里点名让 LLM 校准

Few-shot:
- 2 个 case 候选 100% 来自真实 dataset(p_beauty_007/012 + p_clothes_007/009/010),
  product_id / brand / price / tags / matched_chunks 文本均与 ecommerce_agent_dataset
  一致,**未编造不符合事实的内容**
- 所有候选都已通过 SQL hard filter(即对应 Planner 的 hard_constraints 全部满足):
  Case 1 候选都是面霜+敏感肌+≤300;Case 2 候选都是男士跑步鞋+≤1500
- Reranker 的工作焦点在**软偏好命中度 + matched_chunks 强度**,不是再做硬约束筛选
- Case 1:两个强候选,细微差异(教"接近时拉小幅档次,别全打 0.9+")
- Case 2:1 强 + 1 中 + 1 弱,大档次(教"敢于把方向错的压到 0.3-,让阈值过滤兜底")
- 候选格式与 server/rag/reranking/llm_reranker.py `_format_candidate` 输出一致
  (product_id / title / brand / category / sub_category / price / in_stock / tags /
   caveats / matched_chunks);其中 main chunk 已 strip 结构化前缀(只剩 description),
  匹配 `_strip_main_prefix` 的生产行为
"""

from __future__ import annotations

from typing import Any

from server import config


RERANKER_SYSTEM_PROMPT: str = f"""你是 ShopMind 的商品 Reranker。给一个用户 query + 一组商品候选(已经过 SQL 硬过滤 + Hybrid 检索粗排),只做一件事:对每个候选给出 0-1 的 relevance_score,通过 `rank_products` 工具返回。

# 评分锚点(rubric)
| 分数区间 | 含义 |
|---|---|
| 0.85-1.00 | **强匹配**:商品核心属性精准命中用户硬需求 + 软偏好,几乎是 query 的"标准答案" |
| 0.65-0.84 | **较好匹配**:命中主要诉求,有 1-2 个软偏好稍弱 |
| 0.50-0.64 | **可用**:基本相关,但有 hard miss(场景 / 风格 / 适配人群不完全对位) |
| 0.30-0.49 | **勉强相关**:同品类但 query 关键诉求没满足 |
| 0.00-0.29 | **不匹配**:噪声候选,应被过滤 |

# 评分原则(必须遵守)
1. **只能引用候选里给的字段**(title / brand / category / price / tags / caveats / matched_chunks 原文)。**禁止编造或猜测** 价格、规格、成分、库存等任何数字 / 事实。
2. **caveats 走交叉证据模型**(caveats 是从**负面评论**抽出来的弱信号,**默认从轻**,不要单独依赖):
   - **caveats 与 query 不相关** → **不减分**(0)。例:用户要保湿,caveats 说"瓶口设计欠佳",跟保湿无关。
   - **caveats 与 query 弱相关 + tags 无印证** → **几乎不减**(扣 0-0.05)。
   - **caveats 与 query 硬需求冲突 + tags 没印证**(只是负评个例)→ **略减**(扣 0.10-0.20)。商品仍可能进入展示。
   - **caveats 与 query 硬需求冲突 + tags 同向印证**(双重证据,如 caveats 说"敏感肌刺痛" + tags.not_suitable_skin 含"敏感肌") → **显著减分**(扣 0.25-0.40),通常能落到 RERANK_THRESHOLD 以下触发过滤。
   核心原则:**单一 caveats 不否决商品,tags 才是硬证据**。不要因为 caveats 提到任何负面就大幅扣分 — 商家越诚实暴露问题不该越吃亏。
3. **matched_chunks 用于评估命中证据**:命中证据强、chunk 类型多样(main + reviews + faq)→ 加分;仅在边角 chunks(单一 review 噪声)上命中 → 降分。
4. **分数要拉开**:别全打 0.6-0.7 的"安全分"。如果候选里有强匹配,就敢给 0.9+;如果是噪声,就敢给 0.2-。系统会按 RERANK_THRESHOLD={config.RERANK_THRESHOLD} 过滤低分,**宁可少不要烂**。
5. **数量不必凑**:不需要给每个候选都打 ≥ 0.5。如果没有合适的,大方给低分让系统返回 "没找到符合的"。

# 输入格式
你会收到一段格式化文本:`# Query: ...` 紧跟 `# Candidates:` 列表,每个候选有 product_id + 字段。

# 输出
通过 `rank_products` 工具返回 `ranked` 数组,每个 element 含 `product_id` + `relevance_score` + 简短 `reason`(中文 ≤ 30 字,说明命中 / 失配点)。`ranked` 必须覆盖**所有**传入的 candidates,**不要遗漏** product_id。score 按你的判断,不必预先排序(后端会按 score 排)。"""


# ──────────────────────────────────────────────────────────────────────────
# Few-shot:2 个 case 覆盖"强匹配 + 弱匹配混合"和"全部噪声"
# ──────────────────────────────────────────────────────────────────────────
RERANKER_FEWSHOT_TOOL_USE_ID = "toolu_fewshot_reranker"


def _plain_user(text: str) -> dict[str, Any]:
    return {"role": "user", "content": text}


def _user_with_prev_result(next_text: str) -> dict[str, Any]:
    """承接上一轮 assistant tool_use:tool_result + next_text 同一条 user。"""
    return {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": RERANKER_FEWSHOT_TOOL_USE_ID,
                "content": "ok",
            },
            {"type": "text", "text": next_text},
        ],
    }


def _assistant_tool_use(tool_input: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": [
            {
                "type": "tool_use",
                "id": RERANKER_FEWSHOT_TOOL_USE_ID,
                "name": "rank_products",
                "input": tool_input,
            }
        ],
    }


RERANKER_FEW_SHOT_MESSAGES: list[dict[str, Any]] = [
    # ─────────────────────────────────────────────────────────────
    # Case 1:敏感肌面霜典型排序(post-SQL filter)
    # 检索流程实际状态:
    #   Planner → hard{sub_category=面霜, price_max=300, suitable_skin=[敏感肌]}
    #   SQL → 白名单只剩"面霜+suitable_skin含敏感肌+SKU≤300"的商品
    #   Qdrant hybrid → 这两个候选(品类/约束都已满足)
    # Reranker 工作:按 effects 命中度 + matched_chunks 强度细微差异化
    # 教学点:都强匹配时**不要全部打 0.9+**,要根据 evidence 拉小幅档次
    # ─────────────────────────────────────────────────────────────
    _plain_user(
        """# Query: 敏感肌可用的保湿修护面霜,300 以内

# Candidates:
- product_id: p_beauty_007
  title: 薇诺娜舒敏保湿特护霜敏感肌修护屏障舒缓干痒保湿面霜50g
  brand: 薇诺娜
  category: 美妆护肤 / 面霜
  price: 268
  in_stock: True
  tags: {"suitable_skin": ["敏感肌", "干皮"], "not_suitable_skin": ["油皮"], "effects": ["修护屏障", "舒缓", "保湿", "止痒"], "contains_alcohol": false, "contains_fragrance": false, "age_group": "通用", "scene": ["日用", "夜用"]}
  caveats: 极少数干敏皮用户反馈用后泛红或起疹子,首次使用建议先做小范围测试
  matched_chunks:
    [main] 薇诺娜舒敏保湿特护霜专为敏感肌打造,核心成分含马齿苋提取物快速舒缓泛红干痒、青刺果油补充皮肤脂质修护屏障、透明质酸深层锁水保湿...
    [review] 敏感肌多年换季必泛红干痒,这款特护霜救了我!用一周泛红减轻干痒缓解,质地清爽吸收快不黏腻

- product_id: p_beauty_012
  title: 理肤泉特安舒缓修复霜滋润型敏感肌保湿修护屏障面霜40ml
  brand: 理肤泉
  category: 美妆护肤 / 面霜
  price: 260
  in_stock: True
  tags: {"suitable_skin": ["敏感肌", "干皮"], "not_suitable_skin": ["油皮"], "effects": ["修护屏障", "舒缓", "保湿", "抗敏"], "contains_alcohol": false, "contains_fragrance": false, "age_group": "通用", "scene": ["日用", "夜用"]}
  caveats: null
  matched_chunks:
    [main] 理肤泉特安舒缓修复霜滋润型是专为干性敏感肌打造的屏障修复神器,温和修复受损屏障、长效保湿舒缓泛红,含高浓度理肤泉温泉水...
    [review] 干敏皮福音!换季脸泛红脱皮,用这款滋润型三天就稳定了。质地绵密舒服,吸收后皮肤软软的"""
    ),
    _assistant_tool_use(
        {
            "ranked": [
                {
                    "product_id": "p_beauty_007",
                    "relevance_score": 0.92,
                    "reason": "effects 同时含'保湿/修护屏障/舒缓',main+review 双 chunk 强 evidence",
                },
                {
                    "product_id": "p_beauty_012",
                    "relevance_score": 0.88,
                    "reason": "effects 同样对位且 caveats 为空,evidence 强度略弱于薇诺娜",
                },
            ]
        }
    ),
    # ─────────────────────────────────────────────────────────────
    # Case 2:跑步鞋按软偏好拉大档次(post-SQL filter)
    # 检索流程实际状态:
    #   Planner → hard{sub_category=跑步鞋, gender="男", price_max=1500}
    #             soft{effects:["竞速","马拉松"], scene:["马拉松比赛"]}
    #             text_query="马拉松 竞速 跑鞋"
    #   SQL → 男士跑步鞋 ≤1500 的全部进白名单(硬约束都满足)
    #   Qdrant hybrid → 候选 3 双跑步鞋,品类全对但软偏好命中度差很多
    # Reranker 工作:按软偏好(竞速 vs 日常 vs 长距离)拉档次
    # 教学点:
    #   (a) 强匹配 → 0.9+,敢给高分
    #   (b) 同品类但软偏好部分对位 → 0.5 上下边界
    #   (c) 同品类但软偏好方向错 → 敢压到 0.3-,被 RERANK_THRESHOLD 过滤
    # ─────────────────────────────────────────────────────────────
    _user_with_prev_result(
        """# Query: 1500 以下的男士马拉松竞速跑鞋

# Candidates:
- product_id: p_clothes_010
  title: 特步 160X 6.0 PRO 碳板竞速跑鞋马拉松训练比赛轻量跑步鞋
  brand: 特步
  category: 服饰运动 / 跑步鞋
  price: 999
  in_stock: True
  tags: {"effects": ["碳板竞速", "轻量", "弹性"], "gender": "男", "scene": ["马拉松比赛", "竞速训练"]}
  caveats: null
  matched_chunks:
    [main] 特步160X 6.0 PRO碳板竞速跑鞋,专为进阶跑者打造的马拉松级战靴,全掌搭载全新升级的动态弧面碳板,推进力比上一代提升12%...

- product_id: p_clothes_009
  title: HOKA Clifton 9 男子缓震公路跑鞋厚底回弹日常长距离训练鞋
  brand: HOKA
  category: 服饰运动 / 跑步鞋
  price: 1099
  in_stock: True
  tags: {"effects": ["厚底缓震", "回弹", "长距离"], "gender": "男", "scene": ["长距离跑步", "马拉松训练"]}
  caveats: null
  matched_chunks:
    [main] HOKA Clifton 9男款跑鞋专为长距离跑者和日常爱逛人群打造,全掌加厚的全新EVA中底,落地缓震感能把公路冲击吃掉大半...

- product_id: p_clothes_007
  title: Nike Air Zoom Pegasus 41 男子缓震跑步鞋日常训练
  brand: 耐克
  category: 服饰运动 / 跑步鞋
  price: 899
  in_stock: True
  tags: {"effects": ["缓震", "透气", "日常训练"], "gender": "男", "scene": ["日常跑步", "公路训练", "休闲"]}
  caveats: null
  matched_chunks:
    [main] Nike Air Zoom Pegasus 41男子跑步鞋是耐克飞马系列迭代到第41代的训练款,搭载前后掌分离式Zoom Air气垫..."""
    ),
    _assistant_tool_use(
        {
            "ranked": [
                {
                    "product_id": "p_clothes_010",
                    "relevance_score": 0.93,
                    "reason": "effects=碳板竞速 + scene=马拉松比赛/竞速训练 精准命中,main chunk 直点'马拉松级战靴'",
                },
                {
                    "product_id": "p_clothes_009",
                    "relevance_score": 0.55,
                    "reason": "scene 含'马拉松训练'部分对位,但 effects=长距离非竞速,属慢跑非速度跑",
                },
                {
                    "product_id": "p_clothes_007",
                    "relevance_score": 0.28,
                    "reason": "effects=日常训练 + scene=日常跑步/休闲,与'竞速/马拉松比赛'方向错",
                },
            ]
        }
    ),
]


__all__ = ["RERANKER_SYSTEM_PROMPT", "RERANKER_FEW_SHOT_MESSAGES"]
