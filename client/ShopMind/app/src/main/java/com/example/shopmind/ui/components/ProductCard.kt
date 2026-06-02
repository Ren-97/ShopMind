package com.example.shopmind.ui.components

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AssistChip
import androidx.compose.material3.AssistChipDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedCard
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import coil3.compose.AsyncImage
import com.example.shopmind.domain.ProductCardData

/**
 * Product 卡片(lean,§4.7.4)。
 *
 * 横向滑动场景:外层 LazyRow + 每张 ProductCard width=170dp。
 * 点击整张卡 → 跳 ProductDetailScreen 拉完整 detail。
 *
 * `tagsCandidates` 是后端给的候选池(不排序、不截断),前端用 FlowRow 自适应布局,
 * 放不下显示 "+N more"(§4.7.4 职责分工)。
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ProductCard(
    data: ProductCardData,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    OutlinedCard(
        modifier = modifier
            .width(170.dp)
            .clickable(onClick = onClick),
        shape = RoundedCornerShape(12.dp),
    ) {
        Column {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .aspectRatio(1f)
                    .clip(RoundedCornerShape(topStart = 12.dp, topEnd = 12.dp)),
            ) {
                AsyncImage(
                    model = data.imageUrl,
                    contentDescription = data.title,
                    modifier = Modifier.fillMaxWidth(),
                )
                if (!data.inStock) {
                    AssistChip(
                        onClick = {},
                        enabled = false,
                        label = { Text("缺货", style = MaterialTheme.typography.labelSmall) },
                        modifier = Modifier
                            .align(Alignment.TopEnd)
                            .padding(6.dp),
                        colors = AssistChipDefaults.assistChipColors(
                            disabledContainerColor = MaterialTheme.colorScheme.errorContainer,
                            disabledLabelColor = MaterialTheme.colorScheme.onErrorContainer,
                        ),
                    )
                }
            }
            Column(
                modifier = Modifier.padding(horizontal = 10.dp, vertical = 8.dp),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                Text(
                    data.brand,
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    data.title,
                    style = MaterialTheme.typography.bodyMedium,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    "¥${"%.0f".format(data.basePrice)}",
                    style = MaterialTheme.typography.titleMedium,
                    color = MaterialTheme.colorScheme.primary,
                )
                TagsFlowRow(tags = data.tagsCandidates, maxShown = 4)
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun TagsFlowRow(tags: List<String>, maxShown: Int) {
    if (tags.isEmpty()) return
    val shown = tags.take(maxShown)
    val remaining = (tags.size - shown.size).coerceAtLeast(0)
    FlowRow(
        horizontalArrangement = Arrangement.spacedBy(4.dp),
        verticalArrangement = Arrangement.spacedBy(4.dp),
        modifier = Modifier.fillMaxWidth(),
    ) {
        shown.forEach { tag ->
            TinyChip(text = tag)
        }
        if (remaining > 0) {
            TinyChip(text = "+$remaining", muted = true)
        }
    }
}

@Composable
private fun TinyChip(text: String, muted: Boolean = false) {
    val bg = if (muted) Color.Transparent else MaterialTheme.colorScheme.secondaryContainer
    val fg = if (muted) MaterialTheme.colorScheme.onSurfaceVariant
    else MaterialTheme.colorScheme.onSecondaryContainer
    Box(
        modifier = Modifier
            .clip(RoundedCornerShape(6.dp))
            .padding(0.dp),
    ) {
        Text(
            text,
            style = MaterialTheme.typography.labelSmall,
            color = fg,
            modifier = Modifier
                .clip(RoundedCornerShape(6.dp))
                .padding(horizontal = 6.dp, vertical = 2.dp),
        )
    }
}
