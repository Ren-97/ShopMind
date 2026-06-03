package com.example.shopmind.viewmodel

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.example.shopmind.domain.CardData
import com.example.shopmind.domain.ChatMessage
import com.example.shopmind.domain.HistoryCardRefs
import com.example.shopmind.domain.OrderCardData
import com.example.shopmind.domain.ProductCardData
import com.example.shopmind.domain.ProductDetail
import com.example.shopmind.domain.SuggestionItem
import com.example.shopmind.domain.UserListItem
import com.example.shopmind.network.HttpClients
import com.example.shopmind.network.RestApi
import com.example.shopmind.network.SessionStore
import com.example.shopmind.network.SseClient
import kotlinx.coroutines.Job
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.flow.onCompletion
import kotlinx.coroutines.flow.onEach
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/**
 * 主聊天 ViewModel(AndroidViewModel — 需要 Application Context 访问 SessionStore SharedPreferences)。
 *
 * 设计要点:
 *   - 单 session 心智:per user 一个持久 session_id(SessionStore),清空对话(🔄)只删 DB
 *   - 启动时拉 history → 批量并发拉 product/order → 拼成历史消息
 *   - 流式累积:`onTextDelta` 不断 append `streamingText`,done 时 flush 成 Assistant ChatMessage
 *   - SSE 取消语义:发新消息前先 cancel 上一个 job
 */
