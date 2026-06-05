package com.example.shopmind.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Star
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
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
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.navigation.NavController
import coil3.compose.AsyncImage
import com.example.shopmind.domain.ProductDetail
import com.example.shopmind.domain.computeSkuDimensions
import com.example.shopmind.domain.findMatchingSku
import com.example.shopmind.network.RestApi
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull

/** 常见问题 / 用户评论默认预览条数,超出折叠到「查看全部」。 */
private const val DETAIL_PREVIEW_COUNT = 3

/**
 * 商品详情(GET /product/{id})+ SKU selector + 加购按钮。
 *
 * SKU 选择是落地产品的"决策入口" — 用户在 chat 看到商品 card 是探查,
 * 进详情页选规格才能加购,跟京东 / 淘宝商详的 UX 一致。
 */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun ProductDetailScreen(
    navController: NavController,
    productId: String,
    onCartChanged: () -> Unit = {},
) {
    val rest = remember { RestApi() }
    var detail by remember { mutableStateOf<ProductDetail?>(null) }
    var loading by remember { mutableStateOf(true) }
    var errorMsg by remember { mutableStateOf<String?>(null) }
    var adding by remember { mutableStateOf(false) }
    val selected = remember { mutableStateMapOf<String, String>() }
    val scope = rememberCoroutineScope()
    val snackbarHost = remember { SnackbarHostState() }

    LaunchedEffect(productId) {
        loading = true
        errorMsg = null
        try {
            detail = rest.getProduct(productId)
        } catch (e: Exception) {
            errorMsg = e.message ?: "加载详情失败"
        } finally {
            loading = false
        }
    }

    val dimensions = remember(detail) {
        detail?.skus?.let { skus -> computeSkuDimensions(skus.map { it.properties }) } ?: emptyMap()
    }
    val matchedSku = remember(detail, selected.toMap()) {
        detail?.let { findMatchingSku(it.skus, selected, dimensions.keys) { sku -> sku.properties } }
    }
    val canAdd = matchedSku != null && detail?.inStock == true && !adding

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("商品详情") },
                navigationIcon = {
                    IconButton(onClick = { navController.popBackStack() }) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回")
                    }
                },
            )
        },
        bottomBar = {
            if (detail != null) {
                Surface(tonalElevation = 3.dp) {
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(16.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        val price = matchedSku?.price ?: detail!!.basePrice
                        Text(
                            "¥${"%.0f".format(price)}",
                            style = MaterialTheme.typography.titleLarge,
                            color = MaterialTheme.colorScheme.primary,
                            fontWeight = FontWeight.SemiBold,
                            modifier = Modifier.weight(1f),
                        )
                        Button(
                            onClick = {
                                val sku = matchedSku ?: return@Button
                                adding = true
                                scope.launch {
                                    try {
                                        rest.addToCart(skuId = sku.skuId, qty = 1)
                                        onCartChanged()
                                        snackbarHost.showSnackbar("已加入购物车")
                                    } catch (e: Exception) {
                                        snackbarHost.showSnackbar(e.message ?: "加购失败")
                                    } finally {
                                        adding = false
                                    }
                                }
                            },
                            enabled = canAdd,
                        ) {
                            Text(
                                when {
                                    detail?.inStock == false -> "缺货"
                                    matchedSku == null && dimensions.isNotEmpty() -> "请选规格"
                                    adding -> "添加中…"
                                    else -> "加入购物车"
                                }
                            )
                        }
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
                Text(
                    errorMsg!!,
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.error,
                )
            }

            detail != null -> DetailBody(
                detail = detail!!,
                dimensions = dimensions,
                selected = selected,
                onSelect = { key, value -> selected[key] = value },
                modifier = Modifier.padding(padding),
            )
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun DetailBody(
    detail: ProductDetail,
    dimensions: Map<String, List<String>>,
    selected: Map<String, String>,
    onSelect: (String, String) -> Unit,
    modifier: Modifier = Modifier,
) {
    var faqsExpanded by remember { mutableStateOf(false) }
    var reviewsExpanded by remember { mutableStateOf(false) }
    LazyColumn(
        modifier = modifier.fillMaxSize(),
        contentPadding = PaddingValues(bottom = 16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        // 大图
        item {
            AsyncImage(
                model = detail.imageUrl,
                contentDescription = detail.title,
                modifier = Modifier
                    .fillMaxWidth()
                    .aspectRatio(1f),
            )
        }
        // 标题 + 品牌 + 类目
        item {
            Column(
                modifier = Modifier.padding(horizontal = 16.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                Text(
                    detail.brand,
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Text(
                    detail.title,
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold,
                )
                detail.subCategory?.let {
                    Text(
                        "${detail.category ?: ""} · $it",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
        // SKU selector
        if (dimensions.isNotEmpty()) {
            item {
                Column(
                    modifier = Modifier.padding(horizontal = 16.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Text(
                        "选择规格",
                        style = MaterialTheme.typography.labelLarge,
                        fontWeight = FontWeight.SemiBold,
                    )
                    dimensions.forEach { (key, values) ->
                        Text(
                            key,
                            style = MaterialTheme.typography.labelMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        FlowRow(
                            horizontalArrangement = Arrangement.spacedBy(6.dp),
                            verticalArrangement = Arrangement.spacedBy(6.dp),
                        ) {
                            values.forEach { v ->
                                FilterChip(
                                    selected = selected[key] == v,
                                    onClick = { onSelect(key, v) },
                                    label = { Text(v) },
                                )
                            }
                        }
                    }
                }
            }
        }
        item { PropertiesSection(detail, Modifier.padding(horizontal = 16.dp)) }
        detail.marketingDescription?.takeIf { it.isNotBlank() }?.let { desc ->
            item {
                Column(modifier = Modifier.padding(horizontal = 16.dp)) {
                    SectionTitle("商品介绍")
                    Text(desc, style = MaterialTheme.typography.bodyMedium)
                }
            }
        }
        if (detail.faqs.isNotEmpty()) {
            item {
                Column(modifier = Modifier.padding(horizontal = 16.dp)) {
                    SectionTitle("常见问题")
                }
            }
            val visibleFaqs = if (faqsExpanded) detail.faqs else detail.faqs.take(DETAIL_PREVIEW_COUNT)
            items(items = visibleFaqs, key = { it.question }) { faq ->
                Column(
                    modifier = Modifier.padding(horizontal = 16.dp),
                    verticalArrangement = Arrangement.spacedBy(2.dp),
                ) {
                    Text(
                        "Q: ${faq.question}",
                        style = MaterialTheme.typography.labelMedium,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Text(
                        "A: ${faq.answer}",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            if (detail.faqs.size > DETAIL_PREVIEW_COUNT) {
                item {
                    TextButton(
                        onClick = { faqsExpanded = !faqsExpanded },
                        modifier = Modifier.padding(horizontal = 8.dp),
                    ) {
                        Text(if (faqsExpanded) "收起" else "查看全部 ${detail.faqs.size} 条问题")
                    }
                }
            }
        }
        if (detail.reviews.isNotEmpty()) {
            item {
                Column(modifier = Modifier.padding(horizontal = 16.dp)) {
                    SectionTitle("用户评论 (${detail.reviews.size})")
                }
            }
            // 评论摘要卡(大家怎么说)— 评论列表之上,读评论前先定调(对标 Amazon Customers say)
            if (!detail.highlights.isNullOrBlank() || !detail.caveats.isNullOrBlank()) {
                item {
                    ReviewSummaryCard(
                        highlights = detail.highlights,
                        caveats = detail.caveats,
                        reviewCount = detail.reviews.size,
                        modifier = Modifier.padding(horizontal = 16.dp),
                    )
                }
            }
            val visibleReviews = if (reviewsExpanded) detail.reviews else detail.reviews.take(DETAIL_PREVIEW_COUNT)
            items(items = visibleReviews, key = { it.reviewId }) { r ->
                Column(modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        repeat((r.rating ?: 0).coerceIn(0, 5)) {
                            Icon(
                                Icons.Default.Star,
                                contentDescription = null,
                                tint = Color(0xFFFFB300),
                                modifier = Modifier.height(14.dp),
                            )
                        }
                        Text(
                            r.nickname ?: "匿名用户",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.padding(start = 6.dp),
                        )
                    }
                    Text(r.content, style = MaterialTheme.typography.bodySmall)
                }
                HorizontalDivider(modifier = Modifier.padding(horizontal = 16.dp))
            }
            if (detail.reviews.size > DETAIL_PREVIEW_COUNT) {
                item {
                    TextButton(
                        onClick = { reviewsExpanded = !reviewsExpanded },
                        modifier = Modifier.padding(horizontal = 8.dp),
                    ) {
                        Text(if (reviewsExpanded) "收起" else "查看全部 ${detail.reviews.size} 条评论")
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun PropertiesSection(detail: ProductDetail, modifier: Modifier = Modifier) {
    val effects = detail.properties["effects"]?.let { primToList(it) }.orEmpty()
    val scene = detail.properties["scene"]?.let { primToList(it) }.orEmpty()
    val suitableSkin = detail.properties["suitable_skin"]?.let { primToList(it) }.orEmpty()

    if (effects.isEmpty() && scene.isEmpty() && suitableSkin.isEmpty()) return

    Column(modifier = modifier, verticalArrangement = Arrangement.spacedBy(8.dp)) {
        SectionTitle("商品特性")
        if (effects.isNotEmpty()) PropertyChipRow(label = "功效", values = effects)
        if (scene.isNotEmpty()) PropertyChipRow(label = "适用场景", values = scene)
        if (suitableSkin.isNotEmpty()) PropertyChipRow(label = "适合肤质", values = suitableSkin)
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun PropertyChipRow(label: String, values: List<String>) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Text(
            label,
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        FlowRow(
            horizontalArrangement = Arrangement.spacedBy(6.dp),
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            values.forEach { v ->
                Surface(
                    shape = RoundedCornerShape(6.dp),
                    color = MaterialTheme.colorScheme.secondaryContainer,
                ) {
                    Text(
                        v,
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSecondaryContainer,
                        modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                    )
                }
            }
        }
    }
}

@Composable
private fun ReviewSummaryCard(
    highlights: String?,
    caveats: String?,
    reviewCount: Int,
    modifier: Modifier = Modifier,
) {
    Surface(
        color = MaterialTheme.colorScheme.surfaceVariant,
        shape = RoundedCornerShape(10.dp),
        modifier = modifier.fillMaxWidth(),
    ) {
        Column(
            modifier = Modifier.padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                "大家怎么说 · AI 摘要自 $reviewCount 条评论",
                style = MaterialTheme.typography.labelMedium,
                fontWeight = FontWeight.SemiBold,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            highlights?.takeIf { it.isNotBlank() }?.let {
                SummaryRow(label = "优点", labelColor = Color(0xFF2E7D32), text = it)
            }
            caveats?.takeIf { it.isNotBlank() }?.let {
                SummaryRow(label = "注意", labelColor = Color(0xFFEF6C00), text = it)
            }
        }
    }
}

@Composable
private fun SummaryRow(label: String, labelColor: Color, text: String) {
    Row(
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalAlignment = Alignment.Top,
    ) {
        Surface(shape = RoundedCornerShape(4.dp), color = labelColor.copy(alpha = 0.12f)) {
            Text(
                label,
                style = MaterialTheme.typography.labelSmall,
                fontWeight = FontWeight.SemiBold,
                color = labelColor,
                modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp),
            )
        }
        Text(
            text,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurface,
            modifier = Modifier.weight(1f),
        )
    }
}

@Composable
private fun SectionTitle(text: String) {
    Text(
        text,
        style = MaterialTheme.typography.labelLarge,
        fontWeight = FontWeight.SemiBold,
        modifier = Modifier.padding(top = 4.dp, bottom = 4.dp),
    )
}

// ──────────────────────────────────────────────────────────────
// 属性展示 helper(选规格逻辑见 domain/SkuMatching.kt,与规格选择卡共用)
// ──────────────────────────────────────────────────────────────
private fun primToList(el: JsonElement): List<String>? = when (el) {
    is JsonArray -> el.mapNotNull { (it as? JsonPrimitive)?.contentOrNull }
    is JsonPrimitive -> el.contentOrNull?.let { listOf(it) }
    else -> null
}
