package com.example.shopmind.ui.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.example.shopmind.domain.CardData
import com.example.shopmind.domain.ProductCardData

/**
 * 把一串 [CardData] 转成 Composable 流。
 *
 * 关键规则:**连续的 ProductCard 合并成一个 LazyRow 横排**(§4.7.6 决策)— 其他 card
 * 类型纵向独立渲染。
 */
@Composable
fun CardListRenderer(
    cards: List<CardData>,
    onProductClick: (String) -> Unit,
    onCartClick: () -> Unit,
    onCheckoutClick: () -> Unit,
    onSkuAdd: (skuId: String, title: String) -> Unit,
    modifier: Modifier = Modifier,
) {
    if (cards.isEmpty()) return
    val groups = remember(cards) { groupCards(cards) }
    Column(
        modifier = modifier,
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        groups.forEach { group ->
            when (group) {
                is CardGroup.Products -> ProductCardRow(group.items, onProductClick)
                is CardGroup.Single -> {
                    when (val c = group.card) {
                        is CardData.CompareTable -> CompareTableCard(c.data, onProductClick = onProductClick)
                        is CardData.Cart -> CartCard(c.data, onClickOpenCart = onCartClick)
                        is CardData.Checkout -> CheckoutCard(c.data, onClickGoCheckout = onCheckoutClick)
                        is CardData.Order -> OrderCard(c.data)
                        is CardData.SkuSelector -> SkuSelectorCard(c.data, onAddSku = onSkuAdd)
                        is CardData.Product -> ProductCardRow(listOf(c.data), onProductClick)
                        is CardData.Unknown -> Unit
                    }
                }
            }
        }
    }
}

/**
 * 卡片相对正文的位置:点评型(商品 / 对比 —— 文字在解读卡片)排在文字**上方**,
 * 指令 / 结论型(规格选择 / 结算 / 订单 —— 文字引导或陈述,卡片是它指向的东西)排在文字**下方**。
 */
fun CardData.rendersBelowText(): Boolean = when (this) {
    is CardData.SkuSelector, is CardData.Checkout, is CardData.Order -> true
    else -> false
}

@Composable
private fun ProductCardRow(items: List<ProductCardData>, onProductClick: (String) -> Unit) {
    LazyRow(
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        contentPadding = PaddingValues(horizontal = 2.dp),
    ) {
        items(items = items, key = { it.productId }) { item ->
            ProductCard(
                data = item,
                onClick = { onProductClick(item.productId) },
            )
        }
    }
}

// ──────────────────────────────────────────────────────────────
// Grouping
// ──────────────────────────────────────────────────────────────
private sealed class CardGroup {
    data class Products(val items: List<ProductCardData>) : CardGroup()
    data class Single(val card: CardData) : CardGroup()
}

private fun groupCards(cards: List<CardData>): List<CardGroup> {
    val out = mutableListOf<CardGroup>()
    var batch: MutableList<ProductCardData>? = null
    for (card in cards) {
        if (card is CardData.Product) {
            if (batch == null) batch = mutableListOf()
            batch.add(card.data)
        } else {
            if (batch != null) {
                out.add(CardGroup.Products(batch))
                batch = null
            }
            out.add(CardGroup.Single(card))
        }
    }
    if (batch != null) out.add(CardGroup.Products(batch))
    return out
}
