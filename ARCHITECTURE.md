# Memora 项目架构

> **最后更新**：2026-07-04（v4.7 全景图 + 后端 DI 三层分层 + 前端流式解析/选字暂停/弹窗多实例 + Android 双WS/剪贴板v2/陷阱WebView）
> 本项目是一个跨设备 AI 助手：电脑端 Python 后端 + Vue 3 网页前端 + Android 原生壳。

---

## 一、全景图

```
                    ┌── 浏览器 ──────────┐
                    │  Vue 3 SPA          │
                    │  (frontend-vue/)    │
                    └──┬──┬──────────────┘
              wss://   │  │
                       │  │
┌─ 云服务器 (your-server-ip) ────────────────────────────────┐
│  nginx-proxy (80/443)                                  │
│    └── nginx → /home/ → frps:7002 → frpc → 本地 :8007  │
└────────────────────────────────────────────────────────┘
                       │
              wss://   │
                       ▼
┌─ 电脑端 (localhost:8007) ──────────────────────────────┐
│  main.py (FastAPI)                                     │
│    ├── /ws?conn=chat    → MessageRouter → Conversation  │
│    ├── /ws?conn=global  → MessageRouter → 手机通道      │
│    ├── /api/*           → http_routes.py                │
│    ├── /assets/*        → frontend-vue/dist/assets/    │
│    └── lifespan         → AppContainer 初始化            │
│                                                         │
│  AppContainer (DI 容器，三层分层)                       │
│    ├── L1 零依赖：MessageRepository, ToolRegistry,      │
│    │              SkillIndexManager, ToolIndexManager   │
│    ├── L2 依赖L1：SessionManager, ClientActionManager,  │
│    │              MobileConnectionPool                  │
│    └── L3 依赖L2：SubTaskRunner, ConversationTurn       │
│                   (工厂方法), MessageRouter              │
│                                                         │
│  后台守护任务：                                          │
│    ├── BrowserSessionManager (TTL 空闲回收)             │
│    ├── frpc_watchdog.py (Windows frpc 异常重启守护)     │
│    └── ReminderScheduler (事件驱动提醒 + 远眺)          │
└──────────────────────┬──────────────────────────────────┘
                       │ wss://
                       ▼
┌─ Android App ──────────────────────────────────────────┐
│  MainActivity      WebView 壳 + 定位权限 + 双机热备      │
│  SyncService       前台 Service + 剪贴板v2 + 通知        │
│  WsManager         WebSocket 单例 (home + work 双连接)   │
│  NativeBridge      JS ↔ Kotlin 桥接                    │
│  SubSessionActivity  纯HTML内嵌图片查看器                │
│  ClipboardActionActivity  上传/下载透明中转              │
│  ClipHelper        剪贴板推送 + 异步非阻塞GPS定位        │
│  InstallHelper     APK自下载安装 (OkHttp + FileProvider) │
└─────────────────────────────────────────────────────────┘
```

---

## 二、后端架构（v4.0 OOP 重构）

### 2.1 模块层级 + DI 容器三层分层

```
container.py (DI 容器，按依赖层级分层创建)
    │
    ├── L1 零依赖 ─────────────────────────────────────
    │   ├── repository.py      MessageRepository     (SQLite CRUD，零依赖)
    │   ├── tool_registry.py   ToolRegistry          (唯一的工具注册中心，零依赖)
    │   ├── tool_index.py      ToolIndexManager      (工具模块加载：shared/tool/ + local/tool/)
    │   ├── skill_index.py     SkillIndexManager     (技能索引+系统提示词)
    │   └── permissions.py     ContextVar 统一定义   (current_role / parent_queue / current_tool_call_id / browser_session_id)
    │
    ├── L2 依赖 L1 ───────────────────────────────────
    │   ├── session_manager.py SessionManager        (连接池+广播+队列消费+锁+run_turn)
    │   ├── client_action.py   ClientActionManager   (动作请求，依赖：parent_queue ctx)
    │   └── mobile_push.py     MobileConnectionPool  (手机全局连接池)
    │
    ├── L3 依赖 L2 ───────────────────────────────────
    │   ├── sub_task.py        SubTaskRunner         (子AI，依赖：L1+L2全部)
    │   ├── conversation.py    ConversationTurn      (对话，工厂方法注入到 MessageRouter)
    │   └── message_router.py  MessageRouter         (消息分发，依赖全部)
    │
    └── 独立组件 ──────────────────────────────────────
        ├── mcp_tool.py        MCP 接入层（预安装依赖 + 30s启动宽限期）
        ├── browser_session_manager.py  浏览器会话管理器（TTL 空闲自动回收）
        ├── frpc_watchdog.py   frpc 健康守护（仅 Windows，每2min检测 IP 变化异常重连）
        └── platform_utils.py  跨平台工具函数（进程管理、resolve_win_node_cmd 等）
```

**DI 容器三层设计原则**：

| 层级 | 约束 | 包含组件 |
|------|------|---------|
| L1 零依赖 | 不依赖任何 ai_agent 模块，纯数据/工具 | MessageRepository, ToolRegistry, SkillIndexManager, ToolIndexManager |
| L2 依赖 L1 | 只依赖 L1 组件，通过构造器注入 | SessionManager, ClientActionManager, MobileConnectionPool |
| L3 依赖 L2 | 依赖 L1+L2，通过工厂方法创建 ConversationTurn | SubTaskRunner, ConversationTurn(工厂), MessageRouter |

`ConversationTurn` 通过工厂方法 `_create_conversation(sid, prompt, role)` 创建（而非直接 `__init__`），每轮对话对应一个新实例。工厂方法由 `AppContainer` 定义，注入到 `MessageRouter`，`MessageRouter` 传给 `SessionManager.run_turn()` 调用。

### 2.2 关键类职责

