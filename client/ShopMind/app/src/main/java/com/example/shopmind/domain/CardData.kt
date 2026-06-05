package com.example.shopmind.domain

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * SSE `event: card` 的 5 种 card.type 解析后产物(§4.7.4-5)。
 *
 * 后端推的原始 wire 是 `{"type": "<kind>", "data": {...}}`,按 type 分发到具体子类。
 */
sealed class CardData {

    data class Product(val data: ProductCardData) : CardData()
    data class CompareTable(val data: CompareTableData) : CardData()
    data class Cart(val data: CartCardData) : CardData()
    data class Checkout(val data: CheckoutCardData) : CardData()
    data class Order(val data: OrderCardData) : CardData()
    data class SkuSelector(val data: SkuSelectorCardData) : CardData()

    /** 未识别 type — 保留原始 JSON 给 debug,不阻塞渲染。 */
    data class Unknown(val rawType: String, val rawJson: JsonElement) : CardData()

    companion object {
        fun parse(json: Json, root: JsonElement): CardData {
            val obj = root as? JsonObject ?: return Unknown("", root)
            val type = (obj["type"] as? JsonPrimitive)?.content.orEmpty()
            val data = obj["data"] ?: return Unknown(type, obj)
            return when (type) {
                "product" -> Product(json.decodeFromJsonElement(ProductCardData.serializer(), data))
                "compare_table" -> CompareTable(json.decodeFromJsonElement(CompareTableData.serializer(), data))
                "cart" -> Cart(json.decodeFromJsonElement(CartCardData.serializer(), data))
                "checkout" -> Checkout(json.decodeFromJsonElement(CheckoutCardData.serializer(), data))
                "order" -> Order(json.decodeFromJsonElement(OrderCardData.serializer(), data))
                "sku_selector" -> SkuSelector(json.decodeFromJsonElement(SkuSelectorCardData.serializer(), data))
                else -> Unknown(type, obj)
            }
        }
    }
}

// ──────────────────────────────────────────────────────────────
// 各 card 的 data schema(§4.7.4 + §4.7.5)
// ──────────────────────────────────────────────────────────────

@Serializable
data class ProductCardData(
    @SerialName("product_id") val productId: String,
    val title: String,
    val brand: String,
    @SerialName("image_url") val imageUrl: String? = null,
    @SerialName("base_price") val basePrice: Double,
    @SerialName("default_sku_id") val defaultSkuId: String? = null,
    @SerialName("sku_count") val skuCount: Int = 0,
    @SerialName("tags_candidates") val tagsCandidates: List<String> = emptyList(),
    @SerialName("in_stock") val inStock: Boolean = true,
)

@Serializable
data class CompareTableData(
    val headers: List<CompareHeader>,
    val rows: List<CompareRow>,
)

@Serializable
data class CartCardData(
    val items: List<CartItemData>,
    @SerialName("total_price") val totalPrice: Double,
    @SerialName("item_count") val itemCount: Int,
)

@Serializable
data class CheckoutCardData(
    val items: List<CheckoutItemData>,
    val address: String,
    @SerialName("recipient_name") val recipientName: String? = null,
    val phone: String? = null,
    @SerialName("total_price") val totalPrice: Double,
    @SerialName("item_count") val itemCount: Int,
)

@Serializable
data class SkuSelectorCardData(
    @SerialName("product_id") val productId: String,
    val title: String,
    @SerialName("image_url") val imageUrl: String? = null,
    @SerialName("base_price") val basePrice: Double,
    @SerialName("in_stock") val inStock: Boolean = true,
    val dimensions: Map<String, List<String>> = emptyMap(),
    val skus: List<SkuOption> = emptyList(),
)

@Serializable
data class SkuOption(
    @SerialName("sku_id") val skuId: String,
    val properties: Map<String, JsonElement> = emptyMap(),
    val price: Double,
)

@Serializable
data class OrderCardData(
    @SerialName("order_id") val orderId: String,
    val status: String,
    val items: List<OrderItemData>,
    val address: String,
    @SerialName("recipient_name") val recipientName: String? = null,
    val phone: String? = null,
    @SerialName("total_price") val totalPrice: Double,
    @SerialName("created_at") val createdAt: String,
)
