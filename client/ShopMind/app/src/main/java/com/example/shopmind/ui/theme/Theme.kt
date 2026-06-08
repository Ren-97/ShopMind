package com.example.shopmind.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable

// 暗金浅色主题(甲方案)。固定一套配色 —— 不跟随系统深色、不动态取色,
// 答辩演示机的壁纸 / 系统主题都不会影响品牌呈现。深色模式 V1 不做。
private val ShopMindLightColors = lightColorScheme(
    primary = GoldPrimary,
    onPrimary = GoldOnPrimary,
    primaryContainer = GoldPrimaryContainer,
    onPrimaryContainer = GoldOnPrimaryContainer,
    secondary = GoldSecondary,
    onSecondary = GoldOnSecondary,
    secondaryContainer = GoldSecondaryContainer,
    onSecondaryContainer = GoldOnSecondaryContainer,
    tertiary = GoldTertiary,
    onTertiary = GoldOnTertiary,
    tertiaryContainer = GoldTertiaryContainer,
    onTertiaryContainer = GoldOnTertiaryContainer,
    background = WarmBackground,
    onBackground = WarmOnBackground,
    surface = WarmSurface,
    onSurface = WarmOnSurface,
    surfaceVariant = WarmSurfaceVariant,
    onSurfaceVariant = WarmOnSurfaceVariant,
    outline = WarmOutline,
    outlineVariant = WarmOutlineVariant,
)

@Composable
fun ShopMindTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = ShopMindLightColors,
        typography = Typography,
        content = content,
    )
}
