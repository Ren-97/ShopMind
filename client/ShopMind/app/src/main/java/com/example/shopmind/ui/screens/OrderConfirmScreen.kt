package com.example.shopmind.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.LocationOn
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedCard
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.navigation.NavController
import coil3.compose.AsyncImage
import com.example.shopmind.domain.CartCardData
import com.example.shopmind.domain.CartItemData
import com.example.shopmind.domain.PlaceOrderRequest
import com.example.shopmind.domain.ProfileResponse
import com.example.shopmind.network.RestApi
import com.example.shopmind.ui.nav.Routes
import com.example.shopmind.viewmodel.ChatViewModel
import kotlinx.coroutines.launch

/**
 * 下单确认页 — 跑通业务闭环的最后一步。
 *
 * 入口:Chat CheckoutCard [去结算](整车)或 CartScreen [去下单](可勾选子集)
 * 数据:启动时 GET /cart + GET /profile 拼显示快照
 * 选择:selectedSkuIds 非空 → 只显示/合计/下单这些;空 → 整车
 * 地址:可在本地编辑(只用一次,**不**回写 profile;改 profile 走个人资料页)
 * 提交:POST /order → 成功 popBack 到 chat + chatViewModel.insertOrderCard
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun OrderConfirmScreen(
    navController: NavController,
    chatViewModel: ChatViewModel,
    selectedSkuIds: List<String> = emptyList(),
) {
    val rest = remember { RestApi() }
    var cart by remember { mutableStateOf<CartCardData?>(null) }
    var profile by remember { mutableStateOf<ProfileResponse?>(null) }
    var loading by remember { mutableStateOf(true) }
    var errorMsg by remember { mutableStateOf<String?>(null) }
    var submitting by remember { mutableStateOf(false) }

    // 本地可编辑的地址三件套(覆盖 profile 一次,不回写)
    var address by remember { mutableStateOf("") }
    var recipientName by remember { mutableStateOf("") }
    var phone by remember { mutableStateOf("") }
    var editorOpen by remember { mutableStateOf(false) }

    val scope = rememberCoroutineScope()
    val snackbarHost = remember { SnackbarHostState() }

    LaunchedEffect(Unit) {
        try {
            cart = rest.getCart()
            profile = rest.getProfile()
            address = profile?.address.orEmpty()
            recipientName = profile?.recipientName.orEmpty()
            phone = profile?.phone.orEmpty()
        } catch (e: Exception) {
            errorMsg = e.message ?: "加载下单信息失败"
        } finally {
            loading = false
        }
    }

    // 勾选下单:selectedSkuIds 非空时只取选中项,否则整车
    val displayItems = (cart?.items ?: emptyList()).let { items ->
        if (selectedSkuIds.isEmpty()) items
        else items.filter { it.skuId in selectedSkuIds }
    }
    val displayTotal = displayItems.sumOf { it.subtotal }

    val canSubmit = !submitting && address.isNotBlank() && displayItems.isNotEmpty()

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("确认订单") },
                navigationIcon = {
                    IconButton(onClick = { navController.popBackStack() }) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回")
                    }
                },
            )
        },
        bottomBar = {
            cart?.let {
                Surface(tonalElevation = 3.dp) {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(16.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        Column(modifier = Modifier.weight(1f)) {
                            Text(
                                "合计",
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                            Text(
                                "¥${"%.0f".format(displayTotal)}",
                                style = MaterialTheme.typography.titleLarge,
                                color = MaterialTheme.colorScheme.primary,
                                fontWeight = FontWeight.SemiBold,
                            )
                        }
                        Button(
                            onClick = {
                                submitting = true
                                scope.launch {
                                    try {
                                        val req = PlaceOrderRequest(
                                            address = address.takeIf { it.isNotBlank() },
                                            recipientName = recipientName.takeIf { it.isNotBlank() },
                                            phone = phone.takeIf { it.isNotBlank() },
                                            skuIds = selectedSkuIds.ifEmpty { null },
                                        )
                                        val order = rest.placeOrder(req)
                                        chatViewModel.insertOrderCard(order)
                                        navController.popBackStack(
                                            route = Routes.CHAT,
                                            inclusive = false,
                                        )
                                    } catch (e: Exception) {
                                        snackbarHost.showSnackbar(e.message ?: "下单失败")
                                    } finally {
                                        submitting = false
                                    }
                                }
                            },
                            enabled = canSubmit,
                        ) { Text(if (submitting) "下单中…" else "确认下单") }
                    }
                }
            }
        },
        snackbarHost = { SnackbarHost(snackbarHost) },
    ) { padding ->
        when {
            loading -> Box(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(padding),
                contentAlignment = Alignment.Center,
            ) { CircularProgressIndicator() }

            errorMsg != null -> Box(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(padding)
                    .padding(24.dp),
                contentAlignment = Alignment.Center,
            ) {
                Text(errorMsg!!, color = MaterialTheme.colorScheme.error)
            }

            else -> ConfirmBody(
                items = displayItems,
                address = address,
                recipientName = recipientName,
                phone = phone,
                onEditAddress = { editorOpen = true },
                modifier = Modifier.padding(padding),
            )
        }
    }

    if (editorOpen) {
        AddressEditorDialog(
            initialAddress = address,
            initialName = recipientName,
            initialPhone = phone,
            onConfirm = { a, n, p ->
                address = a
                recipientName = n
                phone = p
                editorOpen = false
            },
            onDismiss = { editorOpen = false },
        )
    }
}

@Composable
private fun ConfirmBody(
    items: List<CartItemData>,
    address: String,
    recipientName: String,
    phone: String,
    onEditAddress: () -> Unit,
    modifier: Modifier = Modifier,
) {
    LazyColumn(
        modifier = modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            AddressBlock(
                address = address,
                recipientName = recipientName,
                phone = phone,
                onEdit = onEditAddress,
            )
        }
        item {
            Text(
                "商品清单",
                style = MaterialTheme.typography.labelLarge,
                fontWeight = FontWeight.SemiBold,
            )
        }
        items(items = items, key = { it.skuId }) { item ->
            OrderConfirmItemRow(item)
        }
    }
}

@Composable
private fun AddressBlock(
    address: String,
    recipientName: String,
    phone: String,
    onEdit: () -> Unit,
) {
    OutlinedCard(shape = RoundedCornerShape(12.dp)) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(12.dp),
            verticalAlignment = Alignment.Top,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Icon(
                Icons.Default.LocationOn,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.primary,
            )
            Column(modifier = Modifier.weight(1f)) {
                if (address.isBlank()) {
                    Text(
                        "请填写收货地址",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.error,
                    )
                } else {
                    val nameAndPhone = listOf(recipientName, phone)
                        .filter { it.isNotBlank() }
                        .joinToString("  ")
                    if (nameAndPhone.isNotEmpty()) {
                        Text(
                            nameAndPhone,
                            style = MaterialTheme.typography.labelMedium,
                            fontWeight = FontWeight.Medium,
                        )
                    }
                    Text(
                        address,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            IconButton(onClick = onEdit) {
                Icon(Icons.Default.Edit, contentDescription = "编辑地址")
            }
        }
    }
}

@Composable
private fun OrderConfirmItemRow(item: CartItemData) {
    OutlinedCard(shape = RoundedCornerShape(8.dp)) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(8.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            AsyncImage(
                model = item.imageUrl,
                contentDescription = item.title,
                modifier = Modifier
                    .size(56.dp)
                    .clip(RoundedCornerShape(6.dp)),
            )
            Column(
                modifier = Modifier.weight(1f),
                verticalArrangement = Arrangement.spacedBy(2.dp),
            ) {
                Text(
                    item.title,
                    style = MaterialTheme.typography.bodySmall,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    "× ${item.qty}",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Text(
                "¥${"%.0f".format(item.subtotal)}",
                style = MaterialTheme.typography.bodyMedium,
            )
        }
    }
}

@Composable
private fun AddressEditorDialog(
    initialAddress: String,
    initialName: String,
    initialPhone: String,
    onConfirm: (address: String, name: String, phone: String) -> Unit,
    onDismiss: () -> Unit,
) {
    var addr by remember { mutableStateOf(initialAddress) }
    var name by remember { mutableStateOf(initialName) }
    var ph by remember { mutableStateOf(initialPhone) }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("编辑收货地址") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it },
                    label = { Text("收件人") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = ph,
                    onValueChange = { ph = it },
                    label = { Text("电话") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = addr,
                    onValueChange = { addr = it },
                    label = { Text("地址") },
                    minLines = 2,
                    maxLines = 4,
                    modifier = Modifier.fillMaxWidth(),
                )
                Text(
                    "本次下单使用此地址,不会更新个人资料。",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        },
        confirmButton = {
            TextButton(onClick = { onConfirm(addr, name, ph) }) { Text("确定") }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("取消") }
        },
    )
}
