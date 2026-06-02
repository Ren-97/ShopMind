package com.example.shopmind.network

import okhttp3.Interceptor
import okhttp3.Response

/**
 * 自动在每个请求上挂 `X-User-Id` header(§4.6.8 客户端注入路径)。
 *
 * 用 callback 拿 user_id(而不是构造时传死)— 顶栏切 user 时无需重建 OkHttpClient。
 */
class UserIdInterceptor(
    private val userIdProvider: () -> String,
) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val original = chain.request()
        val withUser = original.newBuilder()
            .header("X-User-Id", userIdProvider())
            .build()
        return chain.proceed(withUser)
    }
}
