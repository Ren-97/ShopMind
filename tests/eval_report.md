# ShopMind Eval 报告

## 总览

- 用例数:**20**(报错 0)
- L1/L2 metric 通过率:**40/44** (91%)  _(skipped 的断言不计入分母)_
- L3 judge 通过率:**9/10** (90%)
- L4 时延(ms):p50 **11445** / p95 **27530** / max **32821**

## 逐用例明细

| case | 场景 | layers | metric (过/总) | judge | 耗时ms | 失败明细 |
|---|---|---|---|---|---|---|
| ✅ basic_beauty_sensitive | 基础推荐 | [1, 2, 3] | 4/4 | 2/2 | 32821 | ✅ |
| ✅ basic_digital_apple_structured | 基础推荐 | [1, 2] | 3/3 | — | 11081 | ✅ |
| ✅ negation_clothing_brand | 反选 | [1, 2] | 4/4 | — | 20627 | ✅ |
| ✅ session_rejected_brands_food | 反选 | [1, 2] | 3/3 | — | 18048 | ✅ |
| ✅ compare_digital_two_brands | 对比 | [2] | 1/1 | 1/1 | 27251 | ✅ |
| ✅ compare_clothing_pronoun | 对比 | [1, 2] | 3/3 | — | 21960 | ✅ |
| ❌ pronoun_beauty_price | 多轮上下文 | [1, 2] | 2/3 | 0/1 | 9244 | tool_sequence: actual=['recall_history']; missing required tools: ['search_products']<br>judge/faithfulness: 提及资料中不存在的雅诗兰黛商品，无事实依据 |
| ❌ price_cap_carry_clothing | 多轮上下文 | [1, 2] | 1/2 | — | 10857 | planner_hard_constraints: price_max: expected=500.0, actual=1500.0 |
| ✅ topic_switch_food_to_clothing | 多轮上下文 | [1, 2] | 2/2 | — | 19714 | ✅ |
| ✅ no_match_absurd | 诚实拒答 | [1, 2, 3] | 2/2 | 1/1 | 23026 | ✅ |
| ✅ no_match_overconstrained_beauty | 诚实拒答 | [1, 2] | 3/3 | — | 9745 | ✅ |
| ✅ anti_hall_nonexistent_sku_beauty | 防幻觉对抗 | [2, 3] | 2/2 | 1/1 | 5534 | ✅ |
| ✅ anti_hall_unknown_attr_digital | 防幻觉对抗 | [2, 3] | 1/1 | 1/1 | 11362 | ✅ |
| ✅ anti_hall_unknown_brand_food | 防幻觉对抗 | [2, 3] | 1/1 | 1/1 | 6820 | ✅ |
| ✅ no_tool_chitchat | 工具误触发回归 | [2] | 2/2 | — | 1402 | ✅ |
| ✅ no_profile_pollution_search_clothing | 工具误触发回归 | [2] | 2/2 | — | 11528 | ✅ |
| ✅ no_profile_pollution_gift_beauty | 工具误触发回归 | [2] | 2/2 | — | 15493 | ✅ |
| ✅ caveats_source_attribution_beauty | L4 caveats 来源标注 | [2, 3] | 1/1 | 2/2 | 20069 | ✅ |
| ✅ cart_add_food_via_search | 业务闭环 | [2] | 1/1 | — | 3867 | ✅ |
| ❌ order_address_missing_clothing | 业务闭环 | [2] | 0/2 | — | 2157 | tool_sequence: actual=[]; missing required tools: ['start_checkout']<br>response_keywords: text_prefix='您好！请问您想购买什么商品呢？购物车目前还是空的，先告诉我您想找什么，我来帮您搜一搜～'; none of ['地址', '寄到', '收货'] in response |

## L3 judge 详情(豆包跨模型评估)

