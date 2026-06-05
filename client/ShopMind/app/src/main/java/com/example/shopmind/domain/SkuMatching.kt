package com.example.shopmind.domain

import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull

/**
 * SKU 选规格的纯逻辑,详情页(ProductDetailScreen)与聊天内嵌的规格选择卡(SkuSelectorCard)共用。
 *
 * 与后端 `_serializers.compute_sku_dimensions` 同口径:只保留 SKU 间有 >=2 个不同值的维度。
 * 选 SKU = 每维选一个值 → 在 skus 里唯一命中一条 → 取其 sku_id,全程不经过 LLM。
 */

/** 从一组 SKU 的 properties 里提取"差异化维度"(>=2 个不同值的 key),保序。 */
fun computeSkuDimensions(skuProps: List<Map<String, JsonElement>>): Map<String, List<String>> {
    val keyToValues = linkedMapOf<String, MutableList<String>>()
    for (props in skuProps) {
        for ((k, v) in props) {
            val s = (v as? JsonPrimitive)?.contentOrNull ?: continue
            val bucket = keyToValues.getOrPut(k) { mutableListOf() }
            if (s !in bucket) bucket.add(s)
        }
    }
    return keyToValues.filter { it.value.size >= 2 }
}

/**
 * 在 skus 里找与已选维度值完全匹配的那条。维度未选满返回 null;无差异化维度则取第一条。
 *
 * [properties] 抽取器让本函数对任意 SKU 类型(SkuDetail / SkuOption)通用。
 */
fun <T> findMatchingSku(
    skus: List<T>,
    selected: Map<String, String>,
    dimensionKeys: Set<String>,
    properties: (T) -> Map<String, JsonElement>,
): T? {
    if (dimensionKeys.isEmpty()) return skus.firstOrNull()
    if (!dimensionKeys.all { selected[it]?.isNotEmpty() == true }) return null
    return skus.firstOrNull { sku ->
        dimensionKeys.all { key ->
            (properties(sku)[key] as? JsonPrimitive)?.contentOrNull == selected[key]
        }
    }
}
