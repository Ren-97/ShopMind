"""兜底文案集中模板(§4.6.9)。

四层防御:
  1. Tool 异常 → 包成 `{"error":...}` 回灌 Claude(在 tools/base.py 处理)
  2. Agent loop MAX_TURNS → agent_max_turns 文案
  3. Catastrophic handler(最外层 try)→ catastrophic 文案
  4. SSE error event → 客户端 Snackbar
"""

from __future__ import annotations

FALLBACK_MESSAGES: dict[str, str] = {
    "no_match": "抱歉,没找到完全符合您要求的商品。可以换个描述试试,或放宽一些约束?",
    "planner_failure": "系统暂时繁忙,请稍后再试。",
    "agent_max_turns": "这个问题我处理出了点状况,稍后再试或换个问法。",
    "catastrophic": "系统暂时繁忙,请稍后再试。",
    "tool_unavailable": "{tool_name} 暂时不可用,我换个方式帮你。",
    "out_of_stock": "{product_title} 暂时缺货,要不要看看类似的?",
    "off_shelf": "{product_title} 已下架,这里有几款相似的可以参考。",
    "address_missing": "下单需要地址,可以告诉我寄到哪里吗?",
}


__all__ = ["FALLBACK_MESSAGES"]
