package com.example.shopmind.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.ShoppingCart
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.CenterAlignedTopAppBar
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavController
import com.example.shopmind.domain.CardData
import com.example.shopmind.domain.ChatMessage
import com.example.shopmind.domain.SuggestionItem
import com.example.shopmind.ui.components.CardListRenderer
import com.example.shopmind.ui.components.SuggestionChips
import com.example.shopmind.ui.components.ThinkingBubble
import com.example.shopmind.ui.nav.Routes
import com.example.shopmind.viewmodel.ChatViewModel

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(
    navController: NavController,
    vm: ChatViewModel = viewModel(),
) {
    val state by vm.state.collectAsState()
    val listState = rememberLazyListState()
    val snackbarHost = remember { SnackbarHostState() }
    var inputText by remember { mutableStateOf("") }

    LaunchedEffect(state.messages.size, state.streamingText.length, state.isLoading) {
        val targetIndex = state.messages.size + (if (state.isLoading) 1 else 0) - 1
        if (targetIndex >= 0) listState.animateScrollToItem(targetIndex.coerceAtLeast(0))
    }

    LaunchedEffect(state.errorMsg) {
        state.errorMsg?.let {
            snackbarHost.showSnackbar(it)
            vm.consumeError()
        }
    }

    val cartCount = 0 // Chunk 11 接真实 cart count

    // ── 卡片点击回调(共享给已完成消息和 streaming 消息)──
    val onProductClick: (String) -> Unit = { id ->
        navController.navigate(Routes.productDetail(id))
    }
    val onCartClick: () -> Unit = { navController.navigate(Routes.CART) }
    val onCheckoutClick: () -> Unit = { navController.navigate(Routes.CHECKOUT) }
    val onSuggestionClick: (SuggestionItem) -> Unit = { item ->
        // V1:所有 suggestion 都当消息发出去(包括"去结算"也由 Agent 处理路由判断)
        vm.sendMessage(item.query)
    }

    Scaffold(
        topBar = {
            CenterAlignedTopAppBar(
                title = { Text("ShopMind") },
                navigationIcon = {
                    AssistChip(
                        onClick = { /* Chunk 11 弹下拉菜单 */ },
                        label = { Text(state.currentUserId.ifEmpty { "Alice" }) },
                        trailingIcon = {
                            Icon(Icons.Default.ArrowDropDown, contentDescription = "切换用户")
                        },
                        modifier = Modifier.padding(start = 8.dp),
                    )
                },
                actions = {
                    BadgedBox(
                        badge = { if (cartCount > 0) Badge { Text(cartCount.toString()) } },
                        modifier = Modifier.padding(end = 12.dp),
                    ) {
                        IconButton(onClick = { navController.navigate(Routes.CART) }) {
                            Icon(Icons.Default.ShoppingCart, contentDescription = "购物车")
                        }
                    }
                },
            )
        },
        bottomBar = {
            ChatInputBar(
                value = inputText,
                onValueChange = { inputText = it },
                onSend = {
                    val toSend = inputText
                    inputText = ""
                    vm.sendMessage(toSend)
                },
                enabled = !state.isLoading,
            )
        },
        snackbarHost = { SnackbarHost(snackbarHost) },
    ) { innerPadding ->
        LazyColumn(
            state = listState,
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding),
            contentPadding = PaddingValues(horizontal = 12.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            items(items = state.messages, key = { it.id }) { msg ->
                when (msg) {
                    is ChatMessage.User -> UserBubble(msg.text)
                    is ChatMessage.Assistant -> AssistantBubble(
                        text = msg.text,
                        cards = msg.cards,
                        suggestions = msg.suggestions,
                        thinking = msg.thinking,
                        toolCallHint = null,
                        onProductClick = onProductClick,
                        onCartClick = onCartClick,
                        onCheckoutClick = onCheckoutClick,
                        onSuggestionClick = onSuggestionClick,
                    )
                }
            }
            if (state.isLoading) {
                item(key = "__streaming__") {
                    AssistantBubble(
                        text = state.streamingText,
                        cards = state.streamingCards,
                        suggestions = state.streamingSuggestions,
                        thinking = state.streamingThinking,
                        toolCallHint = state.toolCallHint,
                        showSpinnerIfNothing = true,
                        onProductClick = onProductClick,
                        onCartClick = onCartClick,
                        onCheckoutClick = onCheckoutClick,
                        onSuggestionClick = onSuggestionClick,
                    )
                }
            }
        }
    }
}

