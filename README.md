<div align="center">

# 🐍 Hemlock

**全栈一体化 AI 渗透测试工具**

**Agent 自动渗透 + 人工验证闭环 —— 说句话就能测，证据齐全你说了算**



[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.4.2-green.svg)](CHANGELOG.md)
[![Platform](https://img.shields.io/badge/platform-Windows%20x64-lightgrey.svg)](#)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)
[![Contact](https://img.shields.io/badge/Contact-1878093812%40qq.com-blue)](mailto:1878093812@qq.com)

</div>

---

## 这是什么

Hemlock 不是"抓包工具 + AI 插件"的简单拼装，而是**从零以 Agent 为核心设计的全栈一体化渗透测试工具**：

- **对小白友好**——不需要先懂渗透，装好、配好 LLM Key、说一句「帮我测试 https://xxx.com」，Agent 就自动完成 **侦察 → 测试 → 验证 → 报告** 全流程
- **对专业不妥协**——Agent 全程可中断、可干预、可深查；抓包代理、Repeater、Intruder、漏洞管理都在同一应用内闭环
- **人工验证闭环**——每个漏洞都带原始请求/响应证据，规则验证 + 三态语义判定帮你过滤误报，关键结论随时可人工复核，**AI 不背锅、你说了算**

## 🚀 三步上手

1. **装**：下载安装包，双击安装（Windows x64）
2. **配**：设置页填入 LLM API Key（DeepSeek / OpenAI / 通义都行）
3. **测**：创建项目，说一句「帮我测试 https://xxx.com」

Agent 跑起来，你只需要看着它干活、在关键节点点头放行、最后复核结论。

---

## 🏆 核心技术优势

### 1. ReAct 自主编排引擎（43 个内置工具）
- 工具链覆盖 **侦察 / 测试 / 验证 / 报告** 全阶段：`send_request`、`run_intruder`、`verify_vulnerability`、`report_finding`、`fuzz_paths`、`analyze_js`、`run_command`…
- **N-day 漏洞情报**：识别出组件+版本（如 nginx 1.18.0）可 `load_tools` 后查已知漏洞（`cve_lookup`）+ 联网搜利用细节（`web_search`）——**先思考后查证**，大多数测试靠推理不联网
- **工具按需加载**：30 个核心工具（HTTP 主循环）始终注入，14 个发现 / 文件类工具（含 cve_lookup/web_search）通过 `load_tools` 按需启用——用户说一句话不会全量塞满工具定义
- 支持工具结果**自动化蒸馏**：超大 HTTP 输出按需截断 + 摘要压缩，保留 `flow_id` 等关键引用，Agent 可随时深查，省 Token 不伤判断力
- 预留 200 次工具调用上限作为防死循环兜底，长流程测试（弱口令爆破等）不被硬切

### 2. 上下文智能管理（缓存友好的 system 拆分 + 索引 + 渐进式压缩）
- **缓存友好的 system 拆分**：system prompt 拆为「稳定段 + 动态段」两条消息——稳定段（角色设定 + 渗透策略）逐字节不变，位于缓存断点之前，命中 Anthropic 显式断点 / DeepSeek-OpenAI 自动前缀缓存；动态段（技能索引 + MCP 列表 + 长期记忆）位于断点之后，改动不影响稳定前缀
- **确定性索引排序**：技能 / 记忆索引按名称确定性排序，集合不变时注入文本逐字节一致——「没变就是没变」，缓存不会因排列不同而失效
- **记忆放在 system 内、断点之外**：长期记忆留在 system（auto-compact 永不压缩），又位于缓存断点之后（技能/记忆增删不影响缓存命中）
- **索引 + 按需**：技能 / 记忆只向 Agent 注入「名称 + 描述」索引，完整内容按需读取（`get_memory` / `invoke_skill`）
- **渐进式压缩**：Session Memory 免费摘要 → LLM 驱动 AutoCompact → 确定性压缩兜底，熔断自动回退，绝不让长任务断链
- **checkpoint 断点续跑**：任务每 3 步自动保存快照，崩溃后重跑同一任务自动从断点续跑

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

### 6. 渐进式技能披露（99 个内置安全技能）
- system prompt 只注入技能名称 + 简短描述，完整指令按需加载
- 技能库覆盖：Web 漏洞（SQLi/XSS/SSRF/SSTI/反序列化…）、系统渗透（提权/横向/域渗透）、密码学、云原生、移动安全等

---

## 🎯 核心功能优势

| 功能 | 说明 |
|------|------|
| 🔍 **AI 自主渗透** | 自然语言驱动，四阶段自主执行，全程可中断、可干预、可续跑 |
| 🕸️ **抓包代理** | mitmproxy 11，HTTP/HTTPS 解密，FlowTag 正则自动标注 |
| 🧪 **Repeater** | 请求重放与编辑，多标签页管理 |
| 💥 **Intruder** | 批量 payload 爆破，弱口令 / 参数 fuzz / WAF 绕过 |
| 🗂️ **漏洞管理** | 证据链记录、状态跟踪、一键复测 |
| 🌐 **信息收集** | FOFA / Quake / Hunter / ZoomEye 等 9 引擎并行搜索 |
| 🧩 **MCP 集成** | 外部 MCP 服务器工具动态注入，无限扩展 Agent 能力 |
| 🚀 **发现工具** | JS 接口提取（analyze_js）、路径字典爆破（fuzz_paths），指令驱动式深挖攻击面 |
| 🕳️ **N-day 情报** | 组件+版本查已知漏洞（cve_lookup），联网搜利用细节（web_search），历史漏洞不靠猜 |
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
