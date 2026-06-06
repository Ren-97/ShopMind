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
import com.example.shopmind.ui.screens.ProfileScreen
import com.example.shopmind.viewmodel.ChatViewModel

@Composable
fun ShopMindNavGraph(
    navController: NavHostController,
    chatViewModel: ChatViewModel,
) {
    NavHost(navController = navController, startDestination = Routes.CHAT) {
        composable(Routes.CHAT) {
            ChatScreen(navController = navController, vm = chatViewModel)
        }
        composable(
            route = Routes.PROFILE_ROUTE,
            arguments = listOf(
                navArgument(Routes.PROFILE_ARG) {
                    type = NavType.BoolType
                    defaultValue = false
                },
            ),
        ) { backStackEntry ->
            val onboarding = backStackEntry.arguments?.getBoolean(Routes.PROFILE_ARG) ?: false
            ProfileScreen(navController = navController, onboarding = onboarding)
        }
        composable(
            route = Routes.PRODUCT_DETAIL,
            arguments = listOf(navArgument(Routes.PRODUCT_ARG) { type = NavType.StringType }),
        ) { backStackEntry ->
            val productId = backStackEntry.arguments?.getString(Routes.PRODUCT_ARG).orEmpty()
            ProductDetailScreen(
                navController = navController,
                productId = productId,
                onCartChanged = { chatViewModel.refreshCartCount() },
            )
        }
        composable(Routes.CART) {
            CartScreen(
                navController = navController,
                onCartChanged = { chatViewModel.refreshCartCount() },
            )
        }
        composable(
            route = Routes.CHECKOUT_ROUTE,
            arguments = listOf(
                navArgument(Routes.CHECKOUT_ARG) {
                    type = NavType.StringType
                    nullable = true
                    defaultValue = null
                },
            ),
        ) { backStackEntry ->
            val skuIds = backStackEntry.arguments
                ?.getString(Routes.CHECKOUT_ARG)
                ?.split(",")
                ?.filter { it.isNotBlank() }
                .orEmpty()
            OrderConfirmScreen(
                navController = navController,
                chatViewModel = chatViewModel,
                selectedSkuIds = skuIds,
            )
        }
    }
}
