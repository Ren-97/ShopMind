package com.example.shopmind.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedCard
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import coil3.compose.AsyncImage
import com.example.shopmind.domain.CompareHeader
import com.example.shopmind.domain.CompareRow
import com.example.shopmind.domain.CompareTableData

/**
 * Compare 表(§4.7.5)。
 *
 * - headers 行:商品图 + 标题 + 价
 * - rows:`attr_label | values × N`
 * - highlight:winner=绿色 / warning=橙色,对应 [indices] 的 cell 加 tint
 *
 * 内容超过屏宽时整张表横向滚动(包括 headers 行,保持列对齐)。
 */
@Composable
fun CompareTableCard(
    data: CompareTableData,
    onProductClick: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val scrollState = rememberScrollState()
    val cellWidth = 120.dp
    val labelWidth = 84.dp

    OutlinedCard(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(12.dp),
    ) {
        Column(
            modifier = Modifier
                .horizontalScroll(scrollState)
                .padding(vertical = 8.dp),
        ) {
            CompareHeaderRow(
                headers = data.headers,
                labelWidth = labelWidth,
                cellWidth = cellWidth,
                onProductClick = onProductClick,
            )
            HorizontalDivider(modifier = Modifier.padding(vertical = 4.dp))
            data.rows.forEach { row ->
                CompareDataRow(
                    row = row,
                    labelWidth = labelWidth,
                    cellWidth = cellWidth,
                )
            }
        }
    }
}

@Composable
private fun CompareHeaderRow(
    headers: List<CompareHeader>,
    labelWidth: androidx.compose.ui.unit.Dp,
    cellWidth: androidx.compose.ui.unit.Dp,
    onProductClick: (String) -> Unit,
) {
    Row(
        modifier = Modifier.padding(horizontal = 8.dp),
        verticalAlignment = Alignment.Top,
    ) {
        Box(modifier = Modifier.width(labelWidth))
        headers.forEach { h ->
            Column(
                modifier = Modifier
                    .width(cellWidth)
                    .padding(horizontal = 4.dp)
                    .clip(RoundedCornerShape(6.dp))
                    .clickable { onProductClick(h.productId) },
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                AsyncImage(
                    model = h.imageUrl,
                    contentDescription = h.title,
                    modifier = Modifier
                        .height(72.dp)
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(6.dp)),
                )
                Text(
                    h.title,
                    style = MaterialTheme.typography.labelMedium,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                    textAlign = TextAlign.Center,
                )
                Text(
                    "¥${"%.0f".format(h.basePrice)}",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.primary,
                )
            }
        }
    }
}

@Composable
private fun CompareDataRow(
    row: CompareRow,
    labelWidth: androidx.compose.ui.unit.Dp,
    cellWidth: androidx.compose.ui.unit.Dp,
) {
    val winnerSet = remember(row) {
        if (row.highlight?.type == "winner") row.highlight.indices.toSet() else emptySet()
    }
    val warningSet = remember(row) {
        if (row.highlight?.type == "warning") row.highlight.indices.toSet() else emptySet()
    }

    Row(
        modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            row.attrLabel,
            style = MaterialTheme.typography.labelMedium,
            fontWeight = FontWeight.Medium,
            modifier = Modifier
                .width(labelWidth)
                .padding(end = 4.dp),
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        row.values.forEachIndexed { idx, value ->
            val cellBg = when {
                idx in winnerSet -> Color(0xFF4CAF50).copy(alpha = 0.15f)
                idx in warningSet -> Color(0xFFFF9800).copy(alpha = 0.15f)
                else -> Color.Transparent
            }
            val cellFg = when {
                idx in winnerSet -> Color(0xFF2E7D32)
                idx in warningSet -> Color(0xFFEF6C00)
                else -> MaterialTheme.colorScheme.onSurface
            }
            Box(
                modifier = Modifier
                    .width(cellWidth)
                    .padding(horizontal = 2.dp)
                    .clip(RoundedCornerShape(6.dp))
                    .background(cellBg),
            ) {
                Text(
                    value,
                    style = MaterialTheme.typography.bodySmall,
                    color = cellFg,
                    textAlign = TextAlign.Center,
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 4.dp, vertical = 6.dp),
                )
            }
        }
    }
}