| 类 | 文件 | 唯一职责 |
|----|------|---------|
| `MessageRepository` | `repository.py` | chat_messages + sub_sessions + session_meta 三表的 SQLite 操作 |
| `ToolRegistry` | `tool_registry.py` | 唯一的工具注册中心：@tool 装饰器 + MCP 注册 + 调用分发。旧全局变量 TOOLS/FUNC_TOOL_MAP/FUNC_SESSION_MAP 已删除 |
| `SkillIndexManager` | `skill_index.py` | YAML frontmatter 自动扫描生成 INDEX.md + 所有角色系统提示词加载（含折叠语法 `>-` 支持） |
| `ToolIndexManager` | `tool_index.py` | 工具模块双源加载：`shared/tool/`（公有）+ `local/tool/`（本机），对标 SkillIndexManager |
| `MobileConnectionPool` | `mobile_push.py` | 手机全局连接池（global 端点），封装 _clients 集合 |
| `SessionManager` | `session_manager.py` | 连接池、广播、chunk 缓存、队列消费、缓冲区（大小为一）、try_claim/release 锁、run_turn 对话循环。**通知完成回调由 MessageRouter 层注入**（`on_complete` 参数），SessionManager 不关心回调做什么 |
| `ClientActionManager` | `client_action.py` | 推送 client_action_request → 等待 client_action_result |
| `SubTaskRunner` | `sub_task.py` | 子 AI 批量委托：run_batch 引擎 → delegate_batch_start → 并行子对话 → **ContextVar copy-on-write 视图隔离** + 双推 text/reason。n=1 退化为单页弹窗 |
| `ConversationTurn` | `conversation.py` | 一轮对话：加载历史 → 按角色注入 system prompt → AI 调用 → 工具循环 → stream_end。**三层 abort 保护**（时刻A/B/C）+ Tool Placeholder 预写 |
| `MessageRouter` | `message_router.py` | 消息类型 → handler 注册表，取代 if-else 分发。注入 mobile_pool 管理手机连接；**通知完成回调定义在 MessageRouter 层**（`_notify_complete`），通过 `on_complete` 参数注入到 `run_turn` |
| `permissions.py` | `permissions.py` | 统一管理 4 个 ContextVar + 角色权限表（`RoleConfig`）：`main`/`delegate`/`browser_inner`/`compress` 四种角色，白名单+黑名单双层过滤 |
| `platform_utils.py` | `platform_utils.py` | 跨平台工具函数。含 **`resolve_win_node_cmd()`**：Windows 上绕过 .cmd 文件，用 `node.exe + cli.js` 直接调用以避开 cmd.exe 对 `&` `\|` `<` `>` 等特殊字符的解析 |
| `browser_session_manager.py` | `browser_session_manager.py` | **TTL 自动回收**：后台每 60s 扫描 daemon 目录，空闲超 10min 自动清理浏览器实例。mark_busy/mark_idle 状态标记 |
| `frpc_watchdog.py` | `frpc_watchdog.py` | 仅 Windows：每 2min 通过 frps Docker 日志检测 kwg 主机名异常重连次数，>2 次则 kill frpc.exe 并重启 |
| `AppContainer` | `container.py` | 创建所有对象并按三层依赖注入 |

### 2.3 ConversationTurn 的三层 Abort 保护

```
ConversationTurn.execute()
  │
  ├── _ai_call()        ← 流式 AI 调用阶段
  │   ├── 时刻 A：工具参数流式中被终止
  │   │   → 构造 tcs_data → 入库 asst_msg → 补 placeholder → fill_tool_results
  │   │
  │   └── 时刻 B：纯文本/思考被终止（无 tool_call）
  │       → 入库纯文本 asst_msg（content 可为空时用 reasoning 兜底）
  │
  └── _execute_tools()  ← 工具并行执行阶段
      └── 时刻 C：asst_msg 已入库，补缺失 tool_result
          → 仅更新仍为 pending 的 placeholder（已完成工具不覆盖）
```

**Tool Placeholder 预写**：`_save_tool_placeholders` 在 `asst_msg` 入库后立即为每条 `tool_call` 写入 `{"pending": true}` 占位符。工具完成后 `update_tool_result` 替换。这确保 crash/重启后 DB 中 `tool_calls` 与 `tool_result` 永远 1:1。

**CancelledError 精确捕获**：v4.3 修复——Python 3.9+ 中 `CancelledError` 继承 `BaseException` 而非 `Exception`。所有工具执行和内层调用改用 `except BaseException` 确保 abort 时正确传播。

### 2.4 SubTaskRunner 的 ContextVar Copy-on-Write 视图隔离

```
run_batch(items)  →  asyncio.gather(
  asyncio.create_task(_run_one(sid, task)) × N
)

_run_one() 内部：
  my_queue = asyncio.Queue()
  parent_queue_ctx.set(my_queue)  ← 覆盖 ContextVar

  asyncio.Task 创建时会 copy-on-write 复制父任务 ContextVar，
  此处 set 只影响当前 Task 及其子协程，其他并行 Task 互不干扰。
```

- 子 AI 的 `text`/`reason` chunk 通过 `consume_queue` 的**双推引擎**转发给父队列（携带 `session_uuid` + `tool_call_id`）
- 子 AI 的 `client_action_request` 推给自己队列 → 自己前端响应
- `browser_inner` 角色：`browser_session_id.set(sid)` → BrowserSessionManager 管理生命周期
- 每个子任务完成后立即发送 `sub_task_end` 到父队列，前端弹窗逐个子任务追加 ✅ 完成标记

### 2.5 WebSocket 协议

**聊天连接 (`/ws?conn=chat`)**

