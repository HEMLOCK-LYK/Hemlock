<div align="center">

# 🐍 Hemlock

**AI Agent + 抓包融合安全测试工具**

把 AI 自主渗透测试 Agent 与 Burp Suite 式抓包代理，融合进一个桌面应用。

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.3.0-green.svg)](CHANGELOG.md)
[![Platform](https://img.shields.io/badge/platform-Windows%20x64-lightgrey.svg)](#)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)
[![Contact](https://img.shields.io/badge/Contact-1878093812%40qq.com-blue)](mailto:1878093812@qq.com)

</div>

---

## 这是什么

Hemlock 不是"抓包工具 + AI 插件"的简单拼装，而是**从零以 Agent 为核心设计的渗透测试工作流**。你只需要说一句：

> "帮我测试 https://xxx.com"

Agent 就会自主完成 **侦察 → 测试 → 验证 → 报告** 全流程：浏览流量、发起请求、分析响应、验证漏洞，过程中与你自然语言协作。抓包代理、Repeater、Intruder、漏洞管理全部在同一应用内闭环。

---

## 🏆 核心技术优势

### 1. ReAct 自主编排引擎（47 个内置工具）
- 工具链覆盖 **侦察 / 测试 / 验证 / 报告** 全阶段：`send_request`、`run_intruder`、`verify_vulnerability`、`report_finding`、`fuzz_paths`、`analyze_js`、`run_command`…
- 支持工具结果**自动化蒸馏**：超大输出压缩率 95%+，保留 `flow_id` 等关键引用，Agent 可按需深查，省 Token 不伤判断力
- 预留 200 次工具调用上限作为防死循环兜底，长流程测试（弱口令爆破等）不被硬切

### 2. 三级记忆上下文管理（Hot / Warm / Cold）
- **4K Token 热记忆预算**，自动蒸馏旧消息，长会话不爆上下文
- **动态上下文压缩**：每 5 步检查，超阈值自动触发，对齐 Claude Code 的 Microcompact 思路
- **文件化冷记忆持久化**：跨会话保留经验，Agent 越用越懂你的目标环境
- **LLM 驱动 AutoCompact**：用 LLM 生成结构化进展摘要接管旧上下文，失败自动回退，绝不让长任务断链

### 3. 多 Agent 协作（5 个内置角色）
- **侦察员 / 攻击手 / 验证员 / 情报员 / 报告员** 共享渗透测试上下文
- Coordinator 编排全流程，每个子 Agent 专职一块，像一支真实渗透小队

### 4. 漏洞证据链闭环
- 每个漏洞附带**原始请求/响应包**作为证据
- **三态语义判定漏洞验证器** + 7 种验证方法，区分真漏洞与误报
- 支持自动复测（retest）确认修复状态

### 5. 人在环安全控制
- 敏感操作（**DoS / 注入 / RCE / 数据删除**）强制人工确认，300s 超时自动拒绝
- 明确区分"Agent 能碰什么、必须你点头才碰什么"

### 6. 渐进式技能披露（98 个内置安全技能）
- system prompt 只注入技能名称 + 简短描述，完整指令按需加载
- 技能库覆盖：Web 漏洞（SQLi/XSS/SSRF/SSTI/反序列化…）、系统渗透（提权/横向/域渗透）、密码学、云原生、移动安全等

---

## 🎯 核心功能优势

| 功能 | 说明 |
|------|------|
| 🔍 **AI 自主渗透** | 自然语言驱动，四阶段自主执行，全程可中断、可干预 |
| 🕸️ **抓包代理** | mitmproxy 11，HTTP/HTTPS 解密，FlowTag 正则自动标注 |
| 🧪 **Repeater** | 请求重放与编辑，多标签页管理 |
| 💥 **Intruder** | 批量 payload 爆破，弱口令 / 参数 fuzz / WAF 绕过 |
| 🗂️ **漏洞管理** | 证据链记录、状态跟踪、一键复测 |
| 🌐 **信息收集** | FOFA / Quake / Hunter / ZoomEye 等 9 引擎并行搜索 |
| 🧩 **MCP 集成** | 外部 MCP 服务器工具动态注入，无限扩展 Agent 能力 |
| 🚀 **发现工具** | JS 接口提取、路径字典爆破，指令驱动式深挖攻击面 |
| 🎨 **多主题** | GitHub Dark/Light、Matrix、Burp Suite、Midnight Blue、Solarized |
| 🔌 **多 LLM 接入** | OpenAI / DeepSeek / Anthropic / 通义 / Gemini / Kimi / Ollama 等 |

---

## 快速开始

### 方式一：安装包（推荐）

1. 下载 `Hemlock_x.x.x_x64-setup.exe` 并安装
2. 设置页配置 LLM API Key（DeepSeek / OpenAI / 通义 等均可）
3. 启动抓包代理（默认 8080），浏览器配置代理并安装 mitmproxy CA 证书
4. 创建项目，开始抓包或与 Agent 对话

### 方式二：开发模式

```bash
# 后端
cd backend
python -m virtualenv .venv && .\.venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env   # 编辑配置 LLM API Key
python -m hemlock.cli

# 前端
cd frontend
npm install && npm run dev
```

- API：http://127.0.0.1:9876（文档 `/docs`）
- 抓包代理：127.0.0.1:8080
- 前端：http://localhost:5173

### 打包桌面应用

```bash
powershell -ExecutionPolicy Bypass -File build.ps1
```

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | FastAPI + uvicorn + SQLite + SQLAlchemy 2.0 |
| 抓包 | mitmproxy 11.0 |
| 桌面 | Tauri v2 (Rust) |
| 前端 | React + TypeScript + Vite |
| LLM | openai SDK（兼容 DeepSeek / 通义 / Kimi 等） |
| 实时通信 | WebSocket |

## 文档

- [技术文档](docs/hemlock-tech-doc.html)：系统架构、Agent 实现、渗透测试引擎（9 章）
- [部署指南](docs/deployment-guide.md)
- [变更日志](CHANGELOG.md)

## 测试

```bash
cd backend && .\.venv\Scripts\python.exe -m pytest -v
```

## 贡献

欢迎提交 Issue 和 PR，详见 [贡献指南](CONTRIBUTING.md)。

## 联系我们

有问题、建议或合作需求，欢迎联系：

📧 **邮箱**：[1878093812@qq.com](mailto:1878093812@qq.com)

## 免责声明

Hemlock 仅供安全研究和授权测试使用。使用者需确保已获得目标系统的合法授权，并遵守当地法律法规。对未经授权的测试行为，开发者不承担任何责任。

## License

[MIT](LICENSE)
