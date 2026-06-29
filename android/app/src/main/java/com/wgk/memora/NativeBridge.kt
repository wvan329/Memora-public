package com.wgk.memora

import android.content.Context
import android.webkit.JavascriptInterface

/**
 * JS ↔ 原生桥接 —— 通过 `addJavascriptInterface` 暴露给 WebView 中前端 JS 调用。
 *
 * 设计决策：
 * - `onMessageToJs` 回调由 Activity 注入：WebView 的 `evaluateJavascript` 需要在主线程执行，
 *   NativeBridge 不持有 WebView 引用，避免生命周期泄漏。
 * - `sendMessage()` 直接调用 `WsManager.send()`：WsManager 内部已有 `pendingQueue` 断线缓冲，
 *   NativeBridge 侧无需再加一层队列。
 * - 剪贴板推送（sendClipboard）走 `WsManager.sendToAll()`，确保同时推送到 home 和 work。
 * - 设备切换（switchDevice）只影响 WebView 加载的前端页面 + 活跃设备标记，
 *   不再断开/重连全局 WebSocket。
 */
class NativeBridge(
    private val context: Context,
    private val onMessageToJs: (String) -> Unit,
    private val onToast: (String) -> Unit
) {
    /** 设备切换回调：前端点击 Home/Work 后触发，由 MainActivity 注入 */
    var onSwitchDevice: ((String) -> Unit)? = null

    /**
     * 定位结果回调：由 MainActivity 注入，用于将异步定位结果推回 JS。
     *
     * 为什么需要单独回调：
     * `onMessageToJs` 通道走 `window.__nativeOnMessage`，与后端推送消息共用，
     * 混入定位结果会让前端消息路由变复杂（需要 type 字段区分）。
     * 独立回调走 `window.__onLocationResult`，干净隔离。
     */
    var onLocationResult: ((String) -> Unit)? = null

    /**
     * JS 调用 `NativeBridge.sendMessage(json)` → 通过全局 WebSocket 发送至**活跃设备**。
     * 消息格式由前端 JS 自行构造（`type` 字段决定后端路由）。
     * 用于 client_action_result 等与当前前端页面相关的消息。
     */
    @JavascriptInterface
    fun sendMessage(json: String) = WsManager.send(json)

    /**
     * JS 调用 `NativeBridge.getPassword()` → 返回 SharedPreferences 中的密码。
     * 前端初始化时若 localStorage 为空则调用此方法，避免 LoginOverlay 二次弹窗。
     * 密码仅在 JS ↔ Kotlin 桥接传输，不经过网络。
     */
    @JavascriptInterface
    fun getPassword(): String {
        return context.getSharedPreferences("memora", Context.MODE_PRIVATE)
            .getString("password", "") ?: ""
    }
    /**
     * JS 调用 `NativeBridge.getStatus()` → 返回活跃设备 WebSocket 连接状态字符串。
     * 前端据此决定是否显示"连接断开"提示。
     */
    @JavascriptInterface
    fun getStatus() = WsManager.state.name

    /**
     * JS 调用 `NativeBridge.sendClipboard(text)` → 同时推送到 home 和 work。
     * 用户在前端点击"上传剪贴板"按钮时触发。
     */
    @JavascriptInterface
    fun sendClipboard(text: String) {
        if (text.isNotEmpty()) {
            WsManager.sendToAll(org.json.JSONObject().apply {
                put("type", "clipboard_push")
                put("content", text)
                put("timestamp", System.currentTimeMillis())
            })
            onToast("已上传: ${text.take(30)}")
        } else {
            onToast("剪贴板为空，请先复制文字")
        }
    }

    /**
     * JS 调用 `NativeBridge.getLocation()` → 异步获取设备当前位置。
     *
     * 为什么是异步：旧版同步调用用 CountDownLatch 阻塞 WebView JS 线程等待 GPS
     * 定位（最长 12 秒），导致整个页面卡死。现改为纯异步：立即返回，定位完成后
     * 通过 `onLocationResult` 回调推送到 JS 端 `window.__onLocationResult(json)`。
     */
    @JavascriptInterface
    fun getLocation() {
        ClipHelper.getLocationAsync(context) { result ->
            onLocationResult?.invoke(result.toString())
        }
    }

    /**
     * 从后端推送消息到前端 JS。
     * 调用 `window.__nativeOnMessage` 函数（由前端 app.js 注册）。
     * 注意：转义处理——反斜杠、单引号、换行符必须转义，否则 `evaluateJavascript` 会解析失败。
     */
    fun pushToJs(json: String) = onMessageToJs(json)

    /**
     * JS 调用 `NativeBridge.switchDevice(device)` → 切换活跃设备。
     *
     * v2.0 变更：不再断开/重连全局 WebSocket。
     * 全局连接始终双连（home + work），侧边栏切换只做三件事：
     * 1. 保存设备偏好到 SharedPreferences
     * 2. 更新 WsManager.activeDevice（影响 sendMessage 目标和 getStatus 返回值）
     * 3. 回调 MainActivity 重新加载 WebView（加载新设备的前端页面）
     */
    @JavascriptInterface
    fun switchDevice(device: String) {
        android.os.Handler(android.os.Looper.getMainLooper()).post {
            val prefs = context.getSharedPreferences("memora", Context.MODE_PRIVATE)
            prefs.edit().putString("device", device).apply()
            WsManager.switchDevice(device)
            onSwitchDevice?.invoke(device)
        }
    }

    /**
     * JS 调用 `NativeBridge.triggerInstall()` → 手动触发已下载 APK 的安装。
     * 用于设置页面的「安装更新」按钮，解决偶尔安装界面不弹出的问题。
     */
    @JavascriptInterface
    fun triggerInstall() {
        android.os.Handler(android.os.Looper.getMainLooper()).post {
            val apkFile = java.io.File(context.getExternalFilesDir(null), "memora_update.apk")
            if (!apkFile.exists()) {
                onToast("没有待安装的更新")
                return@post
            }
            try {
                val uri = androidx.core.content.FileProvider.getUriForFile(
                    context,
                    "${context.packageName}.fileprovider",
                    apkFile
                )
                val intent = android.content.Intent(android.content.Intent.ACTION_VIEW).apply {
                    setDataAndType(uri, "application/vnd.android.package-archive")
                    addFlags(android.content.Intent.FLAG_GRANT_READ_URI_PERMISSION)
                    addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)
                }
                context.startActivity(intent)
            } catch (e: Exception) {
                onToast("安装失败: ${e.message}")
            }
        }
    }
}
