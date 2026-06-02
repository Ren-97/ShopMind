package com.example.shopmind.network

/**
 * 网络层常量。
 *
 * `BASE_URL` 默认指向 Android Emulator 主机别名:`10.0.2.2:8000` = 宿主机 localhost:8000。
 * 真机调试时改为局域网 IP(笔记本的 192.168.x.x:8000),记得手机和电脑同 WiFi。
 *
 * `DEFAULT_USER_ID` 对齐后端 `server/config.py::DEFAULT_USER_ID`(§4.6.8)。
 */
object ApiConfig {
    const val BASE_URL: String = "http://10.0.2.2:8000"
    const val DEFAULT_USER_ID: String = "demo_user_1"
}
