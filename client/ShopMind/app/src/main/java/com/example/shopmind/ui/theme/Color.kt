package com.example.shopmind.ui.theme

import androidx.compose.ui.graphics.Color

// ShopMind 品牌色:暗金(甲方案 —— 浅暖底 + 金色点缀)。锁浅色,不跟随系统、不动态取色。
// 颜色只出现在"强调元素 + 用户气泡":价格 / 发送键 / chip / 角标 / 用户气泡;背景与正文保持中性。

// ── 主色:暗金(按钮 / 价格 / 发送键 / 用户气泡描边)──
val GoldPrimary = Color(0xFF8A6A00)
val GoldOnPrimary = Color(0xFFFFFFFF)
val GoldPrimaryContainer = Color(0xFFFBE08A) // 浅金 —— 用户气泡填充
val GoldOnPrimaryContainer = Color(0xFF2A2000)

// ── 次色:暖灰金(chip / 次要容器)──
val GoldSecondary = Color(0xFF6D5D3E)
val GoldOnSecondary = Color(0xFFFFFFFF)
val GoldSecondaryContainer = Color(0xFFF6E3BB)
val GoldOnSecondaryContainer = Color(0xFF251A04)

// ── 第三色:一点克制的橄榄绿,作对比强调(可点缀,不喧宾)──
val GoldTertiary = Color(0xFF4E6543)
val GoldOnTertiary = Color(0xFFFFFFFF)
val GoldTertiaryContainer = Color(0xFFD0EBC0)
val GoldOnTertiaryContainer = Color(0xFF0C2006)

// ── 中性:浅暖白背景 + 近黑正文(不染色)──
val WarmBackground = Color(0xFFFFFBF2)
val WarmOnBackground = Color(0xFF1E1B13)
val WarmSurface = Color(0xFFFFFBF2)
val WarmOnSurface = Color(0xFF1E1B13)
val WarmSurfaceVariant = Color(0xFFECE1CD) // 卡片描边 / 分隔 / 浅容器
val WarmOnSurfaceVariant = Color(0xFF4B4639)
val WarmOutline = Color(0xFF7D7667)
val WarmOutlineVariant = Color(0xFFCFC6B4)