| type | 方向 | 说明 |
|------|------|------|
| `subscribe` | 客户端→服务端 | 订阅会话，返回 history + 实时缓存 + buffer_status |
| `chat` | 客户端→服务端 | 发送 AI 聊天消息（忙时自动写入缓冲区，不再返回 busy） |
| `abort` | 客户端→服务端 | 中断当前 AI 响应 + 取消 asyncio.Task（3s 宽限期等补入库） |
| `discard_buffer` | 客户端→服务端 | 仅清空缓冲区（不取消 AI 任务） |
| `history` | 服务端→客户端 | 历史消息批量推送 |
| `stream_end` | 服务端→客户端 | 流式输出结束 |
| `delegate_batch_start` | 服务端→客户端 | 子 AI 批量开始（含 session_uuid→页面索引映射，n=1 单页 n>1 多页） |
| `vision_images` | 服务端→客户端 | 视觉识别：图片缩略图（流式阶段提前展示） |
| `vision_chunk` | 服务端→客户端 | 视觉识别：流式文本 chunk（路由到对应工具卡片 result） |
| `buffer_status` | 服务端→客户端 | 缓冲区消息内容（subscribe 时推送，前端恢复待发送指示器） |
| `client_action_request` | 服务端→客户端 | 请求客户端能力（GPS/确认等） |
| `client_action_result` | 客户端→服务端 | 客户端能力结果 |
| `sub_task_end` | 服务端→客户端 | 单个子 AI 完成通知（携带 session_uuid，弹窗追加 ✅ 标记） |

**全局连接 (`/ws?conn=global`) — 手机专用**

| type | 方向 | 说明 |
|------|------|------|
| `clipboard_push` | 手机→PC | 手机剪贴板推送到电脑 |
| `clipboard_sync` | PC→手机 | 电脑剪贴板广播到手机 |
| `request_clipboard` | 手机→PC | 手动拉取 PC 剪贴板 |
| `push_notification` | PC→手机 | AI 推送的通知 |
| `install_apk` | PC→手机 | 触发 APK 自下载安装 |

### 2.6 HTTP 接口

| 端点 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 返回 `frontend-vue/dist/index.html`（Vue 3 构建产物） |
| `/api/login` | POST | 密码登录 |
| `/api/fork` | POST | 分叉会话（复制指定 turn 及之前所有消息到新会话） |
| `/api/download?path=...` | GET | 文件下载 |
| `/sessions` | GET | 最近会话列表（分页，含置顶 + 自定义标题） |
| `/sessions/{id}` | DELETE | 删除会话 |
| `/sessions/{id}/pin` | PUT | 切换置顶状态 |
| `/sessions/{id}/rename` | PUT | 重命名会话（空→清除） |
| `/turns/{sid}/{tid}` | DELETE | 删除某轮对话 |
| `/api/notification` | GET/POST | 手机通知开关（持久化 .env） |
| `/api/vision-high-res` | GET/POST | 图片识别高精度默认值 |
| `/api/browser-headed` | GET/POST | 浏览器是否显示窗口 |
| `/api/welcome` | GET | SSE 流式返回一句灵性话语（空会话封面，flash 模型） |
| `/api/debug/pool` | GET | 调试：MobileConnectionPool 连接列表 |

**新增工具**：`ai_delegate(tasks, session_uuids?)` — 统一子 AI 委托为批量接口（n≥1），`session_uuids` 与 `tasks` 一一对应支持上下文复用。`browser_task(tasks)` 同理。

### 2.7 数据存储

- **SQLite**：`Memora/ai_chat.db`，三张表：
  - `chat_messages` — 主消息表（含 `tool_calls`、`tool_call_id`、`reasoning_content`、`turn_id`）
  - `sub_sessions` — 子会话标记表（被标记的会话不出现在历史列表）
  - `session_meta` — 会话元数据表（`pinned` 置顶状态、`pinned_at` 置顶时间、`custom_title` 自定义标题）

- **Repository 模式**：`MessageRepository` 封装所有 DB 操作。`replace_session_messages` 在**同一事务中 DELETE + INSERT** 实现上下文压缩的原子替换。

- **会话分叉 API**（`POST /api/fork`）：找到目标 `turn_id` → 截取该轮及之前的所有消息 → 批量写入新 `session_id`。

### 2.8 角色系统提示词

系统提示词按角色拆分，`SkillIndexManager` 负责加载。

| 文件 | 角色 | 说明 |
|------|------|------|
| `system_prompt.txt` | `main` | 智能调度策略（交互确认 + 并行调研 + 并行委派）+ 编码铁律 + 技能索引 |
| `system_prompt_delegate.txt` | `delegate` | 委派子 AI：自主执行 + 假设标注 + 编码铁律 + 技能索引 |
| `system_prompt_browser.txt` | `browser_inner` | 浏览器子 AI：仅浏览器/文件/命令操作指引 |
| *(无)* | `compress` | 压缩子 AI 不需要系统提示词（无工具，仅做文本压缩） |

**技能索引**：`SkillIndexManager.build_index()` 扫描 `shared/skill/*/SKILL.md` 和 `local/skill/*/SKILL.md` 的 **YAML frontmatter**（`name` + `description`），自动生成 `INDEX.md`。支持 `>-` 折叠语法（多行 description）。生成后拼接到主 AI 和 delegate 子 AI 的系统提示词末尾。

### 2.9 MCP 接入层

| 特性 | 实现 |
|------|------|
| 预安装依赖 | `_pre_install(config)` 解析 `uvx --from` / `npx` 命令，在连接前 `pip install` / `npm install -g`，避免首次连接下载超时 |
| 30s 启动宽限期 | `register_mcp_tools()` 中 `asyncio.wait_for(..., timeout=30)`，超时后服务正常启动，后台继续重试 |
| 同名工具去重替换 | `registry.register_mcp_tool()` 内置去重逻辑：同名工具自动覆盖旧定义和旧 session，支持 MCP 重连 |
| Windows 兼容 | `_find_node_bin_dir()` 探测 fnm/nvm 的 node bin 目录并注入 PATH；`resolve_win_node_cmd()` 绕过 `.cmd`→`cmd.exe` 符号解析问题 |
| 黑白名单 | `mcp.json` 中 `black`/`white` 字段过滤工具（在 `_connect` 中 `pop` 避免传给底层） |
| mermaid 修正 | `outputType` 枚举强制 `["file", "svg"]`，默认 `"file"`，防止 base64 撑爆 context window |

### 2.10 服务自重启（Preflight 预验证模式）