class ChatViewModel @JvmOverloads constructor(
    application: Application,
    private val sse: SseClient = SseClient(),
    private val rest: RestApi = RestApi(),
) : AndroidViewModel(application) {

    private val _state = MutableStateFlow(
        ChatUiState(
            currentUserId = HttpClients.currentUser(),
            currentDisplayName = displayNameFor(HttpClients.currentUser()),
            sessionId = SessionStore.getOrCreate(application, HttpClients.currentUser()),
        )
    )
    val state: StateFlow<ChatUiState> = _state.asStateFlow()

    private var activeJob: Job? = null

    init {
        loadUsers()
        refreshCartCount()
        loadHistory()
    }

    // ──────────────────────────────────────────────────────────
    // 用户操作入口
    // ──────────────────────────────────────────────────────────
    fun sendMessage(text: String) {
        val query = text.trim()
        if (query.isEmpty() || _state.value.isLoading) return

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

        activeJob?.cancel()
        activeJob = viewModelScope.launch {
            sse.chat(query = query, sessionId = _state.value.sessionId)
                .onEach { event -> EventDispatcher.dispatch(event, this@ChatViewModel) }
                .catch { e ->
                    onError("collect_error", e.message ?: "SSE 收流失败")
                    flushTurn()
                }
                .onCompletion {
                    if (_state.value.isLoading) flushTurn()
                }
                .collect()
        }
    }

    fun consumeError() {
        _state.update { it.copy(errorMsg = null) }
    }

    fun switchUser(userId: String) {
        if (userId == _state.value.currentUserId) return
        HttpClients.setCurrentUser(userId)
        activeJob?.cancel()
        val keepUsers = _state.value.availableUsers
        _state.value = ChatUiState(
            currentUserId = userId,
            currentDisplayName = displayNameFor(userId, keepUsers),
            sessionId = SessionStore.getOrCreate(getApplication(), userId),
            availableUsers = keepUsers,
        )
        refreshCartCount()
        loadHistory()
    }

    /** ➕ 新建空白用户:POST /users → 加进列表 → 切过去(落到空 profile / 空对话)。 */
    fun createUser(displayName: String) {
        val name = displayName.trim()
        if (name.isEmpty()) return
        viewModelScope.launch {
            try {
                val created = rest.createUser(name)
                _state.update { it.copy(availableUsers = it.availableUsers + created) }
                switchUser(created.userId)
            } catch (e: Exception) {
                onError("create_user_failed", e.message ?: "新建用户失败")
            }
        }
    }

    fun refreshCartCount() {
        viewModelScope.launch {
            try {
                val cart = rest.getCart()
                _state.update { it.copy(cartItemCount = cart.itemCount) }
            } catch (e: Exception) {
                // 静默失败 — 非关键路径
            }
        }
    }

    /** 🔄 清空对话(硬删 DB + UI 清空)。 */
    fun clearHistory() {
        viewModelScope.launch {
            try {
                rest.clearHistory()
                _state.update { it.copy(messages = emptyList()) }
            } catch (e: Exception) {
                onError("clear_history_failed", e.message ?: "清空失败")
            }
        }
    }

    private fun loadUsers() {
        viewModelScope.launch {
            try {
                val users = rest.listUsers().filter { !it.userId.startsWith("eval_") }
                _state.update { it.copy(availableUsers = users) }
            } catch (e: Exception) {
                // 静默
            }
        }
    }

    /**
     * 启动 / 切 user 时拉历史消息 → 批量并发拉 product / order → 拼回 messages。
     *
     * 关键设计:**只存引用,实时拉数据**(对齐京东 / 淘宝级别的导购 AI 做法)。
     * 商品下架(404)→ 渲染时跳过该 card,文字部分仍保留 — 不阻塞历史展示。
     */
    private fun loadHistory() {
        viewModelScope.launch {
            try {
                val raw = rest.getHistory()
                if (raw.isEmpty()) {
                    _state.update { it.copy(messages = emptyList()) }
                    return@launch
                }
                // 1. 收集所有引用的 product_ids + order_ids 去重
                val productIds = linkedSetOf<String>()
                val orderIds = linkedSetOf<String>()
                for (m in raw) {
                    val refs = m.cardRefs ?: continue
                    refs.products?.forEach { productIds.add(it) }
                    refs.compare?.forEach { productIds.add(it) }
                    refs.order?.let { orderIds.add(it) }
                }
                // 2. 并发批量拉
                val (productMap, orderMap) = coroutineScope {
                    val products = async {
                        productIds.associateWith { id ->
                            runCatching { rest.getProduct(id) }.getOrNull()
                        }
                    }
                    val orders = async {
                        orderIds.associateWith { id ->
                            runCatching { rest.getOrder(id) }.getOrNull()
                        }
                    }
                    awaitAll(products, orders)
                    products.await() to orders.await()
                }
                // 3. 拼回 ChatMessage 列表
                val messages = raw.map { m ->
                    when (m.role) {
                        "user" -> ChatMessage.User(id = "hist-${m.msgId}", text = m.content)
                        "assistant" -> {
                            val cards = buildList<CardData> {
                                m.cardRefs?.products?.forEach { pid ->
                                    productMap[pid]?.let { add(CardData.Product(productDetailToCardData(it))) }
                                }
                                // compare:简化为顺序展示 product card(V1 不重跑后端 compare 逻辑)
                                m.cardRefs?.compare?.forEach { pid ->
                                    productMap[pid]?.let { add(CardData.Product(productDetailToCardData(it))) }
                                }
                                m.cardRefs?.order?.let { oid ->
                                    orderMap[oid]?.let { add(CardData.Order(it)) }
                                }
                            }
                            ChatMessage.Assistant(
                                id = "hist-${m.msgId}",
                                text = m.content,
                                cards = cards,
                            )
                        }
                        else -> null
                    }
                }.filterNotNull()
                _state.update { it.copy(messages = messages) }
            } catch (e: Exception) {
                // 静默 — 拉历史失败不阻塞用户继续聊天
            }
        }
    }

    fun insertOrderCard(order: OrderCardData) {
        val assistantMsg = ChatMessage.Assistant(
            text = "已为你下单,订单号 ${order.orderId.take(12)}…",
            cards = listOf(CardData.Order(order)),
        )
        _state.update { it.copy(messages = it.messages + assistantMsg) }
        refreshCartCount()
    }

    // ──────────────────────────────────────────────────────────
    // EventDispatcher 回调
    // ──────────────────────────────────────────────────────────
    internal fun onMeta(turnId: String, sessionId: String) {
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

    companion object {
        // V1 hardcoded display names — V2 改为调 GET /users 真实映射
        private val DEMO_DISPLAY_NAMES: Map<String, String> = mapOf(
            "demo_user_1" to "Alice",
            "demo_user_2" to "Bob",
            "demo_user_3" to "Charlie",
        )

        private fun displayNameFor(
            userId: String,
            users: List<UserListItem> = emptyList(),
        ): String =
            users.find { it.userId == userId }?.displayName
                ?: DEMO_DISPLAY_NAMES[userId] ?: userId

        /** ProductDetail → ProductCardData(历史卡片简化版,无 chips)。 */
        private fun productDetailToCardData(d: ProductDetail): ProductCardData =
            ProductCardData(
                productId = d.productId,
                title = d.title,
                brand = d.brand,
                imageUrl = d.imageUrl,
                basePrice = d.basePrice,
                defaultSkuId = null,
                skuCount = d.skus.size,
                tagsCandidates = emptyList(),  // 历史里不展示 chip,避免前端复制 chip 推断逻辑
                inStock = d.inStock,
            )
    }
}
