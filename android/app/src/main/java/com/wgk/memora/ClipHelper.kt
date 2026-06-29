package com.wgk.memora

import android.content.ClipboardManager
import android.content.Context
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Handler
import android.os.Looper
import org.json.JSONObject

/**
 * 系统剪贴板 + 定位工具 —— 无状态的辅助函数集合。
 *
 * 剪贴板同步（v2.0）：
 * - `pushClipboard` 通过 WsManager.sendToAll 同时推送到 home 和 work，
 *   确保两台电脑都收到剪贴板内容。仅由用户手动操作触发（通知栏按钮 / 前端按钮）。
 */
object ClipHelper {

    fun pushClipboard(context: Context): String? {
        val cm = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val clip = cm.primaryClip ?: return null
        if (clip.itemCount == 0) return null
        val text = clip.getItemAt(0).text?.toString() ?: return null
        if (text.isEmpty()) return null
        WsManager.sendToAll(JSONObject().apply {
            put("type", "clipboard_push")
            put("content", text)
            put("timestamp", System.currentTimeMillis())
        })
        return text
    }

    /**
     * 获取 GPS 位置（异步版）—— 不阻塞调用线程。
     *
     * 为什么改成异步：旧版用 CountDownLatch.await() 阻塞 WebView JS 引擎线程等待
     * 定位结果，导致页面在等待期间完全卡死（无法滚动/点击）。现在改为纯回调模式，
     * 利用 Android LocationManager 自身的异步机制，完全不阻塞任何线程。
     *
     * 策略（不变）：
     * - 同时监听 GPS 和 Network provider，取精度最好的结果
     * - 精度 ≤ 15m → 立即返回
     * - 3 秒后仍没达标但有结果 → 取当前最佳返回（避免室内干等）
     * - 硬超时 12 秒兜底
     */
    fun getLocationAsync(context: Context, onResult: (JSONObject) -> Unit) {
        val lm = context.getSystemService(Context.LOCATION_SERVICE) as LocationManager
        var best: Location? = null
        var resolved = false  // 防重复回调（精度达标 + 超时可能竞速）

        val startTime = System.currentTimeMillis()
        val handler = Handler(Looper.getMainLooper())
        // 两步初始化（var + 后赋值）：lambda 内部需要引用 listener 自身，
        // val 在初始化右侧不可见，必须用 var 让 lambda 捕获变量引用。
        var listener: LocationListener? = null
        listener = LocationListener { loc ->
            if (resolved) return@LocationListener
            val cur = best
            if (cur == null || loc.accuracy < cur.accuracy) {
                best = loc
            }
            if (loc.accuracy <= 15f) {
                resolved = true
                removeUpdates(lm, listener!!)
                handler.removeCallbacksAndMessages(null)
                onResult(locationToJson(best!!))
            } else if (System.currentTimeMillis() - startTime > 3000 && best != null) {
                resolved = true
                removeUpdates(lm, listener!!)
                handler.removeCallbacksAndMessages(null)
                onResult(locationToJson(best!!))
            }
        }

        val hasGps = lm.isProviderEnabled(LocationManager.GPS_PROVIDER)
        val hasNet = lm.isProviderEnabled(LocationManager.NETWORK_PROVIDER)
        if (!hasGps && !hasNet) {
            onResult(JSONObject().apply { put("error", "GPS 和网络定位均已关闭") })
            return
        }

        try {
            if (hasGps) lm.requestLocationUpdates(
                LocationManager.GPS_PROVIDER, 1000, 0f, listener, Looper.getMainLooper()
            )
            if (hasNet) lm.requestLocationUpdates(
                LocationManager.NETWORK_PROVIDER, 1000, 0f, listener, Looper.getMainLooper()
            )
        } catch (e: SecurityException) {
            onResult(JSONObject().apply { put("error", "权限被拒绝") })
            return
        }

        // 硬超时 12 秒兜底
        handler.postDelayed({
            if (resolved) return@postDelayed
            resolved = true
            removeUpdates(lm, listener!!)
            if (best != null) {
                onResult(locationToJson(best!!))
            } else {
                onResult(JSONObject().apply { put("error", "定位超时") })
            }
        }, 12_000)
    }

    /** 将 Location 转为标准 JSON 结果 */
    private fun locationToJson(loc: Location): JSONObject {
        val typeStr = when (loc.provider) {
            LocationManager.GPS_PROVIDER -> "GPS"
            LocationManager.NETWORK_PROVIDER -> "网络"
            "fused" -> "融合定位"
            "passive" -> "被动定位"
            else -> loc.provider ?: "未知"
        }
        return JSONObject().apply {
            put("lat", loc.latitude)
            put("lng", loc.longitude)
            put("accuracy", loc.accuracy)
            put("type", typeStr)
        }
    }

    private fun removeUpdates(lm: LocationManager, listener: LocationListener) {
        try { lm.removeUpdates(listener) } catch (_: Exception) {}
    }
}