```
schedule_restart 工具
  → 弹出确认框（client_action confirm）
  → 预验证：subprocess.run main.py MEMORA_PREFLIGHT=1
    → main.py __main__ 块检测到该环境变量
    → 执行 AppContainer.__init__ + init()（完整加载所有 tools 模块）
    → 但不绑定端口，验证通过 → print "[PREFLIGHT] OK" → sys.exit(0)
    → 验证失败 → 阻止重启，返回编译错误详情
  → 预验证通过 → 写入 restart_flag + 启动 _restart_agent.py 独立进程
    → kill 旧进程 → 轮询端口释放 → 重新启动 → 健康检查（轮询端口监听）
  → main.py lifespan 检测 .restart_flag → 发送"重启完成。"到原会话
```

**魔术字段协议**：`schedule_restart` 返回 `{"content": ..., "__restart__": True}`，`ConversationTurn._execute_tools` 中 `_detect_early_stop` 检测 `__restart__` 魔术字段 → 调用 `_handle_early_stop` 终止后续 AI 调用，避免 AI 在重启前继续输出。

### 2.11 浏览器会话管理器（TTL 自动回收）

```
BrowserSessionManager（模块级单例）
  ├── mark_busy(sid)   ← browser_inner 角色开始
  ├── mark_idle(sid)   ← browser_inner 角色结束
  └── _cleanup_loop()  ← 后台每 60s 执行：
      ├── 扫描 Playwright daemon 目录，发现未知 .session 文件 → 注册
      ├── 跳过 busy 会话
      └── idle_since > TTL_IDLE(10min) → _force_cleanup_browser + 移除
```

### 2.12 frpc 健康守护（仅 Windows）

`frpc_watchdog.py`：每 2 分钟通过 `ssh_exec` 执行 `docker logs frps --since 2m | grep -c 'hostname.*kwg'`，若异常重连次数 >2 则 `taskkill frpc.exe` → 重启 frpc。

### 2.13 通知完成回调的注入模式

`MessageRouter._on_chat` 中定义 `_notify_complete` 闭包，通过 `on_complete` 参数注入到 `SessionManager.run_turn()`。`SessionManager` 不感知通知逻辑——它只知道"缓冲区彻底空了后调用此回调"。回调内部检查通知开关后通过 `MobileConnectionPool.broadcast` 推送 `push_notification` 到手机。

---

## 三、前端架构（Vue 3 + TypeScript + Pinia + Tailwind CSS v4）

### 3.1 技术栈

| 项 | 值 |
|------|------|
| 框架 | Vue 3（Composition API + `<script setup>`） |
| 构建 | Vite 8 |
| 状态管理 | Pinia |
| 类型 | TypeScript（strict） |
| 样式 | Tailwind CSS v4（Vite 插件） + ~200 行自定义 CSS |
| Markdown | marked 18 |
| 包管理 | pnpm |

### 3.2 目录结构

```
frontend-vue/
├── index.html                  ← Vite 入口 HTML
├── vite.config.ts              ← 构建配置（base: './' 生产，'/' 开发）
├── tsconfig.json
├── src/
│   ├── main.ts                 ← createApp + createPinia + mount
│   ├── App.vue                 ← 顶层组装所有组件
│   ├── style.css               ← Tailwind 指令 + 滚动条 + 截断 + 暗色模式 + 手机端 CSS
│   │
│   ├── types/                  ← 类型层（零依赖）
│   │   ├── ws.ts               ← WebSocket 消息 discriminated union（20+ 种）
│   │   ├── chat.ts             ← ChatMessage、ToolItemState、SessionSummary
│   │   ├── ui.ts               ← Dialog、ContextMenu 等 UI 状态类型
│   │   └── env.d.ts            ← NativeBridge 等全局声明
│   │
│   ├── utils/                  ← 纯函数（零状态、零 DOM）
│   │   ├── uuid.ts             ← generateUUID
│   │   ├── api.ts              ← apiPath、buildWsUrl、authHeaders
│   │   ├── markdown.ts         ← marked 渲染、escapeHtml、formatTime、parseToolCalls
│   │   ├── streamJsonParser.ts ← **流式 JSON 增量解析器（189行手写状态机，5个导出函数）**
│   │   └── vision.ts           ← 图片文件选择器 + 压缩 + 上传
│   │
│   ├── stores/                 ← Pinia 状态（6 个 store）
│   │   ├── auth.ts             ← 登录密码、login/logout/401
│   │   ├── websocket.ts        ← WebSocket 连接/重连/指数退避/事件总线
│   │   ├── chat.ts             ← 核心：消息数组、流式 chunk、thinkBlocks、onChunk 回调、选字暂停
│   │   ├── ui.ts               ← 侧边栏、自动滚动、**弹窗多实例**、Toast、上下文菜单
│   │   ├── quote.ts            ← 划词引用标签 + DOM mark 高亮
│   │   └── sessions.ts         ← 会话列表 CRUD
│   │
│   ├── composables/            ← 可复用逻辑
│   │   ├── useStreamRenderer.ts← 消息分发（switch-case，和旧版结构一致）
│   │   ├── useBatchDelegate.ts ← 批量委托弹窗：routeChunk 三阶段路由 + syncPages 统一更新
│   │   ├── useAskUserStreaming.ts ← ask_user 流式弹窗
│   │   ├── useVisionStreaming.ts  ← vision_understand 流式弹窗
│   │   ├── useAutoScroll.ts    ← 智能滚动 + 跳回底部
│   │   └── useClientAction.ts  ← 客户端动作 + 选图压缩上传
│   │
│   └── components/             ← Vue SFC 组件树
│       ├── common/
│       │   ├── AppToast.vue
│       │   └── ScrollToBottom.vue
│       ├── overlay/
│       │   ├── LoginOverlay.vue
│       │   ├── AppDialog.vue   ← **多实例弹窗（v-for DialogEntry[] + minimize/restore）**
│       │   └── VisionOptionsDialog.vue
│       ├── sidebar/
│       │   ├── SessionItem.vue
│       │   ├── SessionList.vue
│       │   ├── AutoScrollToggle.vue
│       │   └── AppSidebar.vue
│       ├── chat/
│       │   ├── WelcomeHint.vue
│       │   ├── UserMessage.vue
│       │   ├── TextContent.vue
│       │   ├── ReasoningBlock.vue
│       │   ├── ToolCard.vue
│       │   ├── ImageCard.vue    ← 可复用图片卡片（缩略图 + 查看大图 + 保存）
│       │   ├── **ImageViewer.vue** ← **图片预览器（310行零依赖，多手势支持）**
│       │   ├── ThinkTools.vue
│       │   ├── AIMessage.vue
│       │   ├── SystemMessage.vue
│       │   ├── MessageList.vue
│       │   └── ChatView.vue
│       ├── input/
│       │   ├── QuoteTags.vue
│       │   └── InputArea.vue
│       └── context-menu/
│           └── ContextMenu.vue
```

