package com.example.shopmind.domain

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

/**
 * REST 端点返回数据类(对齐 server/api/ 各路由)。
 *
 * `/cart` / POST `/order` 等返回的是 lean card 同 schema,直接复用 [CardData];
 * 这里只放纯 REST 独有的:`/product/{id}` 详情、`/profile`、`/users`。
 */

@Serializable
data class ProductDetail(
    @SerialName("product_id") val productId: String,
    val title: String,
    val brand: String,
    val category: String? = null,
    @SerialName("sub_category") val subCategory: String? = null,
    @SerialName("base_price") val basePrice: Double,
    @SerialName("image_url") val imageUrl: String? = null,
    @SerialName("marketing_description") val marketingDescription: String? = null,
    @SerialName("in_stock") val inStock: Boolean = true,
    @SerialName("is_active") val isActive: Boolean = true,
    val properties: Map<String, JsonElement> = emptyMap(),
    val skus: List<SkuDetail> = emptyList(),
    val faqs: List<FaqDetail> = emptyList(),
    val reviews: List<ReviewDetail> = emptyList(),
    val caveats: String? = null,
)

@Serializable
data class SkuDetail(
    @SerialName("sku_id") val skuId: String,
    val properties: Map<String, JsonElement> = emptyMap(),
    val price: Double,
)

@Serializable
data class FaqDetail(
    val question: String,
    val answer: String,
    @SerialName("order_idx") val orderIdx: Int = 0,
)

@Serializable
data class ReviewDetail(
    @SerialName("review_id") val reviewId: Long,
    val nickname: String? = null,
    val rating: Int? = null,
    val content: String,
    /** 情感分 ∈ [-1.0, 1.0],离线 LLM 抽出;负数 = 负面评价。 */
    val sentiment: Double? = null,
    val aspects: List<String> = emptyList(),
)

@Serializable
data class ProfileResponse(
    @SerialName("user_id") val userId: String,
    val age: Int? = null,
    val gender: String? = null,
    @SerialName("height_cm") val heightCm: Double? = null,
    @SerialName("weight_kg") val weightKg: Double? = null,
    @SerialName("consumption_tier") val consumptionTier: String? = null,
    @SerialName("recipient_name") val recipientName: String? = null,
    val phone: String? = null,
    val address: String? = null,
    val preferences: Map<String, JsonElement> = emptyMap(),
)

/**
 * GET /catalog/facets —— 库存感知的 chip 候选源。
 * 每个 category 是库里"在售有货"商品聚合出的真实词表,starter chip 只从这里填。
 */
@Serializable
data class CatalogFacets(
    val categories: List<CategoryFacet> = emptyList(),
)

@Serializable
data class CategoryFacet(
    val category: String,
    @SerialName("sub_categories") val subCategories: List<String> = emptyList(),
    val brands: List<String> = emptyList(),
    @SerialName("price_min") val priceMin: Double = 0.0,
    @SerialName("price_max") val priceMax: Double = 0.0,
    @SerialName("product_count") val productCount: Int = 0,
)

@Serializable
data class UserListItem(
    @SerialName("user_id") val userId: String,
    @SerialName("display_name") val displayName: String,
)

@Serializable
data class PlaceOrderRequest(
    val address: String? = null,
    @SerialName("recipient_name") val recipientName: String? = null,
    val phone: String? = null,
    // 只下单这些 sku_id(勾选下单);null = 整车下单
    @SerialName("sku_ids") val skuIds: List<String>? = null,
)

// ──────────────────────────────────────────────────────────────
// B+ 对话历史(GET /chat/history)
// 后端只存引用,客户端拿 product_id / order_id 实时拉最新数据
// ──────────────────────────────────────────────────────────────

@Serializable
data class HistoryCardRefs(
    val products: List<String>? = null,
    val compare: List<String>? = null,
    val order: String? = null,
)

@Serializable
data class HistoryMessage(
    @SerialName("msg_id") val msgId: Long,
    @SerialName("session_id") val sessionId: String,
    val role: String,
    val content: String,
    @SerialName("card_refs") val cardRefs: HistoryCardRefs? = null,
    @SerialName("created_at") val createdAt: String? = null,
)
