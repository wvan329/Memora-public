package com.wgk.memora

import android.content.Context
import android.content.Intent
import android.os.Build
import android.widget.Toast
import androidx.core.content.FileProvider
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.net.URLEncoder
import java.util.concurrent.TimeUnit

/**
 * APK 下载 + 安装工具 —— 收到 install_apk 消息后由 SyncService 调用。
 *
 * 为什么不用系统 DownloadManager：
 * - DownloadManager 对自定义 query param（?password=）传递不稳定。
 * - OkHttp 完全可控：超时、进度（未来可加）、Header/Query 精确控制。
 *
 * 下载流程：
 * 1. 从 WsManager 获取密码，构造下载 URL
 * 2. OkHttp GET 下载 APK 到 externalFilesDir
 * 3. FileProvider 提供 content:// URI
 * 4. Intent.ACTION_VIEW 触发系统安装界面
 *
 * 安全性：
 * - URL 带密码，中间件验证后才放行文件
 * - 下载到沙箱目录，外部不可访问
 * - 安装后删除临时 APK（释放空间）
 */
object InstallHelper {

    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .build()

    /**
     * 在后台线程下载 APK 并触发安装。
     *
     * @param context  上下文
     * @param path     APK 在服务器上的绝对路径（回退用）
     * @param password 访问密码
     * @param url      优先使用的完整下载 URL（不含密码）；为空则从 wsBase + path 推导
     */
    fun downloadAndInstall(context: Context, path: String, password: String, url: String = "") {
        val apkFile = File(context.getExternalFilesDir(null), "memora_update.apk")

        Thread {
            try {
                // 1. 构造下载 URL（密码由手机端拼接）
                val downloadUrl: String
                if (url.isNotEmpty()) {
                    // 消息中带了完整 URL → 直接使用，append 密码
                    downloadUrl = if (url.contains("?")) "$url&password=$password"
                    else "$url?password=$password"
                } else {
                    // 回退：从 wsBase 推导（兼容旧版消息格式）
                    val encodedPath = URLEncoder.encode(path, "UTF-8")
                    val httpBase = WsManager.wsBase
                        .replace("wss://", "https://")
                        .replace("ws://", "http://")
                        .replace("/ws?conn=global", "/")
                    downloadUrl = "${httpBase}api/download?path=$encodedPath&password=$password"
                }

                // 2. 下载（先提示，避免用户以为没反应）
                showToast(context, "正在下载更新...")
                val request = Request.Builder().url(downloadUrl).build()
                val response = client.newCall(request).execute()
                if (!response.isSuccessful) {
                    showToast(context, "下载失败: HTTP ${response.code}")
                    return@Thread
                }
                val body = response.body ?: run {
                    showToast(context, "下载失败: 响应为空")
                    return@Thread
                }

                // 写入本地文件
                body.byteStream().use { input ->
                    apkFile.outputStream().use { output ->
                        input.copyTo(output)
                    }
                }

                // 3. 触发安装
                showToast(context, "下载完成，正在安装...")
                val uri = FileProvider.getUriForFile(
                    context,
                    "${context.packageName}.fileprovider",
                    apkFile
                )
                val intent = Intent(Intent.ACTION_VIEW).apply {
                    setDataAndType(uri, "application/vnd.android.package-archive")
                    addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                    addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                }
                context.startActivity(intent)

            } catch (e: Exception) {
                showToast(context, "安装失败: ${e.message}")
            }
        }.start()
    }

    private fun showToast(context: Context, msg: String) {
        android.os.Handler(android.os.Looper.getMainLooper()).post {
            Toast.makeText(context, msg, Toast.LENGTH_LONG).show()
        }
    }
}
