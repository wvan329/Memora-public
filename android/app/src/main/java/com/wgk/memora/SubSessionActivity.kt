package com.wgk.memora

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.webkit.GeolocationPermissions
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import org.json.JSONObject

/**
 * 图片查看器 —— 从 MainActivity 的 `window.open` 触发。
 *
 * 前端通过 `window.open('?image_view=<encoded_urls>&image_index=<n>', '_blank')` 触发。
 * 多张图片的 URL 用 `|||` 分隔。
 *
 * 为什么用独立 Activity 而非 WebView 内弹窗：
 * - `window.open` 在 Android WebView 中触发 `onCreateWindow`，返回新的 WebView。
 * - 独立 Activity 有完整的返回栈，用户按返回键可回到主聊天界面。
 * - 全屏黑色背景 + 横向滑动需要独立窗口。
 *
 * 注：子会话模式已于 2025-07 移除——前端 delegate/分叉/新窗口全部改为当前窗口内跳转，
 * 不再通过 `window.open` 打开子会话。
 */
class SubSessionActivity : AppCompatActivity() {

    private lateinit var webView: WebView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val imageUrl = intent.getStringExtra(EXTRA_IMAGE_URL) ?: ""

        // 透明状态栏，让图片查看器全屏沉浸
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP) {
            window.apply {
                statusBarColor = android.graphics.Color.TRANSPARENT
                decorView.systemUiVisibility = android.view.View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR
            }
        }

        // ── 图片查看模式 ──
        if (imageUrl.isNotEmpty()) {
            val urls = imageUrl.split("|||")
            val initialIdx = intent.getStringExtra(EXTRA_IMAGE_INDEX)?.toIntOrNull() ?: 0
            webView = WebView(this).apply {
                settings.apply {
                    javaScriptEnabled = true
                    useWideViewPort = true
                    loadWithOverviewMode = true
                }
                setBackgroundColor(android.graphics.Color.BLACK)
                // 构建横向滑动 HTML：每张图片占满视口，scroll-snap 对齐
                val imgs = urls.joinToString("") { u ->
                    """<img src="${u}" style="scroll-snap-align:center;width:100vw;height:100vh;object-fit:contain;flex-shrink:0" />"""
                }
                val html = """
                    <html><head><meta name="viewport" content="width=device-width,initial-scale=1.0">
                    <style>html,body{margin:0;background:#000;overflow:hidden}
                    .swiper{display:flex;overflow-x:auto;scroll-snap-type:x mandatory;-webkit-overflow-scrolling:touch}
                    .swiper::-webkit-scrollbar{display:none}
                    </style></head>
                    <body><div class="swiper" id="swiper">${imgs}</div>
                    <script>
                    (function(){var s=document.getElementById('swiper'),i=${initialIdx};
                    function tryScroll(){var c=s.children[i];if(c&&c.offsetWidth>0){c.scrollIntoView({inline:'center',block:'nearest',behavior:'instant'});return}requestAnimationFrame(tryScroll)}
                    tryScroll();
                    })();
                    </script>
                    </body></html>
                """.trimIndent()
                // baseURL=null：图片 URL 必须是绝对地址（相对 URL 无法解析）
                loadDataWithBaseURL(null, html, "text/html", "UTF-8", null)
            }
            setContentView(webView)
            return
        }
    }

    override fun onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack()
        } else {
            super.onBackPressed()
        }
    }

    override fun onDestroy() {
        super.onDestroy()
    }

    companion object {
        const val EXTRA_IMAGE_URL = "image_url"
        const val EXTRA_IMAGE_INDEX = "image_index"
    }
}
