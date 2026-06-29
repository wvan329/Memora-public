package com.wgk.memora

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.widget.Toast
import androidx.core.app.NotificationCompat
import org.json.JSONObject

/**
 * 前台 Service —— 维持 WebSocket 长连接 + 剪贴板接收 + AI 推送通知。
 *
 * 为什么用前台 Service 而非后台 Service：
 * - Android 8+ 对后台 Service 有严格限制（idle 后几分钟内被杀）。
 * - 前台 Service 配合常驻通知，优先级与前台 Activity 相同，几乎不会被杀。
 * - `START_STICKY`：被杀后系统自动重启 Service（`onStartCommand` 被调用，`onCreate` 不会）。
 *   WsManager 在 `onCreate` 中 `connect()`，重启后通过 `state == CONNECTED` 检测强制重连。
 *
 * 剪贴板同步（v2.0 简化）：
 * - **电脑 → 手机**：WsManager 双连接订阅 "clipboard_sync"，两台电脑推送都写入手机剪贴板。
 * - **手机 → 电脑**：不再自动检测剪贴板变更，改为用户手动点击通知栏「上传」按钮触发。
 * - **回环防护**：不存在——手机不再监听剪贴板，闭环路径架构性切断。
 */
class SyncService : Service() {

    companion object {
        const val ACTION_UPLOAD = "com.wgk.memora.action.UPLOAD"
        const val ACTION_DOWNLOAD = "com.wgk.memora.action.DOWNLOAD"
    }

    private lateinit var clipboardManager: ClipboardManager

    private var unsubClipboard: (() -> Unit)? = null
    private var unsubNotification: (() -> Unit)? = null
    private var unsubInstallApk: (() -> Unit)? = null
    private var eyeRestHandler: Handler? = null
    private var eyeRestRunnable: Runnable? = null
    private var eyeRestStarted = false

    override fun onCreate() {
        super.onCreate()

        clipboardManager = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        startForeground(1, buildNotification())

        // Service 可能被系统独立重启（START_STICKY），确保密码已加载
        WsManager.init(this)
        WsManager.connect("home")
        WsManager.connect("work")

        // 订阅 PC 剪贴板推送 → 写入系统剪贴板
        unsubClipboard = WsManager.subscribe("clipboard_sync") { msg ->
            val text = msg.optString("content", "")
            if (text.isNotEmpty()) {
                clipboardManager.setPrimaryClip(ClipData.newPlainText("synced", text))
                Toast.makeText(this, "已复制", Toast.LENGTH_SHORT).show()
            }
        }


        createNotificationChannel()
        // AI 回复完成 → 弹出系统通知。点击通知跳转 MainActivity 并加载对应会话
        unsubNotification = WsManager.subscribe("push_notification") { msg ->
            val title = msg.optString("title", "AI 提醒")
            val content = msg.optString("content", "")
            val sessionId = msg.optString("session_id", "")
            if (content.isNotEmpty()) showPushNotification(title, content, sessionId)
        }

        // APK 自动安装：收到后端推送后下载 + 弹出安装界面
        unsubInstallApk = WsManager.subscribe("install_apk") { msg ->
            val path = msg.optString("path", "")
            val url = msg.optString("url", "")
            if (path.isNotEmpty() || url.isNotEmpty()) {
                InstallHelper.downloadAndInstall(this, path, WsManager.password, url)
            }
        }

        // client_action_request 由 WebView 中的 JS 通过 chat 连接处理，
        // 不在此处（global 连接）监听，避免重复响应
    }

