# Hemlock 部署指南

## 系统要求

| 项目 | 要求 |
|------|------|
| 操作系统 | Windows 10/11 (x64) |
| 内存 | 4GB 以上 |
| 磁盘空间 | 200MB（安装包）+ 数据存储空间 |
| 网络 | 需要访问 LLM API（如 DeepSeek、OpenAI 等） |

## 方式一：安装包部署（推荐）

### 1. 安装

下载 `Hemlock_0.3.0_x64-setup.exe`，双击运行，按照向导完成安装。

### 2. 首次启动配置

#### 2.1 配置 LLM API Key

启动应用后，进入「设置」→「LLM Provider 管理」，点击「添加 Provider」：

| 字段 | 说明 | 示例 |
|------|------|------|
| 名称 | 自定义标识 | DeepSeek |
| Provider | 服务商类型 | deepseek |
| API Key | 你的 API Key | sk-xxxxxxxx |
| Base URL | API 地址 | https://api.deepseek.com |
| 模型名 | 使用的模型 | deepseek-v4-flash |
| 设为默认 | 是否作为默认 Provider | 勾选 |

常见 Provider 配置：

| Provider | Base URL | 推荐模型 |
|----------|----------|----------|
| DeepSeek | https://api.deepseek.com | deepseek-v4-flash（非思考） |
| OpenAI | https://api.openai.com/v1 | gpt-4o-mini / gpt-4o |
| 通义千问 | https://dashscope.aliyuncs.com/compatible-mode/v1 | qwen-plus |
| Kimi | https://api.moonshot.cn/v1 | moonshot-v1-8k |
| 智谱 | https://open.bigmodel.cn/api/paas/v4 | glm-4-flash |
| Ollama (本地) | http://localhost:11434/v1 | qwen2.5:7b |

> DeepSeek API Base URL 必须使用 `https://api.deepseek.com`（不带 `/anthropic` 后缀），否则 OpenAI SDK 兼容性会出问题。

#### 2.2 启动抓包代理

进入「代理设置」页面：
1. 设置监听端口（默认 8080，可自定义）
2. 点击「启动代理」

#### 2.3 配置浏览器代理

以 Chrome 为例：
1. 打开设置 → 系统 → 打开代理设置
2. 设置 HTTP/HTTPS 代理为 `127.0.0.1:8080`
3. 或使用 SwitchyOmega 等浏览器插件管理代理

#### 2.4 安装 CA 证书（HTTPS 抓包）

浏览器访问 `http://mitm.it`，下载并安装 mitmproxy CA 证书到「受信任的根证书颁发机构」。

如果 `http://mitm.it` 无法访问，手动安装证书：
1. 证书文件位置：`~/.mitmproxy/mitmproxy-ca-cert.cer`
2. 双击文件 → 安装证书 → 本地计算机 → 受信任的根证书颁发机构

#### 2.5 创建项目

1. 启动时选择「立即开始」（临时项目，退出自动清理）或「新建项目」（保存到历史）
2. 临时项目适合快速测试，正式项目适合长期跟踪
3. 切换或关闭未保存的项目时会提示保存

### 3. 开始使用

#### 抓包模式
浏览器正常浏览目标网站，所有 HTTP/HTTPS 流量自动捕获到流量列表。

#### Agent 模式
在 Agent 对话框输入指令，例如：
- `帮我测试 https://example.com`
- `分析最近的流量，找找有没有安全问题`
- `对 /api/login 接口做 SQL 注入测试`

#### 敏感操作确认
Agent 执行批量攻击、系统命令、脚本执行等操作时，会弹出确认请求，需要手动批准。

## 方式二：开发环境部署

### 环境准备

需要安装以下工具：

| 工具 | 版本 | 下载地址 |
|------|------|----------|
| Node.js | 18+ | https://nodejs.org |
| Python | 3.10+ | https://python.org |
| Rust | stable | https://rustup.rs |
| cargo-tauri | 2.x | `cargo install tauri-cli` |

### 后端部署

