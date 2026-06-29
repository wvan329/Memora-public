package com.wgk.memora

import android.Manifest
import android.app.DownloadManager
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.os.Message
import android.text.InputType
import android.webkit.GeolocationPermissions
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.EditText
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import org.json.JSONObject
import java.net.HttpURLConnection

import java.net.URL
import java.util.UUID
import kotlin.concurrent.thread

/**
 * 主界面 —— WebView 加载 AI 聊天前端 + 注入原生桥接。
 *
 * 职责：
 * 1. WebView 生命周期管理（创建、加载、暗色模式禁用、多窗口支持）
 * 2. 密码验证（首次使用弹出密码框 → HTTP POST 验证 → 存入 SharedPreferences）
 * 3. 原生桥接注入（`NativeBridge` → `addJavascriptInterface`）
 * 4. 消息路由（`WsManager.subscribe("*")` 接收全局消息 → 转发给前端 JS）
 * 5. `window.open` 拦截（子会话 → SubSessionActivity，图片查看 → SubSessionActivity）
 * 6. 文件选择器（`onShowFileChooser` → 系统图片选择器）
 * 7. 文件下载（`setDownloadListener` → DownloadManager）
 * 8. 剪贴板上传/下载（通知栏按钮 → Intent action）
 *
 * v2.0 设备切换变更：
 * - 侧边栏切换不再触发全局 WebSocket 断连/重连。
 * - WsManager 始终维护 home + work 双连接，侧边栏只切换 WebView 加载的页面。
 */
class MainActivity : AppCompatActivity() {

    companion object {
        const val EXTRA_ACTION = "clipboard_action"
        const val ACTION_UPLOAD = "upload"
        const val ACTION_DOWNLOAD = "download"

        /** 从 SharedPreferences 读取设备偏好，返回对应的聊天 URL */
        fun getChatUrl(context: Context): String {
            val prefs = context.getSharedPreferences("memora", Context.MODE_PRIVATE)
            val device = prefs.getString("device", "home") ?: "home"
            return "https://a.wgk-fun.top/$device/"
        }
    }

    /** 当前设备的聊天 URL，在 onCreate 和切换设备时更新 */
    private var currentChatUrl = "https://a.wgk-fun.top/home/"