    /**
     * 处理通知栏按钮发来的上传/下载 action。
     * 透明 Activity（ClipboardActionActivity）将 Intent 转发到这里执行实际操作。
     */
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (!eyeRestStarted) {
            startEyeRestReminder()
            eyeRestStarted = true
        }
        when (intent?.action) {
            ACTION_UPLOAD -> {
                val text = ClipHelper.pushClipboard(this)
                if (text != null) {
                    Toast.makeText(this, "已上传: ${text.take(30)}", Toast.LENGTH_SHORT).show()
                } else {
                    Toast.makeText(this, "剪贴板为空", Toast.LENGTH_SHORT).show()
                }
            }
            ACTION_DOWNLOAD -> {
                WsManager.sendToActive(JSONObject().apply { put("type", "request_clipboard") })
                Toast.makeText(this, "正在拉取 PC 剪贴板…", Toast.LENGTH_SHORT).show()
            }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        stopEyeRestReminder()
        unsubClipboard?.invoke()
        unsubNotification?.invoke()
        unsubInstallApk?.invoke()
        WsManager.disconnect()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    /** 每 20 分钟弹 Toast 提醒远眺 */
    private fun startEyeRestReminder() {
        eyeRestHandler = Handler(Looper.getMainLooper())
        eyeRestRunnable = object : Runnable {
            override fun run() {
                Toast.makeText(this@SyncService, "👀 远眺 20 秒，保护眼睛", Toast.LENGTH_SHORT).show()
                eyeRestHandler?.postDelayed(this, 20 * 60 * 1000L)
            }
        }
        eyeRestHandler?.postDelayed(eyeRestRunnable!!, 20 * 60 * 1000L)
    }

    private fun stopEyeRestReminder() {
        eyeRestRunnable?.let { eyeRestHandler?.removeCallbacks(it) }
        eyeRestHandler = null
        eyeRestRunnable = null
    }

    /**
     * 创建 AI 推送通知渠道。
     * IMPORTANCE_HIGH：AI 通知带声音和 heads-up 弹窗，确保用户不遗漏。
     */
    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel(
                "push_notification", "AI 通知", NotificationManager.IMPORTANCE_HIGH
            ).apply { description = "电脑端 AI 推送的通知" }.also {
                getSystemService(NotificationManager::class.java).createNotificationChannel(it)
            }
        }
    }

    /** AI 回复完成 → 弹出系统通知。点击通知跳转 MainActivity 并加载对应会话 */
    private fun showPushNotification(title: String, content: String, sessionId: String) {
        val intent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
            if (sessionId.isNotEmpty()) {
                putExtra("session_id", sessionId)
            }
        }
        val openIntent = PendingIntent.getActivity(this, 1,
            intent, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        getSystemService(NotificationManager::class.java).notify(
            System.currentTimeMillis().toInt(),
            NotificationCompat.Builder(this, "push_notification")
                .setContentTitle(title).setContentText(content)
                .setSmallIcon(R.drawable.ic_notification)
                .setAutoCancel(true).setContentIntent(openIntent).build()
        )
    }

    /**
     * 构建常驻通知 —— 展示"Memora · 心有灵犀 · 一点即通"，含上传/下载按钮。
     * `setOngoing(true)` 防止用户滑动清除。
     */
    private fun buildNotification(): Notification {
        val channelId = "memora_sync"
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel(channelId, "Memora", NotificationManager.IMPORTANCE_MIN).apply {
                description = "Memora · 心有灵犀"
            }.also { getSystemService(NotificationManager::class.java).createNotificationChannel(it) }
        }

        val openIntent = PendingIntent.getActivity(this, 0,
            Intent(this, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
            }, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)

        val uploadPending = PendingIntent.getActivity(this, 100,
            Intent(this, ClipboardActionActivity::class.java).apply {
                putExtra(ClipboardActionActivity.EXTRA_ACTION, ClipboardActionActivity.ACTION_UPLOAD)
            }, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)

        val downloadPending = PendingIntent.getActivity(this, 101,
            Intent(this, ClipboardActionActivity::class.java).apply {
                putExtra(ClipboardActionActivity.EXTRA_ACTION, ClipboardActionActivity.ACTION_DOWNLOAD)
            }, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)

        return NotificationCompat.Builder(this, channelId)
            .setContentTitle("Memora")
            .setContentText("心有灵犀 · 一点即通")
            .setSmallIcon(R.drawable.ic_notification)
            .setOngoing(true)
            .setContentIntent(openIntent)
            .addAction(R.drawable.ic_notification, "上传", uploadPending)
            .addAction(R.drawable.ic_notification, "下载", downloadPending)
            .build()
    }
}
