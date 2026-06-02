package com.example.shopmind.network

import com.example.shopmind.domain.AgentEvent
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources

/**
 * POST /chat 的 SSE 包装(§4.7)。
 *
 * 用 callbackFlow 把 OkHttp EventSource 桥到 Kotlin Flow:
 * - 每条 SSE event → `AgentEvent.parse()` → emit
 * - 失败 → 推一条 [AgentEvent.ErrorEvent] 再关流(消费方能感知)
 * - 协程取消 / Flow collector 退出 → `eventSource.cancel()` 主动关连接
 */
class SseClient(
    private val client: OkHttpClient = HttpClients.okhttp,
    private val json: Json = HttpClients.json,
) {

    /**
     * 启动一次 chat 请求。Flow 是冷流 — collect 才发请求,collect 退出主动断开。
     *
     * @param query 用户输入
     * @param sessionId 客户端持有的 session UUID;null 让后端生成
     */
    fun chat(query: String, sessionId: String?): Flow<AgentEvent> = callbackFlow {
        val bodyJson = buildJsonObject {
            put("query", query)
            if (sessionId != null) put("session_id", sessionId)
        }
        val body = bodyJson.toString().toRequestBody("application/json".toMediaType())
        val request = Request.Builder()
            .url("${ApiConfig.BASE_URL}/chat")
            .post(body)
            .header("Accept", "text/event-stream")
            .build()

        val factory = EventSources.createFactory(client)
        val eventSource = factory.newEventSource(
            request,
            object : EventSourceListener() {
                override fun onEvent(
                    eventSource: EventSource,
                    id: String?,
                    type: String?,
                    data: String,
                ) {
                    val parsed = AgentEvent.parse(json, type.orEmpty(), data)
                    trySend(parsed)
                }

                override fun onFailure(
                    eventSource: EventSource,
                    t: Throwable?,
                    response: Response?,
                ) {
                    val msg = t?.message
                        ?: response?.message
                        ?: "SSE 连接失败(HTTP ${response?.code ?: "?"})"
                    trySend(AgentEvent.ErrorEvent("network_error", msg))
                    close(t)
                }

                override fun onClosed(eventSource: EventSource) {
                    close()
                }
            },
        )

        awaitClose { eventSource.cancel() }
    }
}
