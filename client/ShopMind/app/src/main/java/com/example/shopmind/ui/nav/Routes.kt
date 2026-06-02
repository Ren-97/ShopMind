package com.example.shopmind.ui.nav

object Routes {
    const val CHAT = "chat"
    const val CART = "cart"
    const val CHECKOUT = "checkout"
    const val PRODUCT_ARG = "productId"
    const val PRODUCT_DETAIL = "product/{$PRODUCT_ARG}"

    fun productDetail(productId: String) = "product/$productId"
}
