package com.example.shopmind.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.example.shopmind.domain.CardData
import com.example.shopmind.domain.ChatMessage
import com.example.shopmind.domain.SuggestionItem
import com.example.shopmind.network.ApiConfig
import com.example.shopmind.network.HttpClients
import com.example.shopmind.network.SseClient
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.flow.onCompletion
import kotlinx.coroutines.flow.onEach
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.util.UUID

/**
 * 主聊天 ViewModel。
 *
 * 设计要点(§4.7.7):
 *   - 单 session 设计:per-user 只持一个 session_id(切 user 时换新);后端有能力多 session,V1 UI 不暴露
 *   - 流式累积:`onTextDelta` 不断 append `streamingText`,done 时 flush 成 Assistant ChatMessage
 *   - SSE 取消语义:发新消息前先 cancel 上一个 job(避免两个流并存)
 */
class ChatViewModel(
    private val sse: SseClient = SseClient(),
) : ViewModel() {

    private val _state = MutableStateFlow(
        ChatUiState(
            currentUserId = HttpClients.currentUser(),
            sessionId = freshSessionId(),
        )
    )
    val state: StateFlow<ChatUiState> = _state.asStateFlow()

    private var activeJob: Job? = null

    // ──────────────────────────────────────────────────────────
    // 用户操作入口
    // ──────────────────────────────────────────────────────────
    fun sendMessage(text: String) {
        val query = text.trim()
        if (query.isEmpty() || _state.value.isLoading) return

        // 把用户消息追加到历史
        _state.update { s ->
            s.copy(
                messages = s.messages + ChatMessage.User(text = query),
                isLoading = true,
                streamingText = "",
                streamingThinking = "",
                streamingCards = emptyList(),
                streamingSuggestions = emptyList(),
                toolCallHint = null,
                errorMsg = null,
            )
        }

        // 启动 SSE 流
        activeJob?.cancel()
        activeJob = viewModelScope.launch {
            sse.chat(query = query, sessionId = _state.value.sessionId)
                .onEach { event -> EventDispatcher.dispatch(event, this@ChatViewModel) }
                .catch { e ->
                    onError("collect_error", e.message ?: "SSE 收流失败")
                    flushTurn()
                }
                .onCompletion { /* done event 已 flush 过;此处兜底 isLoading */
                    if (_state.value.isLoading) flushTurn()
                }
                .collect()
        }
    }

    fun consumeError() {
        _state.update { it.copy(errorMsg = null) }
    }

    fun switchUser(userId: String) {
        HttpClients.setCurrentUser(userId)
        activeJob?.cancel()
        _state.value = ChatUiState(
            currentUserId = userId,
            sessionId = freshSessionId(),
        )
    }

    // ──────────────────────────────────────────────────────────
    // EventDispatcher 回调(internal 给 dispatcher 用)
    // ──────────────────────────────────────────────────────────
    internal fun onMeta(turnId: String, sessionId: String) {
        // 第一次 turn 时,接受后端 session_id 作为权威(本地若是 null / 空,用这个)
        _state.update { s ->
            if (s.sessionId.isEmpty()) s.copy(sessionId = sessionId) else s
        }
    }

    internal fun onThinkingDelta(delta: String) {
        _state.update { it.copy(streamingThinking = it.streamingThinking + delta) }
    }

    internal fun onToolCall(name: String) {
        _state.update { it.copy(toolCallHint = "调用 $name…") }
    }

    internal fun onCard(card: CardData) {
        _state.update { it.copy(streamingCards = it.streamingCards + card) }
    }

    internal fun onTextDelta(delta: String) {
        _state.update { it.copy(streamingText = it.streamingText + delta) }
    }

    internal fun onSuggestions(items: List<SuggestionItem>) {
        _state.update { it.copy(streamingSuggestions = items) }
    }

    internal fun onDone(finishReason: String) {
        flushTurn()
    }

    internal fun onError(code: String, message: String) {
        _state.update { it.copy(errorMsg = "[$code] $message") }
    }

    // ──────────────────────────────────────────────────────────
    // 内部
    // ──────────────────────────────────────────────────────────
    private fun flushTurn() {
        _state.update { s ->
            val assistantMsg = ChatMessage.Assistant(
                text = s.streamingText,
                cards = s.streamingCards,
                suggestions = s.streamingSuggestions,
                thinking = s.streamingThinking,
            )
            val hasAnyContent = assistantMsg.text.isNotEmpty() ||
                assistantMsg.cards.isNotEmpty() ||
                assistantMsg.suggestions.isNotEmpty()
            s.copy(
                messages = if (hasAnyContent) s.messages + assistantMsg else s.messages,
                streamingText = "",
                streamingThinking = "",
                streamingCards = emptyList(),
                streamingSuggestions = emptyList(),
                toolCallHint = null,
                isLoading = false,
            )
        }
    }

    private fun freshSessionId(): String =
        "sess-" + UUID.randomUUID().toString().replace("-", "").take(12)
}