// ──────────────────────────────────────────────────────────────
// Bubbles
// ──────────────────────────────────────────────────────────────
@Composable
private fun UserBubble(text: String) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.End,
    ) {
        Surface(
            shape = RoundedCornerShape(16.dp),
            color = MaterialTheme.colorScheme.primaryContainer,
            modifier = Modifier.padding(start = 48.dp),
        ) {
            Text(
                text = text,
                modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
                color = MaterialTheme.colorScheme.onPrimaryContainer,
            )
        }
    }
}

@Composable
private fun AssistantBubble(
    text: String,
    cards: List<CardData>,
    suggestions: List<SuggestionItem>,
    thinking: String,
    toolCallHint: String?,
    onProductClick: (String) -> Unit,
    onCartClick: () -> Unit,
    onCheckoutClick: () -> Unit,
    onSuggestionClick: (SuggestionItem) -> Unit,
    showSpinnerIfNothing: Boolean = false,
) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .padding(end = 0.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        if (thinking.isNotEmpty()) {
            ThinkingBubble(text = thinking, modifier = Modifier.padding(end = 48.dp))
        }
        if (toolCallHint != null) {
            Text(
                "🔧 $toolCallHint",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(start = 4.dp),
            )
        }
        if (cards.isNotEmpty()) {
            CardListRenderer(
                cards = cards,
                onProductClick = onProductClick,
                onCartClick = onCartClick,
                onCheckoutClick = onCheckoutClick,
                modifier = Modifier.padding(end = 12.dp),
            )
        }
        when {
            text.isNotEmpty() -> {
                Surface(
                    shape = RoundedCornerShape(16.dp),
                    color = MaterialTheme.colorScheme.surface,
                    modifier = Modifier.padding(end = 48.dp),
                ) {
                    Text(
                        text = text,
                        modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
                        color = MaterialTheme.colorScheme.onSurface,
                    )
                }
            }
            showSpinnerIfNothing && cards.isEmpty() && thinking.isEmpty() -> {
                Surface(
                    shape = RoundedCornerShape(16.dp),
                    color = MaterialTheme.colorScheme.surface,
                    modifier = Modifier.padding(end = 48.dp),
                ) {
                    Box(
                        modifier = Modifier.padding(14.dp),
                        contentAlignment = Alignment.Center,
                    ) {
                        CircularProgressIndicator(strokeWidth = 2.dp)
                    }
                }
            }
        }
        if (suggestions.isNotEmpty()) {
            SuggestionChips(
                items = suggestions,
                onSelect = onSuggestionClick,
                modifier = Modifier.padding(end = 12.dp),
            )
        }
    }
}

@Composable
private fun ChatInputBar(
    value: String,
    onValueChange: (String) -> Unit,
    onSend: () -> Unit,
    enabled: Boolean,
) {
    Surface(tonalElevation = 3.dp) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            OutlinedTextField(
                value = value,
                onValueChange = onValueChange,
                placeholder = { Text("说点什么...") },
                modifier = Modifier.weight(1f),
                enabled = enabled,
                singleLine = false,
                maxLines = 4,
            )
            IconButton(onClick = onSend, enabled = enabled && value.isNotBlank()) {
                Icon(Icons.AutoMirrored.Filled.Send, contentDescription = "发送")
            }
        }
    }
}