### 3.3 核心设计：单一渲染数据源

**旧版架构（纯 JS）**：DOM 即状态，命令式操作。

**新版架构**：`chat.messages: DisplayMessage[]` 是唯一渲染数据源。
流式消息只是数组中一个 `isStreaming: true` 的普通消息。
chunk 到达 → `processChunk()` 直接 mutate 该消息 → Vue 响应式自动 patch DOM。

```
WebSocket.onmessage
  → useStreamRenderer.handle(msg)
    │
    │  ┌─ 子AI chunk（带 session_uuid）────────────────────┐
    │  │  ① batchDelegate.routeChunk(c)                     │
    │  │     三阶段兜底：sessionToBatch 映射 → batchStates   │
    │  │     暂存 → delegate toolBlock fallback              │
    │  │     返回 true → 跳过 processChunk（已路由到弹窗）    │
    │  │     返回 false → 走 processChunk（可能泄露）         │
    │  └───────────────────────────────────────────────────┘
    │
    │  ┌─ 选字暂停恢复 ────────────────────────────────────┐
    │  │  ② chat.flushPausedChunks()                        │
    │  │     内部调用 _routeChunk（由 useStreamRenderer      │
    │  │     通过 setRouteChunk 注入）→ 先路由再 processChunk │
    │  │     v4.6 修复：原先直接调 processChunk，子AI chunk   │
    │  │     绕过 routeChunk 泄露到 ToolCard 结果区           │
    │  └───────────────────────────────────────────────────┘
    │
    switch (msg.type):
      'history'       → chat.loadHistory() → messages = [...]
      'text'/'reason' → chat.processChunk() → 带 tool_call_id 的路由到对应工具 result，否则追加 msg.content / msg.thinkBlocks
      'tool_call_*'   → chat.processChunk() → msg.thinkBlocks 中增/改 ToolBlock
      'stream_end'    → chat.finishStreaming()
      ...
    ↓
  chat.messages (DisplayMessage[])  ← 单一渲染源
    ↓ v-for
  MessageList → UserMessage / AIMessage / SystemMessage
```

### 3.4 流式 JSON 增量解析器（`utils/streamJsonParser.ts`）

189 行手写状态机，5 个导出函数，零依赖：

| 函数 | 用途 |
|------|------|
| `extractBracedObject(s, start)` | 从指定位置提取完整闭合的 `{...}`，正确处理转义和嵌套 |
| `extractStringField(s, objStart, fieldName)` | 从对象起始位置提取字符串字段值，处理 `\"` `\\` `\n` 等转义 |
| `extractTaskFieldsFromPartial(buf)` | 从半成品 JSON 的 `tasks` 数组中逐对象提取 `task` 字段，用于**批量委托弹窗提前展示** |
| `extractPagesFromPartial(buf)` | 提取已完整到达的 `pages` 数组元素，每个 page 必须是完整闭合对象 |
| `extractPartialMessageFromLastPage(buf)` | 提取 `pages` 数组中最后一个未闭合 page 的 `message` 字段当前值，用于**流式逐字效果** |

**为什么手写**：LLM 输出无 token 边界，`stream-json` 等库不适用；只提取少数顶层字段，手工状态机 189 行完全可控。

### 3.5 选字感知暂停机制

```
用户选中文字（window.getSelection() 非空）
  → isUserSelecting() 返回 true
  → chunk 进入 pauseChunk() 暂存到 pausedChunks[]
用户释放选中
  → ContextMenu.vue 调用 flushPausedChunks()
  → 先过 setRouteChunk 注入的路由函数 → 再 processChunk
```

**v4.6 修复**：原先 `flushPausedChunks` 直接调用 `processChunk`，绕过了 `routeChunk`，导致子 AI chunk 泄露到 ToolCard 结果区。修复后通过 `setRouteChunk` 注入模式，store 层持有回调引用，flush 时先路由再处理。

### 3.6 弹窗多实例架构（v4.3）

```
uiStore.dialogs: DialogEntry[]  ← 独立实例列表
  ├── showDialog(state) → dialogId（页面索引号）
  ├── updateDialog(id, partial) → 更新指定弹窗
  ├── finalizeDialog(id) → 标记 resolved + 自动关闭
  ├── minimize(id) → 缩小到左下角
  ├── restore(id) → 从最小化恢复
  └── closeDialog(id) → 移除实例

App.vue → v-for="d in ui.dialogs" → <AppDialog :dialogId="d.id" />
```

- 每个弹窗拥有独立 `dialogId`，多个弹窗同时存在互不影响
- `showDialog` 返回 `dialogId` 供后续 `updateDialog`/`closeDialog` 操作
- 支持 **minimize/restore**：弹窗缩小为左下角浮动按钮，点击恢复

### 3.7 onChunk 同步回调

```
chat.ts:
  let _onChunk: (() => void) | null = null;
  function onChunk(fn: () => void) { _onChunk = fn; }
  每次 processChunk 后同步调用 _onChunk?.()

ChatView.vue setup 期注册 doScroll：
  chat.onChunk(() => { ... scrollToBottom ... })
```

