# Memora

> 跨设备 AI Agent 框架 — 从零构建的通用 Agent 基础设施。电脑端 Python 后端 + Vue 3 网页前端 + Android 原生客户端。

## 简介

Memora 是一个通用的 AI Agent 框架，核心能力：

- **自研工具调用框架**：@tool 装饰器，基于 Pydantic 类型推导自动生成 Function Calling Schema
- **MCP 协议接入**：支持 stdio / SSE / streamable HTTP 三种传输模式
- **多 Agent 编排**：主 AI 可批量并行启动子 AI 独立执行任务
- **跨设备同步**：Android 原生客户端，双 WebSocket 长连接，剪贴板双向同步
- **技能可编程**：通过 SKILL.md 编写可复用技能，AI 自动索引加载

## 电脑端部署

### 环境要求

- **Python 3.11+**（推荐 3.13）
- **Windows** 或 **macOS**
- （可选）Node.js 18+ — 仅开发前端时需要，使用预构建产物则不需要

### 1. 克隆仓库

```bash
git clone https://github.com/wvan329/Memora-public.git
cd Memora-public
```

### 2. 安装 Python 依赖

```bash
pip install -r requirements.txt
```

### 3. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`，必填字段：

| 字段 | 说明 | 示例 |
|------|------|------|
| `API_KEY` | 大模型 API Key | `sk-xxx` |
| `BASE_URL` | 模型 API 地址 | `https://api.deepseek.com` |
| `ACCESS_PASSWORD` | 访问密码 | 自己设置 |

可选字段：阿里云 API、SSH 凭据、邮件通知等（详见 `.env.example` 注释）。

### 4. 启动服务

```bash
python main.py
```

启动后访问 `http://localhost:8007`，输入你设置的 `ACCESS_PASSWORD` 即可使用。

## 前端开发（可选）

前端已预构建在 `frontend-vue/dist/`，直接可用。如需修改前端源码：

```bash
cd frontend-vue
pnpm install
pnpm dev          # 开发模式，localhost:5173
pnpm build        # 生产构建 → dist/
```

## Android 客户端

### 构建 APK

1. 安装 **Android Studio**
2. 用 Android Studio 打开 `android/` 目录
3. 等待 Gradle 同步完成
4. Build → Build APK
5. APK 文件在 `android/app/build/outputs/apk/debug/`

### 安装到手机

- USB 连接手机，开启 USB 调试，`adb install` 安装
- 或直接传 APK 到手机手动安装

### 手机端配置

1. 打开 App，输入 `ACCESS_PASSWORD` 验证
2. 自动连接 `wss://你的域名/home/ws`（需 frp 公网部署）
3. 连接成功后可双向同步剪贴板、接收 AI 推送通知

## 公网访问（frp 内网穿透）

要让外网访问本地 AI 服务，需要配置 frp。

### 前提

- 一台有公网 IP 的云服务器（已安装 frps）
- 域名 DNS 解析到该服务器

### 配置 frpc

在本地电脑创建 `frpc.toml`：

```toml
serverAddr = "你的服务器地址"
serverPort = 7000
auth.method = "token"
auth.token = "你的frp-token"

[[proxies]]
name = "memora"
type = "tcp"
localIP = "127.0.0.1"
localPort = 8007
remotePort = 7003
```

### 启动 frpc

**Windows**：下载 [frp](https://github.com/fatedier/frp/releases)，解压后：
```bash
frpc.exe -c frpc.toml
```
建议创建 `.vbs` 脚本双击静默启动：
```vbs
Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "frp解压目录"
WshShell.Run "frpc.exe -c frpc.toml", 0, False
```
> ⚠️ VBS 文件需用 **GBK** 编码保存。

**macOS**：
```bash
frpc -c ~/.frpc/frpc.toml
```
建议用 `launchd` 或创建 `.command` 文件双击启动。

### 云服务器端（frps）

确保 frps 已运行并暴露对应端口，nginx 配置反向代理到 frps 端口。

## 技能系统

项目有两类技能，启动时自动扫描：

| 目录 | 说明 |
|------|------|
| `shared/skill/` | 公有技能，随 git 分发 |
| `local/skill/` | 本机独有，不提交 git |

参考 `shared/skill/write-skill/SKILL.md` 了解如何编写技能。

## 目录结构

```
Memora-public/
├── main.py                    ← FastAPI 入口
├── ai_agent/                  ← 核心引擎
├── shared/tool/               ← 公有工具（AI 可调用）
├── shared/skill/              ← 公有技能
├── local/                     ← 本机独有（不提交 git，可放 local/skill/、local/tool/）
├── frontend-vue/              ← Vue 3 前端源码 + dist
├── android/                   ← Kotlin Android 源码
├── ARCHITECTURE.md            ← 项目架构全景
├── mcp.json                   ← MCP 工具配置
└── .env.example               ← 环境变量模板
```

## 隐私

- `.env` 已被 `.gitignore` 忽略，不会被提交
- `local/` 目录被整体忽略，放你的私有技能和配置
- `mcp.json` 中的工具配置可自由修改

## License

MIT