    private lateinit var webView: WebView
    private lateinit var nativeBridge: NativeBridge
    private var unsubDownload: (() -> Unit)? = null
    private var downloadTimeout: Runnable? = null
    private val handler = Handler(Looper.getMainLooper())
    private var filePathCallback: android.webkit.ValueCallback<Array<Uri>>? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // 透明状态栏，沉浸体验
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            window.apply {
                statusBarColor = android.graphics.Color.TRANSPARENT
                decorView.systemUiVisibility = android.view.View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR
            }
        }

        nativeBridge = NativeBridge(
            context = this,
            onMessageToJs = { json -> pushToJs(json) },
            onToast = { msg -> Toast.makeText(this, msg, Toast.LENGTH_SHORT).show() }
        ).also { bridge ->
            // 注入定位结果回调 —— 异步 GPS 定位完成后推送结果到 JS 端。
            // 不走 pushToJs（那是 window.__nativeOnMessage 通道），
            // 直接 evaluateJavascript 调用独立的 window.__onLocationResult 通道。
            bridge.onLocationResult = { json ->
                val escaped = json.replace("\\", "\\\\")
                    .replace("'", "\\'")
                    .replace("\n", "\\n")
                webView.post {
                    webView.evaluateJavascript(
                        "window.__onLocationResult&&window.__onLocationResult('$escaped')",
                        null
                    )
                }
            }
        }

        webView = WebView(this).apply {
            settings.apply {
                javaScriptEnabled = true
                domStorageEnabled = true
                mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
                // LOAD_NO_CACHE：开发阶段确保每次加载最新前端代码。
                // 正式发布后可改为 LOAD_DEFAULT 利用缓存加速。
                cacheMode = WebSettings.LOAD_NO_CACHE
                userAgentString += " Memora/1.0"
                textZoom = 115

                // 禁止 Android 系统级暗色模式覆盖——前端 Tailwind 自行处理暗色主题
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                    isAlgorithmicDarkeningAllowed = false
                } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    @Suppress("DEPRECATION")
                    forceDark = WebSettings.FORCE_DARK_OFF
                }
                // 允许 `window.open` 创建新窗口（子会话 / 图片查看）
                setSupportMultipleWindows(true)
            }
            addJavascriptInterface(nativeBridge, "NativeBridge")

            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(
                    view: WebView?, request: WebResourceRequest?
                ): Boolean {
                    val url = request?.url.toString()
                    if (url.startsWith(currentChatUrl)) {
                        view?.loadUrl(url)
                    } else {
                        startActivity(Intent(Intent.ACTION_VIEW, request?.url))
                    }
                    return true
                }

                /**
                 * HTTP 错误码捕获 —— 设备挂了时 nginx 返回 502/503/504。
                 * isForMainFrame 过滤子资源，只处理主页面。
                 */
                override fun onReceivedHttpError(
                    view: WebView,
                    request: WebResourceRequest,
                    errorResponse: WebResourceResponse
                ) {
                    if (request.isForMainFrame
                        && errorResponse.statusCode in listOf(502, 503, 504)
                    ) {
                        autoSwitchDevice()
                    }
                }

                /**
                 * 网络层错误（DNS 失败、连接超时等）—— 整个云服务器不可达。
                 */
                override fun onReceivedError(
                    view: WebView,
                    request: WebResourceRequest,
                    error: WebResourceError
                ) {
                    if (request.isForMainFrame) {
                        autoSwitchDevice()
                    }
                }

                /** 兼容旧 API 的网络层错误 */
                @Suppress("DEPRECATION")
                override fun onReceivedError(
                    view: WebView, errorCode: Int,
                    description: String?, failingUrl: String?
                ) {
                    autoSwitchDevice()
                }

            }

            // 文件下载 → 系统 DownloadManager（通知栏显示进度）
            setDownloadListener { url, _, _, mimeType, _ ->
                handleDownload(url, mimeType)
            }

            webChromeClient = object : android.webkit.WebChromeClient() {
                // 地理位置授权：一律允许（AI 需要获取手机定位）
                override fun onGeolocationPermissionsShowPrompt(
                    origin: String?, callback: GeolocationPermissions.Callback?
                ) {
                    callback?.invoke(origin, true, false)
                }

                // JS `confirm()` → 原生 AlertDialog（比 WebView 默认弹窗更美观）
                override fun onJsConfirm(
                    view: WebView?, url: String?, message: String?,
                    result: android.webkit.JsResult?
                ): Boolean {
                    android.app.AlertDialog.Builder(this@MainActivity)
                        .setMessage(message ?: "")
                        .setPositiveButton("确定") { _, _ -> result?.confirm() }
                        .setNegativeButton("取消") { _, _ -> result?.cancel() }
                        .setOnDismissListener { result?.cancel() }
                        .show()
                    return true
                }

                // 文件选择器 → 系统图片选择器（vision_understand 的 pick 模式）
                override fun onShowFileChooser(
                    webView: WebView?,
                    callback: android.webkit.ValueCallback<Array<Uri>>?,
                    fileChooserParams: FileChooserParams?
                ): Boolean {
                    filePathCallback = callback
                    val intent = Intent(Intent.ACTION_GET_CONTENT).apply {
                        type = "image/*"
                        putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
                        addCategory(Intent.CATEGORY_OPENABLE)
                    }
                    startActivityForResult(Intent.createChooser(intent, "选择图片"), 200)
                    return true
                }

                /**
                 * `window.open` 拦截 —— 返回一个"陷阱" WebView 捕获 URL 参数，
                 * 然后根据 `?s=` 或 `?image_view=` 启动 SubSessionActivity。
                 *
                 * 为什么用陷阱 WebView 而不是直接解析 URL：
                 * `onCreateWindow` 的 `resultMsg` 要求返回一个 `WebViewTransport`，
                 * 不能直接在此方法中 `startActivity`（会导致时序问题）。
                 * 陷阱 WebView 接收跳转 → `shouldOverrideUrlLoading` 中解析参数 → 启动目标 Activity。
                 */
                override fun onCreateWindow(
                    view: WebView?, isDialog: Boolean, isUserGesture: Boolean,
                    resultMsg: Message?
                ): Boolean {
                    val ctx = view?.context ?: return false
                    val trapWebView = WebView(ctx).apply {
                        settings.javaScriptEnabled = true
                        webViewClient = object : WebViewClient() {
                            override fun shouldOverrideUrlLoading(
                                v: WebView?, req: WebResourceRequest?
                            ): Boolean {
                                val imgView = req?.url?.getQueryParameter("image_view") ?: ""
                                val imgIdx = req?.url?.getQueryParameter("image_index") ?: ""
                                if (imgView.isNotEmpty()) {
                                    startActivity(Intent(ctx, SubSessionActivity::class.java).apply {
                                        putExtra(SubSessionActivity.EXTRA_IMAGE_URL, imgView)
                                        putExtra(SubSessionActivity.EXTRA_IMAGE_INDEX, imgIdx)
                                    })
                                } else {
                                    // 未知 URL → 系统浏览器兜底
                                    startActivity(Intent(Intent.ACTION_VIEW, req?.url))
                                }
                                return true
                            }
                        }
                    }
                    val transport = resultMsg?.obj as? WebView.WebViewTransport
                    transport?.webView = trapWebView
                    resultMsg?.sendToTarget()
                    return true
                }
            }
        }
        setContentView(webView)

        // 加载 SharedPreferences 中的密码。首次使用 → 弹出密码对话框
        WsManager.init(this)
        val prefs = getSharedPreferences("memora", MODE_PRIVATE)
        val savedPassword = prefs.getString("password", "")

        if (savedPassword.isNullOrEmpty()) {
            showPasswordDialog()
        } else {
            onPasswordReady()
        }
    }

    /** 首次使用弹出密码对话框，验证通过后存入 SharedPreferences */
    private fun showPasswordDialog() {
        val input = EditText(this).apply {
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            hint = "密码"
        }

        AlertDialog.Builder(this)
            .setTitle("输入密码")
            .setView(input.apply {
                setPadding(40, 24, 40, 24)
            })
            .setPositiveButton("确定") { dialog, _ ->
                val pwd = input.text.toString().trim()
                if (pwd.isEmpty()) {
                    Toast.makeText(this@MainActivity, "密码不能为空", Toast.LENGTH_SHORT).show()
                    showPasswordDialog()
                    return@setPositiveButton
                }
                // 禁用按钮防止重复点击（密码验证是网络操作，有延迟）
                (dialog as? AlertDialog)?.getButton(AlertDialog.BUTTON_POSITIVE)?.isEnabled = false

                thread {
                    val ok = verifyPassword(pwd)
                    runOnUiThread {
                        if (ok) {
                            getSharedPreferences("memora", MODE_PRIVATE)
                                .edit().putString("password", pwd).apply()
                            WsManager.init(this@MainActivity)
                            onPasswordReady()
                        } else {
                            Toast.makeText(this@MainActivity, "密码错误，请重试", Toast.LENGTH_SHORT).show()
                            showPasswordDialog()
                        }
                    }
                }
            }
            .setNegativeButton("退出") { _, _ -> finish() }
            .setCancelable(false)
            .show()
    }

    /** HTTP POST 验证密码。在子线程执行，不阻塞 UI */
    private fun verifyPassword(pwd: String): Boolean {
        return try {
            val url = URL("${getChatUrl(this)}api/login")
            val conn = url.openConnection() as HttpURLConnection
            conn.requestMethod = "POST"
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json")
            conn.connectTimeout = 5000
            conn.readTimeout = 5000
            conn.outputStream.write("""{"password":"$pwd"}""".toByteArray())
            conn.responseCode == 200
        } catch (e: Exception) {
            false
        }
    }

    /** 密码就绪后：订阅消息 → 启动 SyncService → 加载前端 → 请求定位权限 */
    private fun onPasswordReady() {
        currentChatUrl = getChatUrl(this)

        // 订阅所有 WebSocket 消息（`"*"` 通配），过滤掉 SyncService 已处理的类型，
        // 其余消息通过 NativeBridge 推送给前端 JS
        WsManager.subscribe("*") { msg ->
            val type = msg.optString("type", "")
            if (type != "clipboard_sync" && type != "push_notification") {
                nativeBridge.pushToJs(msg.toString())
            }
        }

        // 设备切换回调：侧边栏点击 Home/Work 后，重新加载 WebView。
        // v2.0：不再调用 WsManager.switchDevice（全局连接不受影响），
        // NativeBridge.switchDevice 已经更新了 activeDevice + SharedPreferences
        nativeBridge.onSwitchDevice = { device ->
            currentChatUrl = "https://a.wgk-fun.top/$device/"
            webView.loadUrl("$currentChatUrl?s=${UUID.randomUUID()}")
        }

        startSyncService()
        // 从通知进入时带 session_id → 直接加载对应会话；否则新建
        val targetSessionId = intent?.getStringExtra("session_id") ?: ""
        webView.loadUrl(
            if (targetSessionId.isNotEmpty()) "$currentChatUrl?s=$targetSessionId"
            else "$currentChatUrl?s=${UUID.randomUUID()}"
        )
        handleIntent(intent)
        requestLocationPermission()
    }

    private fun requestLocationPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION)
                != PackageManager.PERMISSION_GRANTED) {
                ActivityCompat.requestPermissions(
                    this,
                    arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION),
                    100
                )
            }
        }
    }

    /** 上次自动切换设备的时间戳，10 秒冷却防止死循环 */
    private var lastAutoSwitchTime = 0L

    /**
     * 自动切换到备用设备（带冷却）。
     * 502 捕获、网络错误统一走此方法。
     * v2.0：不再调用 WsManager.switchDevice（全局连接不受影响），
     * 仅更新 activeDevice + SharedPreferences + 重载 WebView。
     */
    private fun autoSwitchDevice() {
        if (System.currentTimeMillis() - lastAutoSwitchTime < 10_000) return
        lastAutoSwitchTime = System.currentTimeMillis()

        val prefs = getSharedPreferences("memora", MODE_PRIVATE)
        val currentDevice = prefs.getString("device", "home") ?: "home"
        val alt = if (currentDevice == "home") "work" else "home"
        prefs.edit().putString("device", alt).apply()
        WsManager.switchDevice(alt)
        currentChatUrl = "https://a.wgk-fun.top/$alt/"
        Toast.makeText(this, "已自动切换到 $alt", Toast.LENGTH_LONG).show()
        webView.loadUrl("$currentChatUrl?s=${UUID.randomUUID()}")
    }

    /** 从通知点击进入时复用已有 Activity 实例，直接导航到目标会话 */
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        val sid = intent.getStringExtra("session_id") ?: ""
        if (sid.isNotEmpty()) {
            webView.loadUrl("$currentChatUrl?s=$sid")
        }
        handleIntent(intent)
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == 200) {
            // 解析文件选择器返回的 URI（单张或多张）
            val results: Array<Uri>? = if (resultCode == RESULT_OK && data != null) {
                if (data.clipData != null) {
                    Array(data.clipData!!.itemCount) { i -> data.clipData!!.getItemAt(i).uri }
                } else if (data.data != null) {
                    arrayOf(data.data!!)
                } else null
            } else null
            filePathCallback?.onReceiveValue(results)
            filePathCallback = null
        }
    }

    /** 处理通知栏按钮发来的剪贴板上传/下载 Intent */
    private fun handleIntent(intent: Intent?) {
        val action = intent?.getStringExtra(EXTRA_ACTION) ?: return
        setIntent(intent)
        when (action) {
            ACTION_UPLOAD -> doUpload()
            ACTION_DOWNLOAD -> doDownload()
        }
    }

    private fun doUpload() {
        val text = ClipHelper.pushClipboard(this)
        if (text != null) {
            Toast.makeText(this, "已上传: ${text.take(30)}", Toast.LENGTH_SHORT).show()
        } else {
            Toast.makeText(this, "剪贴板为空，请先复制文字", Toast.LENGTH_SHORT).show()
        }
    }

    /**
     * 拉取活跃设备的 PC 剪贴板。
     * 订阅 `clipboard_sync` → 收到后写入系统剪贴板 → 清理订阅。
     * 10s 超时提示用户。
     */
    private fun doDownload() {
        unsubDownload?.invoke()
        downloadTimeout?.let { handler.removeCallbacks(it) }

        var finished = false
        unsubDownload = WsManager.subscribe("clipboard_sync") { msg ->
            if (finished) return@subscribe
            val text = msg.optString("content", "")
            if (text.isNotEmpty()) {
                finished = true
                val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                cm.setPrimaryClip(ClipData.newPlainText("synced", text))
                cleanupDownload()
            }
        }

        downloadTimeout = Runnable {
            if (!finished) {
                Toast.makeText(this, "拉取超时，电脑在线吗？", Toast.LENGTH_SHORT).show()
                cleanupDownload()
            }
        }
        handler.postDelayed(downloadTimeout!!, 10_000)

        WsManager.sendToActive(JSONObject().apply {
            put("type", "request_clipboard")
        })
    }

    private fun cleanupDownload() {
        unsubDownload?.invoke()
        unsubDownload = null
        downloadTimeout?.let { handler.removeCallbacks(it) }
        downloadTimeout = null
    }

    override fun onDestroy() {
        super.onDestroy()
        cleanupDownload()
    }

    /**
     * 文件下载 → 使用系统 DownloadManager。
     * 从 URL 的 `path` 参数中提取文件名；无法提取时用 `guessFileName` 兜底。
     */
    private fun handleDownload(url: String, mimeType: String) {
        try {
            val pathParam = Uri.parse(url).getQueryParameter("path") ?: ""
            val rawName = pathParam.substringAfterLast("\\").substringAfterLast("/")
            val filename = java.net.URLDecoder.decode(rawName, "UTF-8").ifEmpty {
                android.webkit.URLUtil.guessFileName(url, null, mimeType)
            }
            val request = DownloadManager.Request(Uri.parse(url)).apply {
                setTitle(filename)
                setDescription("下载中…")
                setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, filename)
                setMimeType(mimeType)
            }
            val dm = getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager
            dm.enqueue(request)
            Toast.makeText(this, "开始下载", Toast.LENGTH_SHORT).show()
        } catch (e: Exception) {
            Toast.makeText(this, "下载失败: ${e.message}", Toast.LENGTH_SHORT).show()
        }
    }

    private fun pushToJs(json: String) {
        val escaped = json.replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace("\n", "\\n")
        webView.post {
            webView.evaluateJavascript(
                "window.__nativeOnMessage&&window.__nativeOnMessage('$escaped')",
                null
            )
        }
    }

    private fun startSyncService() {
        val intent = Intent(this, SyncService::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(intent)
        } else {
            startService(intent)
        }
    }
}
