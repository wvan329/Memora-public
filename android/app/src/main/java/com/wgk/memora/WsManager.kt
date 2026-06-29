package com.wgk.memora

import android.content.Context
import android.os.Handler
import android.os.Looper
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.json.JSONObject
import java.util.concurrent.TimeUnit

/**
 * 全局 WebSocket 单例 —— 管理 home + work 双连接。
 *
 * 双连接设计（v2.0）：
 * - 剪贴板同步、通知推送、APK 安装需要始终连通两台电脑，
 *   因此 init() 后即同时建立 home 和 work 两条全局连接。
 * - 侧边栏切换只影响「活跃设备」（activeDevice），
 *   会话 WS / WebView 加载的前端页面随之切换，
 *   但全局连接始终双连，不受影响。
 *
 * 设计决策：
 * - 使用 Kotlin `object`（饿汉单例）：Service 可被系统独立重启，
 *   单例跟随进程生命周期，重启后无需重新创建。
 * - `init(context)` 从 SharedPreferences 加载密码 + 活跃设备偏好，
 *   然后为 home 和 work 各建立一条连接。
 * - handlers 表全局共享：两条连接的 onMessage 都查询同一张表，
 *   订阅者只需 subscribe() 一次，即可接收两台电脑的消息。
 *
 * 重连策略（每条连接独立）：
 * - 指数退避 1s → 2s → 4s → ... → 上限 60s。
 * - `onClosed`/`onFailure` 中通过比较 webSocket 实例忽略旧连接回调。
 * - 每条连接各自维护 reconnectAttempt，互不干扰。
 *
 * 超时保护：
 * - uvicorn 每 30s 发 Ping → OkHttp 收到即重置 readTimeout 计时器。
 * - readTimeout=60s（2 倍安全裕度）。
 */
object WsManager {

    enum class State { CONNECTED, CONNECTING, DISCONNECTED }

    /** 当前活跃设备（侧边栏选中的），影响会话 WS 和 send() 默认目标 */
    @Volatile var activeDevice: String = "home"
        private set

    internal var password: String = ""
        private set

    /**
     * 消息订阅表（全局共享）。
     * Key: WebSocket 消息的 `type` 字段（如 "clipboard_sync"）或 `"*"` 通配。
     * Value: 订阅者回调列表。
     * `subscribe()` 返回取消订阅函数。
     */
    private val handlers = LinkedHashMap<String, MutableList<(JSONObject) -> Unit>>()

