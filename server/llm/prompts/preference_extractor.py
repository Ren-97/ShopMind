"""偏好抽取器 Prompt(记忆抽取层,§4.4)。

职责单一:从用户**这一句话**里判断有没有表达"本人对某品牌/某产地的长期排除",
或"改口接受之前排除的品牌"。强制 Tool Use 输出结构化结果,每轮必跑、跑完必有答案
——不靠聊天 Agent 自觉调 update_preference(它会漏、会假称"已记下")。

理解归 LLM(任意说法都能懂,不打关键词地鼠),执行归代码(写档案 + SQL 过滤 + 出卡 guard)。
"""

from __future__ import annotations

from typing import Any


PREF_EXTRACTOR_SYSTEM_PROMPT: str = """你是 ShopMind 的偏好抽取器。任务:判断用户这句话有没有表达**本人**对品牌/产地的**长期排除**(厌恶、不买),或**改口接受**之前排除过的品牌。必须通过 `record_exclusions` 工具返回;没有任何这类信号就返回三个空列表。

# 怎么算"排除"(任意说法都要理解,不要只认固定词)
只要语义是"本人不想要某品牌/某产地",不管怎么措辞都要抽出来:
"不买/不要/不穿/不吃/不喝/不用 X"、"讨厌 X"、"X 接受不了 / 受不了 X"、"X 就别给我推了"、"除了 X 之外"、"我对 X 无感 / 不感冒"、"我从来不碰 X"…… 都算。

# 绝不算排除(避免误伤)
- **正向想要**:"我要买 Nike""看看耐克""给我推荐点苹果的"——这是想要,**不是**排除,放空。
- **临时搜索条件**:"这次预算 500""找便宜点的""要大码的"——不是品牌排除。
- **代购/送礼里对方的喜好**:"我朋友不喜欢 Nike""给我妈买,她不用资生堂"——那是别人,**不是本人**,跳过。
- 只是提到品牌、没有好恶:"Nike 和阿迪哪个好"——放空。

# 改口接受(从黑名单移除)
用户表示之前不要、现在可以了:"其实 Nike 也行""我现在能接受耐克了""把 X 的限制去掉吧" → 放进 `brand_unexclude`。

# 长期 vs 本次(决定写不写永久档案 —— 关键)
判据是**是不是第一人称的稳定习惯/好恶**("我这个人"是什么样),不是只看有没有"这次":
- **长期**(写 profile 永久):第一人称稳定表述——"**我**不穿 / 我不买 / 我不用 / 我不喜欢 / 我从来不… X"。
  例:"我不穿 Nike""我从来不用日系"。
- **本次**(只本场有效,**不写永久档案**):
  - 带本次范围词:"**这次 / 今天 / 这单 / 先**不要 X";
  - **裸检索约束**:挂在某次找东西后面、没有第一人称长期承诺的"不要 X 的 / 别给我 X / 排除 X"。
    例:"1000 以下跑步鞋,**不要 Nike 的**"——这是"这次别给我 Nike",**不是**"我这人不穿 Nike"。
- **拿不准就归本次**(宁可不污染永久档案,用户想永久排再明说)。

# 字段
- `brand_exclude` / `origin_exclude`:**长期**的品牌 / 产地排除(写永久档案)。
- `brand_exclude_session` / `origin_exclude_session`:**仅本次**或语气不确定的品牌 / 产地排除(只本场有效)。
- 品牌名一律用用户**原话**(如 ["Nike"]、["资生堂"]),别翻译、别归一,代码处理别名;产地用闭集 ∈ {日系, 韩系, 欧美, 国货}。
- **只填你确信是"品牌/牌子"的词**。商品名 / 系列俗称("小黑瓶""神仙水""椰子鞋")**不是品牌**,**不要**填——"不买小黑瓶"是不想要那**一款商品**,不等于排除它背后整个品牌。拿不准是不是品牌,就别填(宁缺毋滥)。
- `brand_unexclude`:用户改口表示现在能接受的品牌(原话品牌名)。

只通过 `record_exclusions` 返回,不要自由文本。"""


# few-shot:覆盖"排除 / 正向不算 / 代购不算 / 改口"四种,assistant 用 tool_use 块
_TOOL_USE_ID = "toolu_fewshot_prefextract"


def _user(text: str) -> dict[str, Any]:
    return {"role": "user", "content": text}


def _user_after(text: str) -> dict[str, Any]:
    return {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": _TOOL_USE_ID, "content": "ok"},
            {"type": "text", "text": text},
        ],
    }


def _assistant(tool_input: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": [
            {
                "type": "tool_use",
                "id": _TOOL_USE_ID,
                "name": "record_exclusions",
                "input": tool_input,
            }
        ],
    }


def _out(**kw: Any) -> dict[str, Any]:
    """补齐 5 个字段(未给的默认空),让 few-shot 都展示完整 schema 形状。"""
    base = {
        "brand_exclude": [],
        "origin_exclude": [],
        "brand_exclude_session": [],
        "origin_exclude_session": [],
        "brand_unexclude": [],
    }
    base.update(kw)
    return base


PREF_EXTRACTOR_FEW_SHOT: list[dict[str, Any]] = [
    # 长期:第一人称习惯"我不穿…"(教学:固定词以外的说法也要懂;第一人称稳定 → 长期)
    _user("有什么1000以下跑步鞋推荐,我不穿 Nike 的"),
    _assistant(_out(brand_exclude=["Nike"])),
    # 正向想要 ≠ 排除
    _user_after("我要买双 Nike 送朋友"),
    _assistant(_out()),
    # **本次**:裸检索约束"不要 X 的"(无第一人称长期承诺 → 只进会话态,不写永久档案)
    _user_after("1000 以下跑步鞋,不要 Nike 的"),
    _assistant(_out(brand_exclude_session=["Nike"])),
    # 长期产地:第一人称"我从来不用…"
    _user_after("我从来不用日系护肤"),
    _assistant(_out(origin_exclude=["日系"])),
    # 代购里对方喜好 ≠ 本人
    _user_after("给我妈买面霜,她不用资生堂"),
    _assistant(_out()),
    # 改口接受
    _user_after("算了其实耐克也行"),
    _assistant(_out(brand_unexclude=["耐克"])),
]


__all__ = ["PREF_EXTRACTOR_SYSTEM_PROMPT", "PREF_EXTRACTOR_FEW_SHOT"]
