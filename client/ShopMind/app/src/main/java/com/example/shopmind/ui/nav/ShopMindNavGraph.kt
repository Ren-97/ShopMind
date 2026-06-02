package com.example.shopmind.ui.nav

import androidx.compose.runtime.Composable
import androidx.navigation.NavHostController
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.navArgument
import com.example.shopmind.ui.screens.CartScreen
import com.example.shopmind.ui.screens.ChatScreen
import com.example.shopmind.ui.screens.OrderConfirmScreen
import com.example.shopmind.ui.screens.ProductDetailScreen

@Composable
fun ShopMindNavGraph(navController: NavHostController) {
    NavHost(navController = navController, startDestination = Routes.CHAT) {
        composable(Routes.CHAT) {
            ChatScreen(navController)
        }
        composable(
            route = Routes.PRODUCT_DETAIL,
            arguments = listOf(navArgument(Routes.PRODUCT_ARG) { type = NavType.StringType }),
        ) { backStackEntry ->
            val productId = backStackEntry.arguments?.getString(Routes.PRODUCT_ARG).orEmpty()
            ProductDetailScreen(navController = navController, productId = productId)
        }
        composable(Routes.CART) {
            CartScreen(navController)
        }
        composable(Routes.CHECKOUT) {
            OrderConfirmScreen(navController)
        }
    }
}
