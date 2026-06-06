package com.example.shopmind.ui.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import coil3.compose.AsyncImage
import com.example.shopmind.domain.SkuOption
import com.example.shopmind.domain.SkuSelectorCardData
import com.example.shopmind.domain.findMatchingSku

/**
 * 聊天内嵌的规格选择卡(形态 B):多规格商品加购时,用户原地点选规格 → 直接加购。
 *
 * 选 SKU 逻辑复用 [findMatchingSku] —— 每维点一个值,选满即本地反查出唯一 sku_id,
 * 全程不经过 LLM。点 [加入购物车] 回调 [onAddSku](带选定数量),由 ViewModel 走 POST /cart。
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun SkuSelectorCard(
    data: SkuSelectorCardData,
    onAddSku: (skuId: String, title: String, qty: Int) -> Unit,
    modifier: Modifier = Modifier,
) {
    val selected = remember(data.productId) { mutableStateMapOf<String, String>() }
    var qty by remember(data.productId) { mutableStateOf(1) }
    val matchedSku: SkuOption? = remember(data, selected.toMap()) {
        findMatchingSku(data.skus, selected, data.dimensions.keys) { it.properties }
    }
    val canAdd = matchedSku != null && data.inStock

    Card(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(12.dp),
    ) {
        Column(
            modifier = Modifier.padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Row(
                horizontalArrangement = Arrangement.spacedBy(10.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                if (data.imageUrl != null) {
                    AsyncImage(
                        model = data.imageUrl,
                        contentDescription = data.title,
                        modifier = Modifier
                            .size(48.dp)
                            .clip(RoundedCornerShape(8.dp)),
                    )
                }
                Text(
                    data.title,
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.SemiBold,
                )
            }

            data.dimensions.forEach { (key, values) ->
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
                            onClick = {
                                if (selected[key] == v) selected.remove(key) else selected[key] = v
                            },
                            label = { Text(v) },
                        )
                    }
                }
            }

            val price = matchedSku?.price ?: data.basePrice
            Row(
                horizontalArrangement = Arrangement.spacedBy(10.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                QuantityStepper(
                    qty = qty,
                    onDecrease = { qty = (qty - 1).coerceAtLeast(1) },
                    onIncrease = { qty += 1 },
                    enabled = data.inStock,
                )
                Button(
                    onClick = { matchedSku?.let { onAddSku(it.skuId, data.title, qty) } },
                    enabled = canAdd,
                    modifier = Modifier.weight(1f),
                ) {
                    Text(
                        when {
                            !data.inStock -> "暂时缺货"
                            matchedSku == null -> "请选规格"
                            else -> "加入购物车 · ¥${"%.0f".format(price * qty)}"
                        }
                    )
                }
            }
        }
    }
}
