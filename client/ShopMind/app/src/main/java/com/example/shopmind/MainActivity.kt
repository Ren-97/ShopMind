package com.example.shopmind

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.navigation.compose.rememberNavController
import com.example.shopmind.ui.nav.ShopMindNavGraph
import com.example.shopmind.ui.theme.ShopMindTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            ShopMindTheme {
                val navController = rememberNavController()
                ShopMindNavGraph(navController = navController)
            }
        }
    }
}
