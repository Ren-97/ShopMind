package com.example.shopmind.viewmodel

import com.example.shopmind.domain.CardData
import com.example.shopmind.domain.ChatMessage
import com.example.shopmind.domain.SuggestionItem
import com.example.shopmind.domain.UserListItem

/**
 * ChatScreen 状态(§4.7.7)。
 *
 * - [messages] 历史已凝固消息(User + Assistant)
 * - [streamingText] / [streamingCards] / [streamingThinking] 当前 turn 累积,done 后 flush 进 messages
 * - [streamingSuggestions] 本 turn 末尾的 follow-up chips(do 后保留在 messages 最后一条 assistant 上)
 * - [toolCallHint] 当前 turn 正在调用的工具名(简短提示,turn 结束清空)
 * - [isLoading] 一轮请求中 = true
 * - [errorMsg] 上一次错误,UI Snackbar 显示;消费后清掉
 */
data class ChatUiState(
    val messages: List<ChatMessage> = emptyList(),
    val streamingText: String = "",
    val streamingThinking: String = "",
    val streamingCards: List<CardData> = emptyList(),
    val streamingSuggestions: List<SuggestionItem> = emptyList(),
    val toolCallHint: String? = null,
    val isLoading: Boolean = false,
    val errorMsg: String? = null,
    val currentUserId: String = "",
    val currentDisplayName: String = "",
    val sessionId: String = "",
    /** 顶栏 🛒 角标。-1 = 未加载;0 = 空购物车;>0 = 件数。 */
    val cartItemCount: Int = -1,
    /** 顶栏下拉切换 user 用 — Chunk 11 接 GET /users。 */
    val availableUsers: List<UserListItem> = emptyList(),
)
