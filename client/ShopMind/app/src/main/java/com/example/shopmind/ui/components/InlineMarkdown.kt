package com.example.shopmind.ui.components

import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.withStyle

/**
 * 把模型输出里的 `**加粗**` 渲染成粗体 —— **只认这一种**语法,其余符号原样保留。
 *
 * 为什么只做加粗:Agent 回复是 ≤150 字口语短句,不会出现标题/表格/列表;强调哪里由模型
 * 自己决定(它会主动打 `**`),客户端只忠实渲染,不替它判断该不该加粗(大厂做法)。
 *
 * 流式容错:按 `**` 切段,奇数段加粗、偶数段普通。逐字出字时若只到了开头的 `**`、配对的
 * 还没来 —— 最后一段落在奇数位 → 直接乐观加粗,**不会**先闪一下字面 `**` 再变粗,也不会崩。
 */
fun parseInlineBold(text: String): AnnotatedString {
    if (!text.contains("**")) return AnnotatedString(text)
    return buildAnnotatedString {
        val parts = text.split("**")
        parts.forEachIndexed { index, part ->
            if (part.isEmpty()) return@forEachIndexed
            if (index % 2 == 1) {
                withStyle(SpanStyle(fontWeight = FontWeight.Bold)) { append(part) }
            } else {
                append(part)
            }
        }
    }
}