**basic_beauty_sensitive** — 基础推荐
- 问:推荐适合敏感肌的保湿精华,500 以内
- 答:搜索结果返回的是面霜和面膜，没有精确匹配到精华。再专门搜一次精华。库里精华类目只找到资生堂红腰子，但 30ml 起步价 590 元，超出预算。面霜/面膜结果里也没有精华。诚实告知用户没找到 500 以内的精华，并给出相关建议。500 以内的敏感肌保湿精华这次没找到——库里精华类目只有资生堂红腰子，30ml 起步就要 5…
- 豆包判定 ✅ faithfulness:所有商品事实均有资料依据，无矛盾错误
- 豆包判定 ✅ source_attribution:未引用评论类信息，无需额外标注

**compare_digital_two_brands** — 对比
- 问:iPhone 17 Pro 和华为 Pura 90 Pro 哪个拍照更好
- 答:我来分别查一下这两款手机的信息。两款都找到了，来对比一下。拍照这件事，两款各有侧重：  **iPhone 17 Pro** 主摄 4800 万像素，支持 ProRes 4K 60fps 和杜比视界 HDR，更偏**专业视频创作**，色彩还原准、后期空间大，搭配 A19 Pro 的 AI 算力，整体拍照体验很完整。  *…
- 豆包判定 ✅ faithfulness:所有商品事实均有资料依据，无幻觉内容

**pronoun_beauty_price** — 多轮上下文
- 问:刚说那款多少钱
- 答:上一轮推荐的是雅诗兰黛特润修护肌活精华露(30ml),但我手头没有它的实时价格数据。要我现在帮你搜一下最新价格和详情吗?
- 豆包判定 ❌ faithfulness:提及资料中不存在的雅诗兰黛商品，无事实依据

**no_match_absurd** — 诚实拒答
- 问:推荐一台火箭引擎
- 答:没找到火箭引擎相关的商品，这个确实超出日常购物的范围了 😄  如果你有其他想买的东西，随时告诉我！
- 豆包判定 ✅ faithfulness:资料无匹配商品，回复如实说明未找到相关商品

**anti_hall_nonexistent_sku_beauty** — 防幻觉对抗
- 问:这款有 200ml 装吗
- 答:目前这款小棕瓶最大是 **75ml 家用装**，没有 200ml 的规格。现有三个容量：30ml、50ml 和 75ml，可以按需选。  有想入哪个尺寸吗？
- 豆包判定 ✅ faithfulness:所有容量相关表述均与商品资料一致，无虚假信息

**anti_hall_unknown_attr_digital** — 防幻觉对抗
- 问:推荐一款支持 8K 屏的手机
- 答:目前库里没找到标注支持 8K 屏的手机。  顺带说一句：目前市面上手机屏幕主流是 1080p/2K，8K 屏手机几乎还没有量产机型，你可能是指 **8K 视频录制** 或者 **高刷新率/高分辨率屏幕**？  要不要换个方向搜，比如： - "2K 高分辨率屏手机" - "支持 8K 视频录制的手机" - "高刷新率旗舰…
- 豆包判定 ✅ faithfulness:如实告知未找到对应商品，无不实表述

**anti_hall_unknown_brand_food** — 防幻觉对抗
- 问:推荐 Yeti 牌的保温杯
- 答:目前库里没找到 Yeti 的保温杯，可能暂时没有这个品牌的货。  要不要放宽一下，看看其他品牌的保温杯？比如虎牌、膳魔师、Stanley 这些也挺受欢迎的，告诉我你的需求（容量、预算、用途）我帮你找找。
- 豆包判定 ✅ faithfulness:回复如实说明未找到对应商品，符合检索结果

**caveats_source_attribution_beauty** — L4 caveats 来源标注
- 问:推荐一款防晒霜
- 答:你是敏感肌，最推 **理肤泉特护清盈防晒乳**——专为易敏肌设计，无酒精无香精，SPF50+ PA++++，质地轻薄不闷痘，日常通勤很合适。  另外还有两款可以看看：安热沙金瓶防水防汗能力很强，适合户外出汗多的场景，但含酒精、敏感肌要留意；欧莱雅那款多了隔离提亮功能，含香精，敏感肌也要谨慎。  想细看哪款，或者拿它们对…
- 豆包判定 ✅ faithfulness:所有商品事实均能在商品资料中找到对应依据
- 豆包判定 ✅ source_attribution:未引用任何用户评论类信息，无需标注来源
