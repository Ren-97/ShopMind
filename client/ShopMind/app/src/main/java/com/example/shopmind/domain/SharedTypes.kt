package com.example.shopmind.domain

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * 与后端 SSE card / REST 响应跨模块复用的小数据类。
 *
 * 字段 @SerialName 对齐后端 snake_case;Kotlin 侧用 camelCase。
 */

@Serializable
data class CartItemData(
    @SerialName("sku_id") val skuId: String,
    @SerialName("product_id") val productId: String? = null,
    val title: String = "",
    @SerialName("image_url") val imageUrl: String? = null,
    val qty: Int,
    @SerialName("unit_price") val unitPrice: Double,
    val subtotal: Double,
    @SerialName("in_stock") val inStock: Boolean = true,
)

@Serializable
data class CheckoutItemData(
    @SerialName("sku_id") val skuId: String,
    @SerialName("product_id") val productId: String? = null,
    val title: String = "",
    @SerialName("image_url") val imageUrl: String? = null,
    val qty: Int,
    @SerialName("unit_price") val unitPrice: Double,
    val subtotal: Double,
)

@Serializable
data class OrderItemData(
    @SerialName("sku_id") val skuId: String,
    @SerialName("product_id") val productId: String? = null,
    val title: String = "",
    @SerialName("image_url") val imageUrl: String? = null,
    val qty: Int,
    @SerialName("unit_price") val unitPrice: Double,
    val subtotal: Double,
)

@Serializable
data class CompareHeader(
    @SerialName("product_id") val productId: String,
    val title: String,
    @SerialName("image_url") val imageUrl: String? = null,
    @SerialName("base_price") val basePrice: Double,
)

@Serializable
data class CompareHighlight(
    val type: String, // "winner" | "warning"
    val indices: List<Int>,
)

@Serializable
data class CompareRow(
    @SerialName("attr_label") val attrLabel: String,
    val values: List<String>,
    val highlight: CompareHighlight? = null,
)

@Serializable
data class SuggestionItem(
    val label: String,
    val query: String,
)
