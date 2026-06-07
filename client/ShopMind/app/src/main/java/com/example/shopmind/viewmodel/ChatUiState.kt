package com.example.shopmind.viewmodel

import com.example.shopmind.domain.CardData
import com.example.shopmind.domain.ChatMessage
import com.example.shopmind.domain.SuggestionItem
import com.example.shopmind.domain.UserListItem

/**
 * ChatScreen 状态(§4.7.7)。
 *
 * 高频的逐字流式文字(text / thinking)**不在这里** —— 它们走独立的 [StreamingContent]
 * StateFlow,按帧节流刷新,只触达正在生成的那一个气泡,不牵连顶栏 / 列表重组(招2 隔离)。
 * 本对象只放 turn 边界 / 低频变化的状态。
 *
 * - [messages] 历史已凝固消息(User + Assistant)
 * - [streamingCards] 当前 turn 累积的卡片(低频,几张/turn),done 后 flush 进 messages
 * - [streamingSuggestions] 本 turn 末尾的 follow-up chips(do 后保留在 messages 最后一条 assistant 上)
 * - [toolCallHint] 当前 turn 正在调用的工具名(简短提示,turn 结束清空)
 * - [isLoading] 一轮请求中 = true
 * - [errorMsg] 上一次错误,UI Snackbar 显示;消费后清掉
 */
data class ChatUiState(
    val messages: List<ChatMessage> = emptyList(),
    val streamingCards: List<CardData> = emptyList(),
    val streamingSuggestions: List<SuggestionItem> = emptyList(),
    val toolCallHint: String? = null,
    val isLoading: Boolean = false,
    val errorMsg: String? = null,
    /** 一次性轻提示(如"购物车已更新"),Snackbar 显示后清掉。 */
    val toastMsg: String? = null,
    /** 空状态欢迎区的示例 chip:profile 有信号则个性化,否则静态四条。 */
    val starterChips: List<String> = emptyList(),
    val currentUserId: String = "",
    val currentDisplayName: String = "",
    val sessionId: String = "",
    /** 顶栏 🛒 角标。-1 = 未加载;0 = 空购物车;>0 = 件数。 */
    val cartItemCount: Int = -1,
    /** 顶栏下拉切换 user 用 — Chunk 11 接 GET /users。 */
    val availableUsers: List<UserListItem> = emptyList(),
)

/**
 * 逐字流式内容(招2 隔离 + 招1 节流的载体)。
 *
 * 后端 SSE 的 text / thinking delta 先攒进 ViewModel 的非观察缓冲(StringBuilder),
 * 由一个按帧(~30fps)醒来的协程把当前快照写进这个对象。订阅它的只有正在生成的那个气泡,
 * 故每帧最多重组一次、且只重组那一处 —— 顶栏角标 / 已凝固消息列表不参与。
 */
data class StreamingContent(
    val text: String = "",
    val thinking: String = "",
)