**为什么用回调而非 Vue watcher**：watcher 触发时机有异步间隙，而回调在 state mutation 的同一调用栈中触发，和旧版 `scrollToBottom()` 同步调用行为一致。

### 3.8 ImageViewer 图片预览器（310 行零依赖）

`components/chat/ImageViewer.vue`：

| 特性 | 实现 |
|------|------|
| 全屏覆盖 | Teleport to body + fixed inset-0 + 黑色半透明背景 |
| 滚轮缩放 | 以鼠标位置为中心，`ZOOM_STEP=0.25`，范围 0.5x ~ 8x |
| 双击缩放 | scale=1 → 3x；scale>1 → 回到 1x |
| 鼠标拖拽 | scale>1 时可用鼠标拖拽平移 |
| 键盘导航 | Esc 关闭、← → 切换图片 |
| 双指缩放 | 触摸设备双指缩放 |
| 单指滑动 | scale=1 时左右滑动切换图片（SWIPE_THRESHOLD=80px，垂直移动过大则取消） |
| 底部指示器 | 多图时显示圆点指示器 |

被 `TextContent`、`ToolCard`、`InputArea`、`AppDialog` 四处复用。`ImageItem` 类型导出为公共接口。

### 3.9 多页弹窗的触摸滑动 + 鼠标拖拽 + 3D 卡片效果

`AppDialog.vue` 的多页模式：

- **CSS perspective 800px** + `rotateY` 实现 3D 翻页效果
- **触摸滑动**：`touchstart` → `touchmove` → `touchend`，计算偏移量切换页面
- **鼠标拖拽**：`mousedown` 记录起始点 → `mousemove` 实时跟踪偏移 → `mouseup` 判断切换方向
- 滚动内容区有 `scrollbar-thin` 自定义滚动条

### 3.10 乐观消息 + 待发送指示器双通道反馈

```
用户输入 → 立即渲染乐观消息（isOptimistic: true）
         → 发送 WebSocket chat 消息

路径 A：后端忙 → buffer_status 返回
       → 乐观消息保留 + pendingBufferMsg 指示器显示（"消息已排队…"）

路径 B：后端正常 → user_message 回执
       → handleUserMessage 替换乐观消息为正式消息（isOptimistic=false）
       → pendingBufferMsg 清除
```

**双通道**：乐观消息给用户"已发送"的即时反馈，待发送指示器告诉用户"后端正忙，消息在队列中"。

### 3.11 跨设备会话持久化策略

```
桌面端（多 Tab）：
  sessionStorage 优先（多 Tab 隔离，F5 恢复）
  localStorage 兜底（Tab 崩溃后仍可恢复）

手机端（WebView）：
  跳过 sessionStorage（WebView 行为不可靠）
  按设备分别存储：ai_last_session_home / ai_last_session_work
  home 和 work 各自独立，切换时自动恢复对应设备的最后会话

读取优先级（手机）：
  ai_last_session_{device} > URL s > URL sub > 新 UUID

读取优先级（桌面）：
  sessionStorage > ai_last_session > URL s > URL sub > 新 UUID
```

URL 参数 `s`/`sub` 仅在首次访问（无 localStorage 记录）时生效，之后由 `updateUrl()` 清理。

### 3.12 thinkBlocks 结构

```typescript
type ThinkBlock =
  | { type: 'reasoning'; text: string }
  | { type: 'tool'; key: string; item: ToolItemState }

// ToolItemState 关键字段
interface ToolItemState {
  name: string; args: string; result: string;
  resultType: 'json' | 'download' | 'image' | 'vision_result' | 'delegate';
  sessionUuid?: string;      // delegate 子会话 UUID（旧历史兼容）
  batchSessions?: Array<{     // delegate 批量模式：所有子会话信息
    session_uuid: string; index: number; task: string;
  }>;
  images?: VisionImage[];    // vision 工具流式阶段提前展示缩略图
  dialogId?: string;         // 关联的弹窗 ID（用于 restore）
  dialogType?: ToolDialogType; // 'ask_user' | 'confirm' | 'vision' | 'delegate'
}
```

### 3.13 Pinia Store 职责

| Store | 文件 | 核心职责 |
|-------|------|---------|
| `useAuthStore` | `auth.ts` | 密码管理、登录/登出、401 处理 |
| `useWebSocketStore` | `websocket.ts` | WebSocket 实例、连接状态机、重连（指数退避）、消息事件总线 |
| `useChatStore` | `chat.ts` | **核心**：`messages: DisplayMessage[]`、流式 chunk 处理、thinkBlocks、onChunk 回调、**选字暂停**、**乐观消息**、**会话持久化** |
| `useUIStore` | `ui.ts` | 侧边栏折叠、自动滚动、**对话框（`dialogs: DialogEntry[]` 独立实例列表，`showDialog()` 返回 dialogId，支持同时多个弹窗 + minimize/restore）**、Toast、上下文菜单 |
| `useQuoteStore` | `quote.ts` | 划词引用：DOM mark 高亮 + 标签 + scrollToQuote |
| `useSessionStore` | `sessions.ts` | 会话列表加载/删除/置顶/重命名（localStorage 持久化）、轮次删除、刷新 |

### 3.14 构建与部署

- **开发**：`pnpm dev` → `localhost:5173`，Vite proxy 转发 `/ws` `/api` 到 `localhost:8007`
- **生产构建**：`pnpm build` → `frontend-vue/dist/`，`base: './'` 使用相对路径，适配任意 nginx 前缀
- **服务端**：`main.py` 挂载 `/assets` → `frontend-vue/dist/assets/`，`/` 返回 `dist/index.html`
- **体积**：JS ~59KB gzip，CSS ~7KB gzip

---

## 四、Android 端架构

### 4.1 组件