```bash
cd backend

# 创建虚拟环境
python -m virtualenv .venv
.\.venv\Scripts\activate

# 安装依赖
pip install -e ".[dev]"

# 配置环境变量
cp .env.example .env
# 编辑 .env，至少配置 HEMLOCK_LLM_API_KEY

# 启动后端服务
python -m hemlock.cli
```

后端启动后：
- API 服务：http://127.0.0.1:9876
- API 文档：http://127.0.0.1:9876/docs
- WebSocket：ws://127.0.0.1:9876/ws/events

### 前端部署

```bash
cd frontend

# 安装依赖
npm install

# 开发模式启动
npm run dev
```

前端开发服务器启动在 http://localhost:5173，Vite 代理自动转发 API 请求到后端 9876 端口。

### 桌面应用打包

```bash
# 在项目根目录执行
powershell -ExecutionPolicy Bypass -File build.ps1
```

构建步骤：
1. 前端构建（npm run build）
2. 后端打包（PyInstaller）
3. 复制后端到 Tauri resources
4. Tauri 构建（cargo tauri build）
5. 输出安装包到 `src-tauri/target/release/bundle/`

可跳过部分步骤：
```bash
# 跳过后端打包（仅更新前端）
powershell -File build.ps1 -SkipBackend

# 跳过前端构建（仅更新后端）
powershell -File build.ps1 -SkipFrontend

# Debug 模式构建（更快，体积更大）
powershell -File build.ps1 -Debug
```

## 配置详解

### 环境变量配置

所有配置项通过环境变量或 `backend/.env` 文件设置，前缀 `HEMLOCK_`：

```env
# 基础配置
HEMLOCK_DEBUG=true
HEMLOCK_API_HOST=127.0.0.1
HEMLOCK_API_PORT=9876
HEMLOCK_PROXY_HOST=127.0.0.1
HEMLOCK_PROXY_PORT=8080

# 目标范围（逗号分隔，支持通配符）
HEMLOCK_TARGET_SCOPE=example.com,*.example.com

# 速率限制
HEMLOCK_RATE_LIMIT_PER_SECOND=10

# LLM 配置（也可在应用内配置）
HEMLOCK_LLM_API_KEY=your-api-key
HEMLOCK_LLM_BASE_URL=https://api.deepseek.com/v1
HEMLOCK_LLM_MODEL=deepseek-v4-flash

# 显式 prompt 缓存标记（仅对 claude- 前缀模型生效，DeepSeek/OpenAI 用自动前缀缓存，默认关闭）
HEMLOCK_LLM_CACHE_CONTROL_ENABLED=false

# 外部情报请求（cve_lookup / web_search）默认直连；直连失败时自动改用
# HEMLOCK_UPSTREAM_PROXY（工具「代理设置」页的上游代理），无需额外配置
# 也可通过 HTTP_PROXY / HTTPS_PROXY 环境变量指定默认代理

# 模型分层路由：按任务复杂度自动选择模型（留空则回退到 HEMLOCK_LLM_MODEL）
# HEMLOCK_LLM_MODEL_LIGHT=deepseek-v4-flash
# HEMLOCK_LLM_MODEL_POWERFUL=deepseek-v4-flash
# HEMLOCK_LLM_PROVIDER_LIGHT=
# HEMLOCK_LLM_PROVIDER_POWERFUL=
# HEMLOCK_MODEL_ROUTING_ENABLED=false

# Agent 行为
# HEMLOCK_AUTO_PENTEST=true                 # 一键自动化渗透开关
# HEMLOCK_LLM_ASSISTED_VERIFY_ENABLED=false # 规则验证不确定时追加 LLM 分析（额外耗 token）
# HEMLOCK_SENSITIVE_OP_CONFIRM=true         # 敏感操作人工确认
```

### 文件化配置

Hemlock 的 Agent 角色、技能、长期记忆均基于 Markdown 文件管理，存储在应用数据目录：

