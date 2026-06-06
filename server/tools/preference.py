"""update_preference tool(§4.6.1 + §4.6.3)。

LLM-driven memory(类 ChatGPT Memory):Agent 看对话识别稳定事实自主调用。
白名单字段(防 LLM 乱写):first-class column + preferences.<key>。
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import update as sa_update

from server.indexing.brand_aliases import normalize_brand
from server.storage.models import UserProfile
from server.storage.user_repo import UserRepo
from server.tools.base import AgentDeps, Tool, ToolError, ToolResult

log = structlog.get_logger("shopmind.tools.preference")


# 白名单(§4.6.3)
# 注:身份基础属性(gender / age / height_cm / weight_kg)+ 收货三件套
# (address / recipient_name / phone)**不在**对话写入白名单。
# 身份属性易被"找女士运动服"/"42 码鞋"等代购/搜索 query 误触发污染身份档;
# 收货信息则不该被下单对话静默改默认地址 —— 两类都强制走 PATCH /profile
# (Android 个人资料页 / 结算页),Agent 看到也不写。
_COLUMN_FIELDS: dict[str, type] = {
    "consumption_tier": str,
}

# preferences JSON 里允许的 key(可继续扩展)
_PREFERENCE_KEYS: set[str] = {
    "skin_type",
    "skin_concerns",
    "fragrance_pref",
    "brand_prefer",
    "brand_exclude",
    "usage",
    "os_pref",
    "clothing_size",
    "shoe_size",
    "style_pref",
    "dietary_restrictions",
}

PreferenceField = Literal[
    "consumption_tier",
    "skin_type", "skin_concerns", "fragrance_pref",
    "brand_prefer", "brand_exclude",
    "usage", "os_pref",
    "clothing_size", "shoe_size", "style_pref",
    "dietary_restrictions",
]


class UpdatePreferenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: PreferenceField = Field(
        description=(
            "要更新的字段名,必须在白名单内。"
            "consumption_tier 写到 profile 列;其它字段写到 preferences JSON。"
            "**注意**:身份基础属性(gender / age / height_cm / weight_kg)和收货信息"
            "(address / recipient_name / phone)不在白名单内 —— "
            "由用户在个人资料页表单 / 结算页填写,不通过对话写入。"
        )
    )
    value: Any = Field(
        description=(
            "字段值。类型必须匹配:"
            "brand_prefer / brand_exclude / skin_concerns / usage / style_pref / dietary_restrictions = list[str];"
            "其余 = str。"
            "**特殊值**:传 null 表示清除该字段(撤销之前的错填,回到从未填过的状态)。"
        )
    )


def _coerce_value(field: str, value: Any) -> Any:
    """简单类型校验 + 转换。失败抛 ToolError。

    value=None 是合法的"清除"信号(撤销错填):
      - 首类字段 → SQL SET NULL
      - preferences key → 从 dict 里 pop
    短路返回,不进类型校验。
    """
    if value is None:
        return None
    if field in _COLUMN_FIELDS:
        expected = _COLUMN_FIELDS[field]
        try:
            return expected(value)
        except (TypeError, ValueError) as e:
            raise ToolError(f"{field} 类型必须为 {expected.__name__}: {e}") from e

    # preferences.<key>:list / str 都接受
    if field in {
        "brand_prefer", "brand_exclude", "skin_concerns",
        "usage", "style_pref", "dietary_restrictions",
    }:
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ToolError(f"{field} 必须为字符串数组")
        # 品牌字段归一化(跟 ingest / catalog_repo 用同一 alias map),保序去重
        if field in {"brand_prefer", "brand_exclude"}:
            return list(dict.fromkeys(normalize_brand(v) for v in value if v))
        return value
    # 尺码类:数字 / 字符串都接受,统一存 str
    # (shoe_size 有 41.5 半码;clothing_size 有 "M"/"175/96A"/32 多种形态)
    if field in {"shoe_size", "clothing_size"}:
        if not isinstance(value, int | float | str):
            raise ToolError(f"{field} 必须为数字或字符串")
        return str(value)
    if not isinstance(value, str):
        raise ToolError(f"{field} 必须为字符串")
    return value


class UpdatePreferenceTool(Tool):
    name: ClassVar[str] = "update_preference"
    description: ClassVar[str] = (
        "把用户的稳定偏好(肤质 / 品牌偏好 / 尺码 / 消费档位等)写进 user_profile。"
        "只在用户明确陈述**本人**事实时调,**不要**为模糊语句 / 本次约束 / 闲聊 / "
        "搜索条件('找女士款')/ 代购语境('给妈妈买')调。"
        "身份基础属性(性别 / 年龄 / 身高 / 体重)和收货信息(地址 / 收件人 / 电话)"
        "不通过本工具写,由用户在个人资料页 / 结算页填。"
        "**撤销**:用户纠正之前的错填时(例如'我不是敏感肌'),传 value=null 清除该字段。"
        "写完后在回复里说一句'已记下你是 ...'或'已清除 ...',让用户能纠正。"
    )
    input_model: ClassVar[type[BaseModel]] = UpdatePreferenceInput

    async def _run(
        self,
        *,
        user_id: str,
        deps: AgentDeps,
        validated_input: BaseModel,
    ) -> ToolResult:
        assert isinstance(validated_input, UpdatePreferenceInput)
        field = validated_input.field
        value = _coerce_value(field, validated_input.value)

        async with deps.session_factory() as session:
            profile = await UserRepo.get_profile(session, user_id)
            if profile is None:
                # 首次写(新用户尚无 profile 行):建一个空 profile。
                # session autoflush=False,需显式 flush 才能让随后的 get_profile
                # SELECT 到刚 merge 的新行,否则仍读到 None。
                await UserRepo.upsert_profile(session, user_id)
                await session.flush()
                profile = await UserRepo.get_profile(session, user_id)
            assert profile is not None

            if field in _COLUMN_FIELDS:
                await session.execute(
                    sa_update(UserProfile)
                    .where(UserProfile.user_id == user_id)
                    .values({field: value})
                )
            elif field in _PREFERENCE_KEYS:
                current = dict(profile.preferences or {})
                if value is None:
                    current.pop(field, None)  # 清除
                else:
                    current[field] = value
                await session.execute(
                    sa_update(UserProfile)
                    .where(UserProfile.user_id == user_id)
                    .values(preferences=current)
                )
            else:
                # Literal 已经在 Pydantic 校验阶段拦了,理论不会到这里
                raise ToolError(f"字段 '{field}' 不在白名单")

            await session.commit()

        log.info("preference_updated", user_id=user_id, field=field, value=value)
        return ToolResult(
            payload={"ok": True, "field": field, "value": value}
        )


__all__ = ["UpdatePreferenceTool"]
