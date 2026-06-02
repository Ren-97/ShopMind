package com.example.shopmind.domain

import java.util.UUID

/**
 * UI 端消息模型(不进网络,纯前端状态)。
 *
 * Assistant 消息由 SSE 流凝固而来:本轮所有 text delta 累积成 textBlocks,
 * 卡片按到达顺序 append 到 cards,suggestions 是本轮末尾的 chips。
 */
sealed class ChatMessage {
    abstract val id: String

    data class User(
        override val id: String = UUID.randomUUID().toString(),
        val text: String,
    ) : ChatMessage()

    data class Assistant(
        override val id: String = UUID.randomUUID().toString(),
        val text: String,
        val cards: List<CardData> = emptyList(),
        val suggestions: List<SuggestionItem> = emptyList(),
        val thinking: String = "",
    ) : ChatMessage()
}
