package com.example.shopmind.ui.screens

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.navigation.NavController

/**
 * 下单确认页(Chunk 8 占位 → Chunk 11 真实化)。
 *
 * 两个入口:
 *   1. Chat 里 CheckoutCard 点 [去结算] → checkout 快照来自 SSE
 *   2. CartScreen 点 [去下单] → 屏内调 GET /cart + GET /profile 自己拼
 *
 * 提交:[确认下单] → POST /order(body 可选 address/recipient/phone 覆盖)
 *      → 成功后 popBackStack 回 ChatScreen + ViewModel 本地插一条 order card。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun OrderConfirmScreen(navController: NavController) {
    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("确认订单") },
                navigationIcon = {
                    IconButton(onClick = { navController.popBackStack() }) {
                        Icon(
                            Icons.AutoMirrored.Filled.ArrowBack,
                            contentDescription = "返回",
                        )
                    }
                },
            )
        },
        bottomBar = {
            Surface(tonalElevation = 3.dp) {
                Button(
                    onClick = {
                        // Chunk 11:POST /order → 成功后弹回 Chat + 插 order card
                    },
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(16.dp),
                ) {
                    Text("确认下单")
                }
            }
        },
    ) { innerPadding ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding)
                .padding(16.dp),
            contentAlignment = Alignment.Center,
        ) {
            Text("OrderConfirm 占位 — Chunk 11 接 POST /order")
        }
    }
}
