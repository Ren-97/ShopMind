package com.example.shopmind.ui.components

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Remove
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.material3.Text

/**
 * 加购数量步进器(− / 数字 / +)。下限 1,无上限 —— 库存由后端 add_to_cart 兜底。
 *
 * 详情页底栏与聊天规格选择卡共用:两处都是「确定 sku + 选数量 → 加购」同一动作。
 */
@Composable
fun QuantityStepper(
    qty: Int,
    onDecrease: () -> Unit,
    onIncrease: () -> Unit,
    enabled: Boolean,
    modifier: Modifier = Modifier,
) {
    Surface(
        modifier = modifier,
        shape = RoundedCornerShape(8.dp),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            IconButton(
                onClick = onDecrease,
                enabled = enabled && qty > 1,
                modifier = Modifier.size(36.dp),
            ) {
                Icon(Icons.Default.Remove, contentDescription = "减少", modifier = Modifier.size(18.dp))
            }
            Text(
                qty.toString(),
                style = MaterialTheme.typography.titleMedium,
                textAlign = TextAlign.Center,
                modifier = Modifier.widthIn(min = 28.dp),
            )
            IconButton(
                onClick = onIncrease,
                enabled = enabled,
                modifier = Modifier.size(36.dp),
            ) {
                Icon(Icons.Default.Add, contentDescription = "增加", modifier = Modifier.size(18.dp))
            }
        }
    }
}
