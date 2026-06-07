package com.example.shopmind.viewmodel

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.example.shopmind.domain.CardData
import com.example.shopmind.domain.CartCardData
import com.example.shopmind.domain.CategoryFacet
import com.example.shopmind.domain.ChatMessage
import com.example.shopmind.domain.HistoryCardRefs
import com.example.shopmind.domain.OrderCardData
import com.example.shopmind.domain.ProductCardData
import com.example.shopmind.domain.ProductDetail
import com.example.shopmind.domain.ProfileResponse
import com.example.shopmind.domain.SuggestionItem
import com.example.shopmind.domain.UserListItem
import com.example.shopmind.network.HttpClients
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonPrimitive
import com.example.shopmind.network.RestApi
import com.example.shopmind.network.SessionStore
import com.example.shopmind.network.SseClient
import kotlinx.coroutines.Job
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.flow.onCompletion
import kotlinx.coroutines.flow.onEach
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * 主聊天 ViewModel(AndroidViewModel — 需要 Application Context 访问 SessionStore SharedPreferences)。
 *
 * 设计要点:
 *   - 单 session 心智:per user 一个持久 session_id(SessionStore),清空对话(🔄)只删 DB
 *   - 启动时拉 history → 批量并发拉 product/order → 拼成历史消息
 *   - 流式累积:`onTextDelta` 把 delta 攒进缓冲,按帧刷进独立的 [streaming] 流,done 时 flush 成 Assistant ChatMessage
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

    /**
     * 逐字流式内容(招2 隔离)。和 [state] 分开,只有正在生成的气泡订阅它,
     * 每帧最多刷一次(见 [streamFlushJob])。
     */
    private val _streaming = MutableStateFlow(StreamingContent())
    val streaming: StateFlow<StreamingContent> = _streaming.asStateFlow()

    private var activeJob: Job? = null

    // ── 招1 节流:delta 先攒进非观察缓冲,按帧醒来的协程批量刷进 _streaming ──
    // text / thinking delta 在主线程串行追加(SSE collect 跑在 viewModelScope=Main),
    // flusher 也在 Main,故无并发,普通 StringBuilder 即可。
    private val textBuf = StringBuilder()
    private val thinkingBuf = StringBuilder()
    private var streamDirty = false
    private var streamFlushJob: Job? = null

    /** 启动按帧刷新循环:有新字才写 _streaming,无字空转一帧 delay,turn 结束 [stopStreamFlusher] 关掉。 */
    private fun startStreamFlusher() {
        streamFlushJob?.cancel()
        streamDirty = false
        streamFlushJob = viewModelScope.launch {
            while (isActive) {
                delay(STREAM_FLUSH_INTERVAL_MS)
                if (streamDirty) {
                    streamDirty = false
                    _streaming.value = StreamingContent(
                        text = textBuf.toString(),
                        thinking = thinkingBuf.toString(),
                    )
                }
            }
        }
    }

    /** 停掉刷新循环并清空缓冲 + 流式快照(turn 结束 / 切用户)。 */
    private fun stopStreamFlusher() {
        streamFlushJob?.cancel()
        streamFlushJob = null
        textBuf.setLength(0)
        thinkingBuf.setLength(0)
        streamDirty = false
        _streaming.value = StreamingContent()
    }

    init {
        loadUsers()
        refreshCartCount()
        refreshStarterChips()
        loadHistory()
    }

    // ──────────────────────────────────────────────────────────
    // 用户操作入口
    // ──────────────────────────────────────────────────────────
    fun sendMessage(text: String) {
        val query = text.trim()
        if (query.isEmpty() || _state.value.isLoading) return

        stopStreamFlusher()
        _state.update { s ->
            s.copy(
                messages = s.messages + ChatMessage.User(text = query),
                isLoading = true,
                streamingCards = emptyList(),
                streamingSuggestions = emptyList(),
                toolCallHint = null,
                errorMsg = null,
            )
        }
        startStreamFlusher()

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

    fun consumeToast() {
        _state.update { it.copy(toastMsg = null) }
    }

    fun switchUser(userId: String) {
        if (userId == _state.value.currentUserId) return
        HttpClients.setCurrentUser(userId)
        activeJob?.cancel()
        stopStreamFlusher()
        val keepUsers = _state.value.availableUsers
        _state.value = ChatUiState(
            currentUserId = userId,
            currentDisplayName = displayNameFor(userId, keepUsers),
            sessionId = SessionStore.getOrCreate(getApplication(), userId),
            availableUsers = keepUsers,
        )
        refreshCartCount()
        refreshStarterChips()
        loadHistory()
    }

    /**
     * ➕ 新建空白用户:POST /users → 加进列表 → 切过去(落到空 profile / 空对话)。
     * 成功后回调 [onCreated](主线程),由 UI 跳个人资料页让用户当场填资料 / 选偏好,
     * 想跳过按返回即可(profile 全可选)。
     */
    fun createUser(displayName: String, onCreated: () -> Unit = {}) {
        val name = displayName.trim()
        if (name.isEmpty()) return
        viewModelScope.launch {
            try {
                val created = rest.createUser(name)
                _state.update { it.copy(availableUsers = it.availableUsers + created) }
                switchUser(created.userId)
                onCreated()
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

    /**
     * 空状态欢迎区示例 chip(库存感知):词全部取自 /catalog/facets(库里在售有货的真实
     * 品牌/子类),点了**构造上保证**有结果;profile 偏好品牌在库时优先出该品牌 chip。无 LLM。
     */
    fun refreshStarterChips() {
        viewModelScope.launch {
            val profile = try { rest.getProfile() } catch (e: Exception) { null }
            val facets = try { rest.getFacets().categories } catch (e: Exception) { emptyList() }
            _state.update { it.copy(starterChips = starterChipsFor(profile, facets)) }
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

    /**
     * 下单成功回到 chat 后补一条收尾气泡 + follow-up chip(本地生成,不调 LLM)。
     * 真下单走 REST POST /order **绕过 agent**,agent 不会 emit suggestions —— 这里手动补,
     * 否则下单后对话戛然而止、也没有 chip 引导用户继续。
     */
    fun insertOrderCard(order: OrderCardData) {
        val firstTitle = order.items.firstOrNull()?.title?.takeIf { it.isNotBlank() }
        val suggestions = buildList {
            if (firstTitle != null) {
                add(SuggestionItem(label = "看看搭配", query = "有适合和「$firstTitle」搭配的吗?"))
            }
            add(SuggestionItem(label = "再逛逛", query = "再给我推荐点别的好物"))
        }
        val assistantMsg = ChatMessage.Assistant(
            text = "已经帮你下单啦 ✅ 订单号 ${order.orderId.take(12)}…,会尽快安排发货。还想再看看别的吗?",
            cards = listOf(CardData.Order(order)),
            suggestions = suggestions,
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
        // 只追加缓冲 + 标脏,实际刷屏交给 startStreamFlusher 按帧批量做(招1)
        thinkingBuf.append(delta)
        streamDirty = true
    }

    internal fun onToolCall(name: String) {
        // 工具名 → 友好状态文案(一闪而过的状态条,turn 结束清空)。
        // 内部工具(show_suggestions 等)→ null,不打扰用户。
        val hint = when (name) {
            "search_products" -> "正在帮你搜索商品…"
            "compare_products" -> "正在帮你对比…"
            "manage_cart" -> "正在更新购物车…"
            "start_checkout" -> "正在准备结算…"
            "update_preference" -> "正在记住你的偏好…"
            "recall_history" -> "正在翻看历史对话…"
            else -> null
        }
        _state.update { it.copy(toolCallHint = hint) }
    }

    internal fun onCard(card: CardData) {
        // 购物车卡是"动作回执",不插进对话流 —— 改成 snackbar + 角标跳数,
        // 避免和上方商品卡重复渲染同一商品(manage_cart 是其唯一流式来源)。
        if (card is CardData.Cart) {
            applyCartSnapshot(card.data)
            return
        }
        _state.update { it.copy(streamingCards = it.streamingCards + card) }
    }

    /**
     * 用户在聊天内嵌的规格选择卡点选规格后加购:直接走 POST /cart(不经 agent,零延迟)。
     * 成功后补一条导购跟进气泡 + "去结算"引导 chip(本地生成,不调 LLM),给对话一个收尾、
     * 顺势推进下单 —— 否则加完购物车原地不动会显得很突兀。
     */
    fun addSkuFromSelector(skuId: String, title: String, qty: Int = 1) {
        viewModelScope.launch {
            try {
                applyCartSnapshot(rest.addToCart(skuId, qty))
                val qtyHint = if (qty > 1) " ×$qty" else ""
                val followUp = ChatMessage.Assistant(
                    text = "已经帮你把「$title」$qtyHint 加进购物车啦 🛒 要现在去结算,还是再看看别的?",
                    suggestions = listOf(
                        SuggestionItem(label = "去结算", query = "我要结算下单"),
                        SuggestionItem(label = "再看看别的", query = "再看看别的"),
                    ),
                )
                _state.update { it.copy(messages = it.messages + followUp) }
            } catch (e: Exception) {
                onError("add_to_cart_failed", e.message ?: "加入购物车失败")
            }
        }
    }

    /** 购物车快照 → 更新角标 + toast 回执(不插卡进对话流)。onCard 与选择卡加购共用。 */
    private fun applyCartSnapshot(data: CartCardData) {
        val count = data.itemCount
        _state.update {
            it.copy(
                cartItemCount = count,
                toastMsg = if (count <= 0) "购物车已清空"
                else "购物车已更新 · 共 $count 件 · ¥${"%.0f".format(data.totalPrice)}",
            )
        }
    }

    internal fun onTextDelta(delta: String) {
        // 同 onThinkingDelta:攒进缓冲,按帧刷(招1)
        textBuf.append(delta)
        streamDirty = true
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
        // 缓冲是最终文本的真相源(节流快照可能差一帧),先取出再停 flusher 清空
        val finalText = textBuf.toString()
        val finalThinking = thinkingBuf.toString()
        stopStreamFlusher()
        _state.update { s ->
            val assistantMsg = ChatMessage.Assistant(
                text = finalText,
                cards = s.streamingCards,
                suggestions = s.streamingSuggestions,
                thinking = finalThinking,
            )
            val hasAnyContent = assistantMsg.text.isNotEmpty() ||
                assistantMsg.cards.isNotEmpty() ||
                assistantMsg.suggestions.isNotEmpty()
            s.copy(
                messages = if (hasAnyContent) s.messages + assistantMsg else s.messages,
                streamingCards = emptyList(),
                streamingSuggestions = emptyList(),
                toolCallHint = null,
                isLoading = false,
            )
        }
    }

    companion object {
        // 流式刷屏节流间隔(招1):~30fps。把后端忽快忽慢的 delta 节奏削成稳定帧率,
        // 肉眼仍是逐字冒出,但屏幕每秒最多刷 ~30 次而非跟着 token 数狂刷。
        private const val STREAM_FLUSH_INTERVAL_MS = 33L

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

        // facets 拉取失败(后端不可达)时的最后兜底 —— 通用、不假设任何具体品类
        private val STATIC_FALLBACK_CHIPS = listOf(
            "有什么好物推荐", "最近热门的商品", "帮我挑份礼物", "性价比高的有哪些",
        )

        // 类目 chip 的问法模板池(洗牌取,4 条问法不重复)。10 个够用,再多易凑出别扭句子;
        // 真正的新鲜感主要靠随机换子类。{0}=子类名
        private val CATEGORY_CHIP_TEMPLATES = listOf(
            "想挑一款{0}",
            "{0}有什么推荐",
            "看看热门{0}",
            "有什么好的{0}",
            "{0}怎么挑",
            "帮我看看{0}",
            "{0}求推荐",
            "想入手{0}",
            "有没有值得买的{0}",
            "{0}哪款好",
        )

        /**
         * 库存感知 chip:词全部来自 facets(库里在售有货),故每条点了都有结果。
         * 个性化 = 偏好品牌在库时(命中某类目 brands)优先出该品牌 chip。
         * 类目 chip 每条**随机**取该类目一个子类 + 一个不重复问法模板 →
         * 每次进来 topic 和问法都在变,不重复也不死板;facets 保证仍在(子类都来自库存)。
         * facets 为空 → 退回 [STATIC_FALLBACK_CHIPS]。恒返回 ≤4 个。
         */
        private fun starterChipsFor(
            profile: ProfileResponse?,
            facets: List<CategoryFacet>,
        ): List<String> {
            if (facets.isEmpty()) return STATIC_FALLBACK_CHIPS

            val prefs = profile?.preferences ?: emptyMap()
            val preferredBrand =
                ((prefs["brand_prefer"] as? JsonArray)?.firstOrNull() as? JsonPrimitive)?.content

            val chips = mutableListOf<String>()
            // 1. 偏好品牌 chip —— 仅当该品牌真的在库里有货(问法天然区别于类目 chip)
            if (preferredBrand != null && facets.any { preferredBrand in it.brands }) {
                chips.add("${preferredBrand}最近有什么值得入的")
            }
            // 2. 各类目随机挑一个子类,配一个洗牌后的不重复问法模板
            val templates = CATEGORY_CHIP_TEMPLATES.shuffled()
            var t = 0
            for (cat in facets) {
                if (chips.size >= 4) break
                val sub = cat.subCategories.randomOrNull() ?: continue
                chips.add(templates[t % templates.size].replace("{0}", sub))
                t++
            }
            return chips.take(4)
        }

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