| 组件 | 文件 | 职责 |
|------|------|------|
| `MainActivity` | `MainActivity.kt` | WebView 壳 + 密码验证 + 定位权限 + 双机热备自动故障转移 + `window.open` 陷阱WebView拦截 |
| `SyncService` | `SyncService.kt` | 前台 Service（`START_STICKY`）：WebSocket 双连接 + 剪贴板接收 + AI 推送通知 + APK 安装触发 |
| `WsManager` | `WsManager.kt` | WebSocket 单例 — **home + work 双连接**同时维护（`DeviceConnection` 内部类），共享 handlers 表 |
| `NativeBridge` | `NativeBridge.kt` | JS ↔ Kotlin 桥接：`sendMessage` / `getPassword` / `getLocation` / `sendClipboard` / `switchDevice` / `triggerInstall` |
| `SubSessionActivity` | `SubSessionActivity.kt` | **纯 HTML 内嵌图片查看器**（CSS `scroll-snap` 横向滑动） |
| `ClipboardActionActivity` | `ClipboardActionActivity.kt` | 通知栏按钮触发的上传/下载透明中转（转发到 MainActivity/SyncService） |
| `ClipHelper` | `ClipHelper.kt` | 剪贴板推送 + **异步非阻塞 GPS 定位**（双 Provider 择优 + 三级超时） |
| `InstallHelper` | `InstallHelper.kt` | **APK 自下载安装**（OkHttp + FileProvider，密码由手机端拼接） |

### 4.2 双 WebSocket 连接架构（v2.0）

```
WsManager（Kotlin object 单例）
  ├── connections: Map<String, DeviceConnection>
  │   ├── "home" → DeviceConnection(device="home")
  │   │   └── wsBase = "wss://a.wgk-fun.top/home/ws?conn=global"
  │   └── "work" → DeviceConnection(device="work")
  │       └── wsBase = "wss://a.wgk-fun.top/work/ws?conn=global"
  │
  ├── handlers: LinkedHashMap<String, List<(JSONObject) -> Unit>>  ← 全局共享
  │   → 两条连接的 onMessage 都查询同一张表
  │
  └── activeDevice: String  ← 仅影响 send() 默认目标和 WebView 加载页面
```

**设计决策**：
- 剪贴板同步、通知推送、APK 安装需要始终连通两台电脑 → `init()` 后即同时建立 home 和 work 两条连接
- 侧边栏切换只影响「活跃设备」，会话 WS / WebView 加载的前端页面随之切换
- 全局双连不受侧边栏影响，`sendToAll()` 同时推送到两台电脑
- 每条连接独立维护 `reconnectAttempt`，互不干扰

### 4.3 剪贴板同步 v2.0：架构性防回环

**v1.0 问题**：手机监听系统剪贴板 → 检测到变更即推送 → 如果正好是 PC 推过来的 → 回环。

**v2.0 方案**：从源头切断监听，改为手动触发。

```
电脑 → 手机：SyncService 订阅 "clipboard_sync" → 写入系统剪贴板（Toast "已复制"）
手机 → 电脑：仅由用户手动操作触发
  ├── 通知栏「上传」按钮 → ClipboardActionActivity → ClipHelper.pushClipboard()
  ├── 前端「上传剪贴板」按钮 → NativeBridge.sendClipboard()
  └── 两者都走 WsManager.sendToAll()，同时推送到 home 和 work
```

回环防护是架构性的——手机不再监听剪贴板变更，闭环路径被切断。

### 4.4 陷阱 WebView 模式（`onCreateWindow` 时序解耦）

```
前端 window.open('?image_view=...', '_blank')
  → WebView.onCreateWindow()
    → 创建陷阱 WebView（临时 WebView 接收跳转）
    → WebViewClient.shouldOverrideUrlLoading 拦截 URL
      → 检测 ?image_view= 参数 → startActivity(SubSessionActivity)
      → 或其他 URL → 系统浏览器兜底
    → 将陷阱 WebView 设置到 WebViewTransport → resultMsg.sendToTarget()
```

**为什么用陷阱 WebView**：`onCreateWindow` 要求返回 `WebViewTransport`，不能直接在此方法中 `startActivity`（会导致时序问题）。陷阱 WebView 接收跳转后再在 `shouldOverrideUrlLoading` 中解析参数。

### 4.5 APK 自下载安装

```
install_apk 消息到达 SyncService
  → InstallHelper.downloadAndInstall(context, path, password, url)
    → 子线程执行（不阻塞 UI）
    → 构造下载 URL：
      ├── 优先用消息中的完整 url + 手机端拼接 password
      └── 回退：从 wsBase 推导 httpBase + api/download?path=...&password=...
    → OkHttp GET 下载 APK 到 externalFilesDir
    → FileProvider 提供 content:// URI
    → Intent.ACTION_VIEW 触发系统安装界面
    → 安装后手动删除临时 APK
```

**安全性**：
- URL 带密码，中间件验证后才放行文件
- **密码由手机端拼接**（不从云服务器传输完整下载 URL），避免敏感信息经过中间节点
- 下载到沙箱目录（`externalFilesDir`），外部不可访问

### 4.6 异步非阻塞 GPS 定位

```
ClipHelper.getLocationAsync(context, onResult)
  ├── 同时监听 GPS Provider + Network Provider
  ├── 精度 ≤ 15m → 立即返回
  ├── 3 秒后仍没达标但有结果 → 取当前最佳返回（避免室内干等）
  └── 硬超时 12 秒兜底
```

**v2.0 改为纯异步**：旧版用 `CountDownLatch.await()` 阻塞 WebView JS 引擎线程（最长 12 秒），导致页面完全卡死。新版利用 `LocationListener` 自身的异步机制，完全不阻塞任何线程。结果通过独立通道 `window.__onLocationResult` 推送。

### 4.7 双机热备自动故障转移

**触发条件**：
- `onReceivedHttpError` 检测 502/503/504（nginx 返回 — 后端挂了）
- `onReceivedError` 网络层错误（DNS 失败、连接超时）
- 仅 `isForMainFrame` 触发（忽略子资源错误）

**转移逻辑**：
```
autoSwitchDevice()
  → 10 秒冷却检查（防死循环）
  → 切换 activeDevice（home ↔ work）
  → 更新 SharedPreferences
  → Toast "已自动切换到 $alt"
  → 重新加载 WebView
```