- **打包版**：`%APPDATA%\com.hemlock.app\`
- **开发模式**：`%APPDATA%\Hemlock\`（或通过 `HEMLOCK_DATA_DIR` 环境变量指定）

```
├── hemlock.db              # SQLite 数据库
├── active_project.json     # 活跃项目 ID
├── workspace/              # Agent 命令/脚本执行目录
├── agents/                 # Agent 角色定义
│   ├── scout.md            # 侦察员
│   ├── striker.md          # 攻击手
│   ├── auditor.md          # 验证员
│   ├── radar.md            # 情报员
│   ├── scribe.md           # 报告员
│   └── custom-*.md         # 自定义角色
├── skills/                 # 技能文件（首次启动自动从内置库复制 98 个）
│   └── *.md
├── memories/               # 长期记忆
│   ├── global/             # 全局记忆
│   └── projects/           # 项目级记忆
│       └── {project_id}/
└── logs/                   # 日志文件
    ├── hemlock.log         # 应用日志（保留 7 天）
    └── security.log        # 安全日志（保留 30 天）
```

### Agent 角色自定义

Agent 角色文件格式（Markdown + YAML frontmatter）：

```markdown
---
name: my-agent
label: 我的Agent
description: 自定义渗透测试角色
category: custom
is_builtin: false
---

# 我的 Agent

你是一个专注于 XX 漏洞测试的 Agent...

## 工作流程
1. ...
2. ...

## 输出要求
- ...
```

将文件保存到 `~/.hemlock/agents/` 目录即可自动加载。

## 常见问题

### 抓不到包

1. **检查代理是否启动**：在应用内代理设置页面确认代理状态为「运行中」
2. **检查浏览器代理配置**：确保浏览器代理指向 `127.0.0.1:8080`
3. **HTTPS 证书问题**：确认已安装 mitmproxy CA 证书到受信任根证书
4. **端口冲突**：如果 8080 端口被占用，在设置中修改代理端口
5. **防火墙拦截**：检查 Windows 防火墙是否放行了 9876 和 8080 端口

### Agent 不工作

1. **检查 LLM API Key**：在 LLM Provider 管理页面确认 API Key 有效
2. **检查 Base URL**：DeepSeek 必须使用 `https://api.deepseek.com`（不带后缀）
3. **检查网络**：确保能访问 LLM API 地址
4. **查看日志**：`~/.hemlock/logs/hemlock.log` 查看错误详情

### API Error: project not found

后端重启后活跃项目丢失。解决方案：
- 重新选择一个项目，或在启动时选择「立即开始」创建临时项目
- 后端已实现启动时自动恢复活跃项目，如仍出现请检查 `~/.hemlock/active_project.json`

### 流量通过但列表无记录

检查是否有活跃项目。流量入库时需要 `project_id`，如果没有活跃项目，流量不会被记录。确保在启动后先选择或创建一个项目。

### 端口冲突

如果 9876（API）或 8080（代理）端口被占用：

```bash
# 查看端口占用
netstat -ano | findstr :9876
netstat -ano | findstr :8080

# 终止占用进程（替换 PID）
taskkill /PID <PID> /F
```

或在 `.env` 文件中修改端口号。

### 构建失败

1. **前端构建失败**：检查 TypeScript 编译错误，运行 `cd frontend && npx tsc --noEmit`
2. **后端打包失败**：确保所有 Python 依赖已安装，`pip install -e .`
3. **Tauri 构建失败**：确保 Rust 和 cargo-tauri 已正确安装

## 性能建议

- **LLM 模型选择**：简单 Q&A 和标题生成使用轻量模型（如 deepseek-v4-flash 非思考模式），漏洞分析使用强模型（如 deepseek-v4-flash 思考模式）
- **上下文管理**：三级记忆架构自动管理 Token 预算，长时间任务无需手动清理
- **流量过滤**：杂包检测自动过滤心跳包和静态资源，减少干扰
- **数据库维护**：定期清理旧流量数据，`~/.hemlock/hemlock.db` 文件会随使用增长
