package com.example.shopmind

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.compose.rememberNavController
import coil3.ImageLoader
import coil3.compose.setSingletonImageLoaderFactory
import coil3.request.crossfade
import com.example.shopmind.ui.nav.ShopMindNavGraph
import com.example.shopmind.ui.theme.ShopMindTheme
import com.example.shopmind.viewmodel.ChatViewModel

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            // 全局给所有 AsyncImage 开 crossfade —— 图加载好时平滑淡入而非"啪"地弹出(配一次,9 处都生效)
            setSingletonImageLoaderFactory { context ->
                ImageLoader.Builder(context)
                    .crossfade(true)
                    .build()
            }
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
