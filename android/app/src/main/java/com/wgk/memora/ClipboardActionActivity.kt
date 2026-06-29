package com.wgk.memora

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity

/**
 * 透明 Activity —— 响应通知栏按钮的上传/下载操作，执行完毕后自动关闭。
 *
 * 为什么用独立 Activity 而不是直接在 Service 中处理：
 * - `ClipboardManager` 需要 Context（Service 有，但 Toast 弹窗在后台 Service 中不显示）。
 * - 通知栏的 `PendingIntent` 只能启动 Activity/BroadcastReceiver。
 * - 透明 Activity 执行操作 + Toast 提示后 `finish()`，用户无感知。
 */
class ClipboardActionActivity : AppCompatActivity() {

    companion object {
        const val EXTRA_ACTION = "action"
        const val ACTION_UPLOAD = "upload"
        const val ACTION_DOWNLOAD = "download"
    }

    private val handler = Handler(Looper.getMainLooper())
    private var unsub: (() -> Unit)? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        when (intent.getStringExtra(EXTRA_ACTION)) {
            // 上传延迟 200ms：等待透明 Activity 的窗口创建完成再操作，
            // 避免系统在窗口未就绪时因 `finish()` 导致的短暂黑屏闪烁
            ACTION_UPLOAD -> window.decorView.postDelayed({ doUpload() }, 200)
            ACTION_DOWNLOAD -> doDownload()
            else -> finish()
        }
    }

    /** 读取系统剪贴板 → 推送到 PC，完成后关闭 */
    private fun doUpload() {
        val text = ClipHelper.pushClipboard(this)
        if (text != null) {
            Toast.makeText(this, "已上传: ${text.take(30)}", Toast.LENGTH_SHORT).show()
        } else {
            Toast.makeText(this, "剪贴板为空", Toast.LENGTH_SHORT).show()
        }
        finish()
    }

    /**
     * 拉取 PC 剪贴板。
     * 订阅 `clipboard_sync` 消息 → 收到后写入系统剪贴板 → 关闭。
     * 3s 超时后提示"拉取超时"并关闭。
     */
    private fun doDownload() {
        var finished = false
        unsub = WsManager.subscribe("clipboard_sync") { msg ->
            if (finished) return@subscribe
            val text = msg.optString("content", "")
            if (text.isNotEmpty()) {
                finished = true
                val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                cm.setPrimaryClip(ClipData.newPlainText("synced", text))
                finish()
            }
        }

        handler.postDelayed({
            if (!finished) {
                Toast.makeText(this, "拉取超时", Toast.LENGTH_SHORT).show()
                finish()
            }
        }, 3_000)

        WsManager.send(org.json.JSONObject().apply { put("type", "request_clipboard") })
    }

    override fun onDestroy() {
        super.onDestroy()
        unsub?.invoke()  // 防止 Activity 已关闭但回调仍触发导致崩溃
        handler.removeCallbacksAndMessages(null)
    }
}
