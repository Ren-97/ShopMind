package com.example.shopmind.ui.nav

object Routes {
    const val CHAT = "chat"
    const val CART = "cart"
    // 整车下单(聊天 CheckoutCard 路径);勾选下单走 checkout(skuIds) 带 query 参数
    const val CHECKOUT = "checkout"
    const val CHECKOUT_ARG = "skuIds"
    const val CHECKOUT_ROUTE = "checkout?$CHECKOUT_ARG={$CHECKOUT_ARG}"
    const val PRODUCT_ARG = "productId"
    const val PRODUCT_DETAIL = "product/{$PRODUCT_ARG}"

    fun productDetail(productId: String) = "product/$productId"

    /** 勾选下单:把选中的 sku_id 逗号拼进 query;空 = 整车,退回 [CHECKOUT]。 */
    fun checkout(skuIds: List<String>): String =
        if (skuIds.isEmpty()) CHECKOUT else "checkout?$CHECKOUT_ARG=${skuIds.joinToString(",")}"
}
