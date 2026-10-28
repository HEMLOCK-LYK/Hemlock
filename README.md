<div align="center">

# 🐍 Hemlock

**全栈一体化 AI 渗透测试工具**

**Agent 自动渗透 + 人工验证闭环 —— 说句话就能测，证据齐全你说了算**



[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.9.0-green.svg)](CHANGELOG.md)
[![Platform](https://img.shields.io/badge/platform-Windows%20x64-lightgrey.svg)](#)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)
[![Contact](https://img.shields.io/badge/Contact-1878093812%40qq.com-blue)](mailto:1878093812@qq.com)

</div>

---

## 这是什么

Hemlock 把 **AI Agent 和 Burp 级抓包工具链放进同一个应用**：配好 LLM Key，说一句「帮我测试 https://xxx.com」，Agent 自主完成侦察、测试、验证、报告；每个请求都进抓包记录，每个漏洞都带原始证据，结论由你复核确认。

## 🚀 三步上手

1. **装**：下载安装包，双击安装（Windows x64）
2. **配**：设置页填入 LLM API Key（DeepSeek / OpenAI / 通义都行）
3. **测**：创建项目，说一句「帮我测试 https://xxx.com」

Agent 跑起来，你只需要看着它干活、在关键节点点头放行、最后复核结论。

---

## 核心特色

### 1. Agent 与抓包工具在同一个闭环里
- 抓包代理、拦截器、Repeater、Intruder、漏洞管理与 AI Agent 同一应用：流量右键即可把数据包发往重放器 / 爆破器 / AI 会话，人工调试和自动测试不用来回切工具
- 登录态一键迁移：把浏览器流量里的认证头迁给 AI 会话（凭据只进内存），会话被踢回登录页时 Agent 自动感知
- Agent 发出的每个请求都进流量列表——AI 干了什么，抓包记录里看得见、可复现

### 2. 漏洞证据链 + 人工验证闭环
- 每个漏洞附原始请求 / 响应证据，随时人工复核
- 规则验证 + 语义判定过滤误报，支持自动复测确认修复状态
- 敏感操作（DoS / 注入 / RCE / 数据删除）强制人工确认，全程可中断、可干预

### 3. 攻击链画布
- 测试过程自动串联成攻击路径（如 SQL 注入 → 写 webshell → RCE）
- 横向主线画布可视化，跨链总览一屏看完，确认后自动写进报告

### 4. 知识库
- 记忆分「长期记忆 / 项目记忆」两大类；知识库按类沉淀实战知识（绕 WAF 技巧 / nday 利用指南 / 0day 记录）
- 使用者在界面维护，Agent 在测试中沉淀的经验也会自动入库；库内检索按需取用

### 5. 长任务成本可控
- 上下文压缩阈值可调（200K～1M 预设 / 自定义），压缩结果固化、对话历史不重写、前缀缓存稳定命中
- 同一渗透任务跑 60 轮对比：常规做法累计输入约 26.4 元，Hemlock 约 0.16 元，详见 [docs/context-management-cost.md](docs/context-management-cost.md)

---

## 功能一览

| 功能 | 说明 |
|------|------|
| 🔍 **AI 自主渗透** | 自然语言驱动，侦察 → 测试 → 验证 → 报告自主执行 |
| 🕸️ **抓包代理** | mitmproxy 11，HTTP/HTTPS 解密，FlowTag 正则自动标注 |
| 🚧 **抓包拦截器** | 请求/响应双层拦截、所见即所得改包、Forward/Drop |
| 🧪 **Repeater** | 请求重放与编辑，多标签页管理 |
| 💥 **Intruder** | 批量 payload 爆破，支持导入 txt 字典、多标记位四种攻击模式 |
| ⛓️ **攻击链画布** | 漏洞串联可视化，跨链总览，自动进报告 |
| 🧠 **知识库** | 长期记忆 / 项目记忆，按库沉淀、Agent 共同维护 |
| 🗂️ **漏洞管理** | 证据链记录、状态跟踪、一键复测 |
| 🎯 **SRC 漏洞挖掘** | 赏金猎人工作流：种子队列 → 资产确认 → 单站打穿 → 落盘报告 |
| 🌏 **浏览器自动化** | 驱动本机 Chrome/Edge：JS 渲染页面、截图取证、登录态核查 |
| 🕳️ **N-day 情报** | 组件+版本查已知漏洞（cve_lookup）、联网搜利用细节（web_search） |
| 🌐 **信息收集** | FOFA / Quake / Hunter / ZoomEye 等 9 引擎并行搜索 |
| 🧩 **MCP 集成** | 外部 MCP 服务器工具动态注入 |
| 🔌 **多 LLM 接入** | OpenAI / DeepSeek / Anthropic / 通义 / Gemini / Kimi / Ollama 等 |
| 🎨 **多主题** | GitHub Dark/Light、Matrix、Burp Suite、Midnight Blue、Solarized |

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
- [上下文管理设计](docs/context-management-dev.md)
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

## 致谢

借鉴并致敬了 Burp Suite、Nuclei、mitmproxy、Claude Code 等优秀项目，详见 [致谢清单](ACKNOWLEDGMENTS.md)。

## 免责声明

Hemlock 仅供安全研究和授权测试使用。使用者需确保已获得目标系统的合法授权，并遵守当地法律法规。对未经授权的测试行为，开发者不承担任何责任。

## License

[MIT](LICENSE)
