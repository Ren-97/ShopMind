package com.example.shopmind.ui.nav

object Routes {
    const val CHAT = "chat"
    // profile 两个入口:菜单进(PROFILE,无跳过)/ 新建用户后跳进(onboarding=true,右上角显式「跳过」)
    const val PROFILE = "profile"
    const val PROFILE_ARG = "onboarding"
    const val PROFILE_ROUTE = "profile?$PROFILE_ARG={$PROFILE_ARG}"

    /** 新建用户后落地的 profile:带 onboarding 标志,顶栏显示「跳过」。 */
    fun profileOnboarding() = "profile?$PROFILE_ARG=true"

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
