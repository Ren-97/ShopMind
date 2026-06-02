package com.example.shopmind.domain

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * SSE 8 种 event 解析后产物(§4.7.1)。
 *
 * 后端 wire 是 `event: <type>\ndata: <JSON 字符串>` — 本类把已切开的
 * `(type, dataJson)` 解析成 sealed class 实例。
 */
sealed class AgentEvent {

    data class Meta(
        val sessionId: String,
        val turnId: String,
        val userId: String,
    ) : AgentEvent()

    data class Thinking(val delta: String) : AgentEvent()
    data class ToolCall(val name: String) : AgentEvent()
    data class CardEvent(val card: CardData) : AgentEvent()
    data class Text(val delta: String) : AgentEvent()
    data class Suggestions(val items: List<SuggestionItem>) : AgentEvent()
    data class Done(val finishReason: String) : AgentEvent()
    data class ErrorEvent(val code: String, val message: String) : AgentEvent()

    /** 未识别 event type,日志打一下即可。 */
    data class Unknown(val rawType: String, val rawData: String) : AgentEvent()

    companion object {
        fun parse(json: Json, type: String, dataJson: String): AgentEvent {
            return try {
                when (type) {
                    "meta" -> json.decodeFromString(MetaWire.serializer(), dataJson).toEvent()
                    "thinking" -> json.decodeFromString(DeltaWire.serializer(), dataJson).let { Thinking(it.delta) }
                    "tool_call" -> json.decodeFromString(ToolCallWire.serializer(), dataJson).let { ToolCall(it.name) }
                    "card" -> {
                        val element = json.parseToJsonElement(dataJson)
                        CardEvent(CardData.parse(json, element))
                    }
                    "text" -> json.decodeFromString(DeltaWire.serializer(), dataJson).let { Text(it.delta) }
                    "suggestions" -> json.decodeFromString(SuggestionsWire.serializer(), dataJson).let { Suggestions(it.items) }
                    "done" -> json.decodeFromString(DoneWire.serializer(), dataJson).let { Done(it.finishReason) }
                    "error" -> json.decodeFromString(ErrorWire.serializer(), dataJson).let { ErrorEvent(it.code, it.msg) }
                    else -> Unknown(type, dataJson)
                }
            } catch (e: Exception) {
                Unknown(type, "parse_error: ${e.message} | raw=$dataJson")
            }
        }
    }
}

// ──────────────────────────────────────────────────────────────
// Wire 形态(后端 JSON 直接对齐)— internal,仅 parse 用
// ──────────────────────────────────────────────────────────────

@Serializable
private data class MetaWire(
    @SerialName("session_id") val sessionId: String,
    @SerialName("turn_id") val turnId: String,
    @SerialName("user_id") val userId: String,
) {
    fun toEvent() = AgentEvent.Meta(sessionId, turnId, userId)
}

@Serializable
private data class DeltaWire(val delta: String)

@Serializable
private data class ToolCallWire(val name: String)

@Serializable
private data class SuggestionsWire(val items: List<SuggestionItem>)

@Serializable
private data class DoneWire(
    @SerialName("finish_reason") val finishReason: String,
)

@Serializable
private data class ErrorWire(val code: String, val msg: String)
