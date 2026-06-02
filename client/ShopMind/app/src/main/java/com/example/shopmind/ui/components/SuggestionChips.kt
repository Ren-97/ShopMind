package com.example.shopmind.ui.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.example.shopmind.domain.SuggestionItem

/**
 * Follow-up suggestions chips(§4.6.11)。
 *
 * Material 3 `FilterChip` 横排(FlowRow 自适应)。点击 → 调 `onSelect(query)`,
 * 由调用方决定:
 *   - 普通 chip → 调 ViewModel.sendMessage(query)
 *   - "去结算"等导航类 → ChatScreen 那一层识别后跳路由(V1 默认都当消息发)
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun SuggestionChips(
    items: List<SuggestionItem>,
    onSelect: (SuggestionItem) -> Unit,
    modifier: Modifier = Modifier,
) {
    if (items.isEmpty()) return
    FlowRow(
        modifier = modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp),
        horizontalArrangement = Arrangement.spacedBy(6.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        items.forEach { item ->
            FilterChip(
                selected = false,
                onClick = { onSelect(item) },
                label = {
                    Text(
                        item.label,
                        style = MaterialTheme.typography.labelMedium,
                    )
                },
            )
        }
    }
}
