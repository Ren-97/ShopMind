package com.example.shopmind.ui.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

/**
 * 一个偏好类别的元数据。
 *
 * - [isColumn]:写到 profile 一级列(目前只有 consumption_tier),其余写 preferences JSON。
 * - [isList]:多值(list,多选 + 追加合并)还是单值(scalar,单选覆盖)—— 跟后端
 *   preference.py 的字段类型保持一致,否则写回类型不符。
 */
data class PrefCategory(
    val key: String,
    val label: String,
    val isList: Boolean,
    val isColumn: Boolean,
    val options: List<String>,
)

/**
 * 预设偏好类别 + 候选词。值对齐种子数据 / 商品属性词表,**选了才能真正驱动 reranker
 * 个性化**(llm_reranker.py 的 fit-boost 按这些值匹配商品属性)。
 * 品牌候选不在此(开放词表)—— 运行时从 GET /catalog/facets 注入。
 */
fun defaultPrefCategories(brands: List<String>): List<PrefCategory> = listOf(
    PrefCategory("consumption_tier", "消费档位", isList = false, isColumn = true,
        options = listOf("节约型", "中等", "高消费")),
    PrefCategory("skin_type", "肤质", isList = false, isColumn = false,
        options = listOf("干性", "油性", "混合性", "敏感肌", "中性")),
    PrefCategory("skin_concerns", "护肤需求", isList = true, isColumn = false,
        options = listOf("保湿", "美白", "抗老", "控油", "舒缓", "祛痘", "紧致", "修复")),
    PrefCategory("fragrance_pref", "香味", isList = false, isColumn = false,
        options = listOf("无香", "淡香", "浓香")),
    PrefCategory("usage", "常用场景", isList = true, isColumn = false,
        options = listOf("办公", "摄影", "出差", "游戏", "运动", "日常")),
    PrefCategory("os_pref", "系统", isList = false, isColumn = false,
        options = listOf("iOS", "Android")),
    PrefCategory("clothing_size", "衣服尺码", isList = false, isColumn = false,
        options = listOf("S", "M", "L", "XL", "XXL")),
    PrefCategory("shoe_size", "鞋码", isList = false, isColumn = false,
        options = listOf("38", "39", "40", "41", "42", "43", "44", "45")),
    PrefCategory("style_pref", "风格", isList = true, isColumn = false,
        options = listOf("简约", "运动", "商务", "休闲", "潮流", "复古")),
    PrefCategory("dietary_restrictions", "饮食", isList = true, isColumn = false,
        options = listOf("素食", "低糖", "低脂", "无麸质", "清真")),
    PrefCategory("brand_prefer", "喜欢的品牌", isList = true, isColumn = false,
        options = brands),
    PrefCategory("brand_exclude", "不想要的品牌", isList = true, isColumn = false,
        options = brands),
)

/**
 * 添加偏好弹窗:先选类别 → 预设 tag 点选(多值多选 / 单值单选)+「自定义」手动输入。
 * 确认回调 [onConfirm] 把选中类别 + 值交给调用方,由它做与现有偏好的合并 + PATCH /profile。
 */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun AddPreferenceDialog(
    categories: List<PrefCategory>,
    onConfirm: (category: PrefCategory, values: List<String>) -> Unit,
    onDismiss: () -> Unit,
) {
    var selectedCat by remember { mutableStateOf<PrefCategory?>(null) }
    // 当前类别的已选值;自定义输入的值临时并进候选列表,这样也以 chip 形态展示
    val picked = remember { mutableStateListOf<String>() }
    val extraOptions = remember { mutableStateListOf<String>() }
    var customText by remember { mutableStateOf("") }

    fun resetForCategory(cat: PrefCategory) {
        selectedCat = cat
        picked.clear()
        extraOptions.clear()
        customText = ""
    }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("添加偏好") },
        text = {
            Column(
                modifier = Modifier.verticalScroll(rememberScrollState()),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text(
                    "选个类别",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                FlowRow(
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                    verticalArrangement = Arrangement.spacedBy(4.dp),
                ) {
                    categories.forEach { cat ->
                        FilterChip(
                            selected = selectedCat?.key == cat.key,
                            onClick = { resetForCategory(cat) },
                            label = { Text(cat.label) },
                        )
                    }
                }

                val cat = selectedCat
                if (cat != null) {
                    HorizontalDivider()
                    Text(
                        if (cat.isList) "${cat.label}(可多选)" else "${cat.label}(单选)",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    val allOptions = cat.options + extraOptions.filterNot { it in cat.options }
                    if (allOptions.isEmpty()) {
                        Text(
                            "暂无预设,用下方自定义添加~",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    FlowRow(
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                        verticalArrangement = Arrangement.spacedBy(4.dp),
                    ) {
                        allOptions.forEach { opt ->
                            FilterChip(
                                selected = opt in picked,
                                onClick = {
                                    if (opt in picked) {
                                        picked.remove(opt)
                                    } else {
                                        if (!cat.isList) picked.clear()  // 单值:互斥
                                        picked.add(opt)
                                    }
                                },
                                label = { Text(opt) },
                            )
                        }
                    }
                    // 自定义输入:加进候选 + 选中(单值则覆盖)
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        OutlinedTextField(
                            value = customText,
                            onValueChange = { customText = it },
                            label = { Text("自定义") },
                            singleLine = true,
                            modifier = Modifier.weight(1f),
                        )
                        TextButton(
                            onClick = {
                                val v = customText.trim()
                                if (v.isNotEmpty()) {
                                    if (v !in cat.options && v !in extraOptions) extraOptions.add(v)
                                    if (!cat.isList) picked.clear()
                                    if (v !in picked) picked.add(v)
                                    customText = ""
                                }
                            },
                            enabled = customText.isNotBlank(),
                        ) { Text("添加") }
                    }
                }
            }
        },
        confirmButton = {
            TextButton(
                onClick = { selectedCat?.let { onConfirm(it, picked.toList()) } },
                enabled = selectedCat != null && picked.isNotEmpty(),
            ) { Text("确定") }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("取消") }
        },
    )
}