**v2.0 变更**：不再调用 `WsManager.switchDevice`（全局连接不受影响），仅更新 activeDevice + 重载 WebView。

### 4.8 纯 HTML 内嵌图片查看器

`SubSessionActivity` 加载横向滑动 HTML：

```html
<div class="swiper" style="display:flex; overflow-x:auto; scroll-snap-type:x mandatory">
  <img src="..." style="scroll-snap-align:center; width:100vw; height:100vh; object-fit:contain; flex-shrink:0" />
</div>
```

- **CSS scroll-snap** 实现滑动对齐
- **JS 延迟滚动**：`requestAnimationFrame` 检测图片加载完成后 `scrollIntoView`
- 多张图片 URL 用 `|||` 分隔
- 黑色全屏背景 + 返回键关闭

### 4.9 密码验证流程

```
首次启动（无 SharedPreferences 密码）
  → showPasswordDialog()：AlertDialog + EditText（密码遮挡）
    → 确定按钮 → 禁用按钮（防重复点击）
    → 子线程 HTTP POST api/login 验证
    → 成功：存入 SharedPreferences → onPasswordReady()
    → 失败：Toast "密码错误" → 重新弹出对话框
    → 取消按钮 / 按返回 → finish()

已有密码：
  → WsManager.init()  → 加载密码和活跃设备偏好
  → onPasswordReady() → 加载 WebView + 启动 SyncService
```

### 4.10 WebSocket 重连机制

```
WsManager.connect(device)
  ├── onOpen    → state=CONNECTED, reconnectAttempt=0
  ├── onMessage → 主线程分发 JSON → handlers[type] + handlers["*"]
  ├── onClosed  → 如果是当前连接实例 → scheduleReconnect() 指数退避 1s→2s→...→60s
  └── onFailure → 如果是当前连接实例 → scheduleReconnect()

关键修复 (2025-06-16)：
  SyncService 重启后 state 可能是旧的 CONNECTED（onDestroy 未调用），
  connect() 现在检测到 state==CONNECTED 时强制关闭旧连接并重连。
```

---

## 五、部署拓扑

```
用户浏览器
  │ https://a.wgk-fun.top/home/
  ▼
云服务器 (your-server-ip)
  │ nginx-proxy (Docker)
  │   ├── /home/       → frps:7002 → frpc → 本地 127.0.0.1:8007
  │   │   └── /home/assets/  → FastAPI /assets → frontend-vue/dist/assets/
  │   ├── /clipboard/ → clipboard:8010
  │   └── /miniprogram-api/ → miniprogram:8080
  │
  └── frps (Docker, 端口 7002)

电脑端
  ├── main.py (FastAPI, 端口 8007)
  │   ├── / → frontend-vue/dist/index.html
  │   └── /assets → frontend-vue/dist/assets/
  └── frpc
      ├── Windows: 桌面 启动frpc.vbs（手动双击）+ frpc_watchdog.py 健康守护
      └── macOS:   桌面 启动frpc.command 或 launchd plist（详见第六章）
```

---

## 六、frpc 内网穿透部署

frpc 负责将本地 8007 端口暴露到公网，通过云端 frps 中转，使 `https://a.wgk-fun.top/home/` 可以访问本地 AI 服务。

### 配置文件

`~/.frpc/frpc.toml`：
```toml
serverAddr = "a.wgk-fun.top"
serverPort = 7000
auth.method = "token"
auth.token = "你的frp-token"

[[proxies]]
name = "myai-mac"
type = "tcp"
localIP = "127.0.0.1"
localPort = 8007
remotePort = 7003
```

### macOS

**手动启动**：双击桌面的 `启动frpc.command`（或终端执行 `frpc -c ~/.frpc/frpc.toml`）。

### Windows

**手动启动**：双击桌面 `启动frpc.vbs`，后台静默运行。

```vbs
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Users\ths\Desktop\回收\内网穿透"
WshShell.Run "frpc.exe -c frpc.toml", 0, False
```

> VBS 文件必须用 **GBK** 编码保存，Windows 中文版 VBS 引擎不支持 UTF-8。

**健康守护**（仅 Windows）：`frpc_watchdog.py` 每 2 分钟检测 frps 日志中的异常重连次数，超过阈值则 `taskkill frpc.exe` 并重启。

---

## 七、电脑端服务管理

### 7.1 启动脚本

桌面 `启动frpc.command` 双击行为：

- **已运行** → 静默跳过，Terminal 窗口自动关闭
- **未运行** → 后台启动 frpc，2 秒确认成功后自动关窗
- **启动失败** → 窗口保持打开，显示错误日志路径

日志输出到 `Memora/.daemon_logs/frpc.log`。

### 7.2 main.py 启动

#### macOS

**手动启动**：
```bash
cd ~/Desktop/Memora && python main.py
```

#### Windows

**手动启动**：在项目目录下运行 `python main.py`。无开机自启。

### 7.3 在线重启机制（Preflight 预验证）

`schedule_restart` 工具（AI 可调用）用于无需 SSH 的在线重启：

```
schedule_restart
  → 弹出确认框（client_action confirm）
  → **预验证**：subprocess.run main.py MEMORA_PREFLIGHT=1
    → 完整加载所有 tools 模块（含 @tool 装饰器注册）
    → 不绑定端口 → "[PREFLIGHT] OK" → sys.exit(0)
    → 失败 → 阻止重启，返回错误详情，旧服务保持运行
  → 预验证通过 → 写入 .restart_flag（含当前 session_id）
  → 启动 shared/tool/_restart_agent.py（独立进程，start_new_session）
    → 查找占用 8007 端口的进程 → kill
    → 轮询确认端口释放
    → 重新启动 python main.py
    → 健康检查：轮询新进程监听 8007（10s 超时）
  → main.py lifespan 检测 .restart_flag
    → 发送「重启完成。」到原会话
```

重启日志：`Memora/.daemon_logs/restart_<uuid>.log`
