package com.example.shopmind.network

import com.example.shopmind.domain.CardData
import com.example.shopmind.domain.CartCardData
import com.example.shopmind.domain.HistoryMessage
import com.example.shopmind.domain.OrderCardData
import com.example.shopmind.domain.PlaceOrderRequest
import com.example.shopmind.domain.ProductDetail
import com.example.shopmind.domain.ProfileResponse
import com.example.shopmind.domain.UserListItem
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.put
import okhttp3.Call
import okhttp3.Callback
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import java.io.IOException
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/**
 * REST 端点 typed wrappers — 对应 server/api/{cart,product,order,profile}.py。
 *
 * `/cart` 系列 + POST/GET `/order/{id}` 后端返回的是 card wrapper `{type, data}`;
 * 本类内部 unwrap 后只暴露 data 类型,调用方拿到的就是 [CartCardData] / [OrderCardData] 等。
 */
class RestApi(
    private val client: OkHttpClient = HttpClients.okhttp,
    private val json: Json = HttpClients.json,
) {

    private val jsonMediaType = "application/json".toMediaType()

    // ──────────────────────────────────────────────────────────
    // Cart(返回 cart card wrapper,内部 unwrap)
    // ──────────────────────────────────────────────────────────
    suspend fun getCart(): CartCardData = unwrapCartCard(get("/cart"))

    suspend fun addToCart(skuId: String, qty: Int = 1): CartCardData {
        val body = buildJsonObject {
            put("sku_id", skuId)
            put("qty", qty)
        }
        return unwrapCartCard(post("/cart", body.toString()))
    }

    suspend fun updateCartQty(skuId: String, qty: Int): CartCardData {
        val body = buildJsonObject { put("qty", qty) }
        return unwrapCartCard(patch("/cart/$skuId", body.toString()))
    }

    suspend fun removeFromCart(skuId: String): CartCardData =
        unwrapCartCard(delete("/cart/$skuId"))

    suspend fun clearCart(): CartCardData = unwrapCartCard(delete("/cart"))

    // ──────────────────────────────────────────────────────────
    // Product
    // ──────────────────────────────────────────────────────────
    suspend fun getProduct(productId: String): ProductDetail =
        json.decodeFromString(ProductDetail.serializer(), get("/product/$productId"))

    // ──────────────────────────────────────────────────────────
    // Order
    // ──────────────────────────────────────────────────────────
    suspend fun placeOrder(req: PlaceOrderRequest = PlaceOrderRequest()): OrderCardData {
        val body = json.encodeToString(PlaceOrderRequest.serializer(), req)
        return unwrapOrderCard(post("/order", body))
    }

    suspend fun listOrders(): List<OrderCardData> {
        val raw = get("/order")
        val obj = json.parseToJsonElement(raw).jsonObject
        val arr = obj["orders"]?.jsonArray ?: return emptyList()
        return arr.map { el ->
            val card = CardData.parse(json, el)
            (card as? CardData.Order)?.data
                ?: throw IOException("orders list 含非 order card: $el")
        }
    }

    suspend fun getOrder(orderId: String): OrderCardData =
        unwrapOrderCard(get("/order/$orderId"))

    // ──────────────────────────────────────────────────────────
    // Profile / Users
    // ──────────────────────────────────────────────────────────
    suspend fun getProfile(): ProfileResponse =
        json.decodeFromString(ProfileResponse.serializer(), get("/profile"))

    suspend fun patchProfile(patchBody: String): ProfileResponse =
        json.decodeFromString(ProfileResponse.serializer(), patch("/profile", patchBody))

    suspend fun listUsers(): List<UserListItem> =
        json.decodeFromString(ListSerializer(UserListItem.serializer()), get("/users"))

    suspend fun createUser(displayName: String): UserListItem {
        val body = buildJsonObject { put("display_name", displayName) }
        return json.decodeFromString(UserListItem.serializer(), post("/users", body.toString()))
    }

    // ──────────────────────────────────────────────────────────
    // Chat history(B+)
    // ──────────────────────────────────────────────────────────
    suspend fun getHistory(): List<HistoryMessage> {
        val raw = get("/chat/history")
        val obj = json.parseToJsonElement(raw).jsonObject
        val arr = obj["messages"]?.jsonArray ?: return emptyList()
        return arr.map { json.decodeFromJsonElement(HistoryMessage.serializer(), it) }
    }

    /** 🔄 清空对话 — DELETE /chat/history。返回 server 报告的删除行数。 */
    suspend fun clearHistory(): Int {
        val raw = delete("/chat/history")
        val obj = json.parseToJsonElement(raw).jsonObject
        return (obj["deleted"]?.toString()?.toIntOrNull()) ?: 0
    }

    // ──────────────────────────────────────────────────────────
    // 共享:HTTP verbs + unwrap
    // ──────────────────────────────────────────────────────────
    private suspend fun get(path: String): String = execute(
        Request.Builder().url("${ApiConfig.BASE_URL}$path").get().build()
    )

    private suspend fun post(path: String, body: String): String = execute(
        Request.Builder()
            .url("${ApiConfig.BASE_URL}$path")
            .post(body.toRequestBody(jsonMediaType))
            .build()
    )

    private suspend fun patch(path: String, body: String): String = execute(
        Request.Builder()
            .url("${ApiConfig.BASE_URL}$path")
            .patch(body.toRequestBody(jsonMediaType))
            .build()
    )

    private suspend fun delete(path: String): String = execute(
        Request.Builder().url("${ApiConfig.BASE_URL}$path").delete().build()
    )

    private suspend fun execute(request: Request): String = withContext(Dispatchers.IO) {
        val response = client.executeAsync(request)
        response.use { resp ->
            val bodyText = resp.body?.string().orEmpty()
            if (!resp.isSuccessful) {
                throw HttpException(
                    code = resp.code,
                    message = extractDetail(bodyText) ?: resp.message,
                )
            }
            bodyText
        }
    }

    private fun extractDetail(body: String): String? = try {
        val obj = json.parseToJsonElement(body).jsonObject
        obj["detail"]?.toString()?.trim('"')
    } catch (_: Exception) {
        null
    }

    private fun unwrapCartCard(raw: String): CartCardData {
        val element = json.parseToJsonElement(raw)
        val card = CardData.parse(json, element)
        return (card as? CardData.Cart)?.data
            ?: throw IOException("期望 cart card,实际拿到: $raw")
    }

    private fun unwrapOrderCard(raw: String): OrderCardData {
        val element = json.parseToJsonElement(raw)
        val card = CardData.parse(json, element)
        return (card as? CardData.Order)?.data
            ?: throw IOException("期望 order card,实际拿到: $raw")
    }
}

/** Suspended OkHttp call(取消 → cancel HTTP request)。 */
private suspend fun OkHttpClient.executeAsync(request: Request): Response =
    suspendCancellableCoroutine { cont ->
        val call = newCall(request)
        cont.invokeOnCancellation { call.cancel() }
        call.enqueue(object : Callback {
            override fun onResponse(call: Call, response: Response) {
                cont.resume(response)
            }

            override fun onFailure(call: Call, e: IOException) {
                cont.resumeWithException(e)
            }
        })
    }

/** REST 端点返回的非 2xx 状态码 → 抛 [HttpException],UI 层兜底显示 detail。 */
class HttpException(val code: Int, message: String) : IOException("HTTP $code: $message")
