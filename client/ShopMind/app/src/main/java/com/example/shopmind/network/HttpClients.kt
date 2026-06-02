package com.example.shopmind.network

import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import java.util.concurrent.TimeUnit

/**
 * OkHttp + Json 进程单例。
 *
 * - `currentUserId` 顶栏切 user 时更新,interceptor 通过 callback 读最新值
 * - 同一 OkHttpClient 给 SSE 和 REST 共用 — readTimeout=0(SSE 长连不超时;
 *   REST 靠 connect / write timeout 防卡死)
 * - Json 配置:`ignoreUnknownKeys`(后端 schema 演化前端不崩) + `explicitNulls=false`
 *   (后端可选字段缺失 → 用默认值而非报错)
 */
object HttpClients {

    @Volatile
    private var currentUserId: String = ApiConfig.DEFAULT_USER_ID

    fun setCurrentUser(userId: String) {
        currentUserId = userId
    }

    fun currentUser(): String = currentUserId

    val json: Json = Json {
        ignoreUnknownKeys = true
        encodeDefaults = true
        explicitNulls = false
        prettyPrint = false
    }

    val okhttp: OkHttpClient by lazy {
        OkHttpClient.Builder()
            .addInterceptor(UserIdInterceptor { currentUserId })
            .addInterceptor(
                HttpLoggingInterceptor().apply { level = HttpLoggingInterceptor.Level.BASIC }
            )
            .connectTimeout(15, TimeUnit.SECONDS)
            .writeTimeout(30, TimeUnit.SECONDS)
            .readTimeout(0, TimeUnit.MILLISECONDS) // SSE 长连
            .build()
    }
}
