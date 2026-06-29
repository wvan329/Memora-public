# Memora - Android 项目

基于 Memora WebSocket 的 Android 剪贴板实时同步 + AI 聊天客户端。

## 功能
- 手机 ↔ 电脑剪贴板实时双向同步
- WebView 加载 AI 聊天界面（复用现有前端）
- 前台 Service 保持 WebSocket 长连接
- 通知栏常驻，App 关闭后同步不中断

## 构建
1. 用 Android Studio 打开本目录
2. 修改 `WsManager.kt` 中的 `WS_URL` 和 `WS_PASSWORD`
3. Build → Generate APK

## 文件结构
```
app/
├── build.gradle.kts
└── src/main/
    ├── AndroidManifest.xml
    └── java/com/wgk/memora/
        ├── App.kt              ← Application，初始化
        ├── MainActivity.kt     ← WebView 壳 + JSI 桥接
        ├── SyncService.kt      ← 前台 Service + 剪贴板监听
        ├── WsManager.kt        ← WebSocket 单例 + 消息总线
        └── NativeBridge.kt     ← JS ↔ 原生桥接接口
```