    private val client = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .pingInterval(15, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .build()
    private val mainHandler = Handler(Looper.getMainLooper())

    /**
     * 单条连接的状态封装。
     * 每条连接独立管理自己的 WebSocket 实例、连接状态和重连计数。
     */
    class DeviceConnection(val device: String) {
        @Volatile var state: State = State.DISCONNECTED
        var ws: WebSocket? = null
        lateinit var wsBase: String
        @Volatile var reconnectAttempt = 0
    }

    private val connections = mutableMapOf<String, DeviceConnection>()

    /**
     * 从 SharedPreferences 加载密码和活跃设备偏好，
     * 为 home 和 work 各创建一个 DeviceConnection（不建立连接）。
     * 连接由 SyncService.onCreate() 统一触发。
     * 幂等调用：MainActivity 和 SyncService 均可安全调用。
     */
    fun init(context: Context) {
        val prefs = context.getSharedPreferences("memora", Context.MODE_PRIVATE)
        password = prefs.getString("password", "") ?: ""
        activeDevice = prefs.getString("device", "home") ?: "home"

        // 已初始化过则跳过（保留已有连接）
        if (connections.isNotEmpty()) return

        for (device in listOf("home", "work")) {
            val conn = DeviceConnection(device)
            conn.wsBase = "wss://a.wgk-fun.top/$device/ws?conn=global"
            connections[device] = conn
        }
    }

    /**
     * 订阅指定 type 的 WebSocket 消息。
     * 双连接下，来自 home 或 work 的同 type 消息都会触发此回调。
     * @param type 消息 type 字段值，`"*"` 表示订阅全部消息
     * @param handler 回调（主线程执行）
     * @return 取消订阅的函数
     */
    fun subscribe(type: String, handler: (JSONObject) -> Unit): () -> Unit {
        val list = handlers.getOrPut(type) { mutableListOf() }
        list.add(handler)
        return { list.remove(handler) }
    }

    // ═══════════════════════════════════════════════════════════════
    // 发送方法
    // ═══════════════════════════════════════════════════════════════

    /** 发送 JSON 到活跃设备（侧边栏选中的那台）。
     *  NativeBridge.sendMessage（client_action_result 等）默认走此方法。 */
    fun send(obj: JSONObject) = sendToActive(obj)

    /** 发送字符串到活跃设备 */
    fun send(text: String) {
        val conn = connections[activeDevice] ?: return
        if (conn.state == State.CONNECTED) {
            conn.ws?.send(text)
        }
    }

    /** 发送 JSON 到所有已连接的设备。
     *  ClipHelper.pushClipboard / NativeBridge.sendClipboard 用此方法，
     *  确保手机剪贴板同时推送到 home 和 work。 */
    fun sendToAll(obj: JSONObject) {
        for ((_, conn) in connections) {
            if (conn.state == State.CONNECTED) {
                conn.ws?.send(obj.toString())
            }
        }
    }

    /** 发送 JSON 到活跃设备 */
    fun sendToActive(obj: JSONObject) {
        val conn = connections[activeDevice] ?: return
        if (conn.state == State.CONNECTED) {
            conn.ws?.send(obj.toString())
        }
    }

    /** 发送 JSON 到指定设备 */
    fun sendToDevice(device: String, obj: JSONObject) {
        val conn = connections[device] ?: return
        if (conn.state == State.CONNECTED) {
            conn.ws?.send(obj.toString())
        }
    }

    // ═══════════════════════════════════════════════════════════════
    // 连接管理
    // ═══════════════════════════════════════════════════════════════

    /**
     * 切换活跃设备 —— 只更新 activeDevice，不断开任何全局连接。
     * 侧边栏切换时通过 NativeBridge 调用。
     * 为什么不断开连接：全局连接（剪贴板/通知/APK）需要双连，
     * 侧边栏切换只影响 WebView 加载的前端页面和会话 WS。
     */
    fun switchDevice(device: String) {
        activeDevice = device
    }

    /**
     * 断开所有连接并阻止重连。
     * 将每条连接的 reconnectAttempt 设为极大值，scheduleReconnect() 检测后跳过。
     */
    fun disconnect() {
        for ((_, conn) in connections) {
            conn.reconnectAttempt = Int.MAX_VALUE
            conn.ws?.close(1000, "bye")
        }
    }

    /** 当前活跃设备的连接状态（供 NativeBridge.getStatus() 暴露给前端 JS） */
    val state: State
        get() = connections[activeDevice]?.state ?: State.DISCONNECTED

    /** 活跃设备的 WebSocket base URL（供 InstallHelper 推导下载 URL） */
    internal var wsBase: String
        get() = connections[activeDevice]?.wsBase ?: ""
        set(value) {
            connections[activeDevice]?.wsBase = value
        }

    /** 活跃设备的重连次数（供 MainActivity 读取） */
    val reconnectAttempt: Int
        get() = connections[activeDevice]?.reconnectAttempt ?: 0

    /** 建立指定设备的 WebSocket 连接。幂等调用。 */
    fun connect(device: String) {
        if (password.isEmpty()) return
        val conn = connections[device] ?: return
        if (conn.state == State.CONNECTING) return
        if (conn.state == State.CONNECTED) {
            // Service 重启后 state 可能是旧 CONNECTED（onDestroy 未被调用），
            // 此时 OkHttp WebSocket 已失效，需强制重连
            val old = conn.ws
            conn.ws = null
            old?.close(1000, "reconnect")
            conn.state = State.DISCONNECTED
        }
        conn.state = State.CONNECTING
        val url = "${conn.wsBase}&password=$password"
        val request = Request.Builder().url(url).build()
        // 捕获 conn 引用到闭包中，确保 onClosed/onFailure 中
        // `webSocket !== conn.ws` 比较的是当前连接实例
        val capturedConn = conn
        conn.ws = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                capturedConn.state = State.CONNECTED
                capturedConn.reconnectAttempt = 0
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                try {
                    val obj = JSONObject(text)
                    mainHandler.post {
                        val type = obj.optString("type", "")
                        handlers[type]?.forEach { it(obj) }
                        handlers["*"]?.forEach { it(obj) }
                    }
                } catch (_: Exception) {}
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                if (webSocket !== capturedConn.ws) return // 忽略旧连接回调
                capturedConn.state = State.DISCONNECTED
                scheduleReconnect(device)
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                if (webSocket !== capturedConn.ws) return // 忽略旧连接回调
                capturedConn.state = State.DISCONNECTED
                scheduleReconnect(device)
            }
        })
    }

    /**
     * 重连调度 —— 指数退避，保持当前设备不变。
     *
     * 为什么不在断开时自动切换设备：
     * 后端重启、网络抖动等临时断连不应触发设备切换。设备切换只在用户打开 App 时
     * 由 MainActivity.onResume() 检测到长时间未连接后执行，避免后台频繁 ping-pong。
     */
    private fun scheduleReconnect(device: String) {
        val conn = connections[device] ?: return
        if (conn.reconnectAttempt >= 100) return

        val delay = minOf(1000L * (1 shl conn.reconnectAttempt), 60_000L)
        conn.reconnectAttempt++
        mainHandler.postDelayed({ connect(device) }, delay)
    }
}
