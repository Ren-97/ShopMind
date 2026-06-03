package com.example.shopmind.network

import android.content.Context
import java.util.UUID

/**
 * Per-user 持久化 session_id(SharedPreferences)。
 *
 * 单 session 心智下,每个 user 终生持有同一个 session_id;清空对话(🔄)只删 DB
 * 数据,session_id 复用。
 *
 * 切 user 时:新 user 各自一个 session_id,互不影响。
 */
object SessionStore {
    private const val PREFS_NAME = "shopmind_prefs"
    private const val KEY_PREFIX = "session_id_"

    /** 取该 user 的 session_id;首次访问自动生成并落盘。 */
    fun getOrCreate(context: Context, userId: String): String {
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        val existing = prefs.getString(KEY_PREFIX + userId, null)
        if (existing != null) return existing
        val fresh = newSessionId()
        prefs.edit().putString(KEY_PREFIX + userId, fresh).apply()
        return fresh
    }

    private fun newSessionId(): String =
        "sess-" + UUID.randomUUID().toString().replace("-", "").take(12)
}
