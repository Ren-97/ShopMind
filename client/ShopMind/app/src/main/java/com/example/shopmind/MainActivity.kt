package com.example.shopmind

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.compose.rememberNavController
import com.example.shopmind.ui.nav.ShopMindNavGraph
import com.example.shopmind.ui.theme.ShopMindTheme
import com.example.shopmind.viewmodel.ChatViewModel

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            ShopMindTheme {
                val navController = rememberNavController()
                // Activity-scoped:OrderConfirmScreen 下单成功后能往同一个 VM 插 order card
                val chatViewModel: ChatViewModel = viewModel()
                ShopMindNavGraph(
                    navController = navController,
                    chatViewModel = chatViewModel,
                )
            }
        }
    }
}
