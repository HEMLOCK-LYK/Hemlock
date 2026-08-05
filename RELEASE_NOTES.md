# Hemlock v0.2.0 Release Notes

## 中文说明

Hemlock 是一款将 AI Agent 能力与传统抓包代理深度融合的桌面安全测试工具。Agent 可以自主浏览流量、发起请求、分析响应、验证漏洞，全程通过自然语言与用户协作。

### 核心特性

- **ReAct 自主编排引擎**：30+ 渗透测试工具，支持 "帮我测试 https://xxx.com" 式自然语言指令，Agent 按 侦察 → 测试 → 验证 → 报告 四阶段自主执行
- **三级记忆上下文管理**：Hot/Warm/Cold Memory 架构，4K Token 热记忆预算，工具输出蒸馏压缩率 95%+，大幅降低 Token 消耗
- **动态上下文压缩**：每 5 步自动检查，超过 6K Token 触发压缩，确保多轮工具调用后仍保持高效决策
- **多 Agent 协作**：5 个内置角色（侦察员/攻击手/验证员/情报员/报告员）共享渗透测试上下文，Coordinator 编排全流程
- **渐进式技能披露**：system prompt 只注入技能名称和简短描述，完整指令按需加载，支持无限扩展技能库
- **漏洞证据链闭环**：AI 报告的每个漏洞包含原始请求/响应包作为证据，支持自动验证和复测
- **智能模型路由**：根据任务复杂度自动选择轻量/标准/强力模型，平衡成本与能力
- **抓包代理**：mitmproxy 代理，FlowTag 正则标注，单击底部面板 / 双击浮动窗口查看数据包
- **多 LLM 接入**：OpenAI / DeepSeek / Anthropic / 通义千问 / Gemini / Kimi / 智谱 / 百川 / Ollama / 自定义
- **人在环安全控制**：敏感操作（DoS / 注入 / RCE）需人工确认，300s 超时自动拒绝

### 安装方式

1. 下载 `Hemlock_0.2.0_x64-setup.exe`
2. 双击安装并启动
3. 进入设置页面配置 LLM API Key
4. 启动抓包代理，浏览器配置代理并安装 CA 证书
5. 创建项目，开始使用

### 系统要求

- Windows 10/11 x64
- 4GB+ 内存
- 200MB 磁盘空间
- 需访问 LLM API 服务

---

## English

Hemlock is a desktop security testing tool that deeply integrates AI Agent capabilities with traditional packet capture proxy. The Agent can autonomously browse traffic, send requests, analyze responses, and verify vulnerabilities, collaborating with users through natural language.

### Key Features

- **ReAct Autonomous Orchestration Engine**: 30+ pentest tools, supports natural language instructions like "test https://xxx.com", Agent executes Recon → Test → Verify → Report four-phase autonomous workflow
- **Three-Tier Memory Context Management**: Hot/Warm/Cold Memory architecture, 4K token hot memory budget, tool output distillation compression 95%+, significantly reducing token consumption
- **Dynamic Context Compression**: Auto-checks every 5 steps, triggers compression when exceeding 6K tokens, ensuring efficient decision-making after multiple tool calls
- **Multi-Agent Collaboration**: 5 built-in roles (Reconnaissance/Attacker/Verifier/Intelligence/Reporter) sharing penetration testing context, Coordinator orchestrates full workflow
- **Progressive Skill Disclosure**: System prompt injects only skill names and brief descriptions, full instructions loaded on-demand, supporting unlimited skill expansion
- **Vulnerability Evidence Chain**: Each AI-reported vulnerability includes original request/response packets as evidence, supports automatic verification and retesting
- **Intelligent Model Routing**: Auto-selects lightweight/standard/powerful models based on task complexity, balancing cost and capability
- **Packet Capture**: mitmproxy proxy, FlowTag regex annotation, click for bottom panel / double-click for floating window
- **Multi-LLM Support**: OpenAI / DeepSeek / Anthropic / Qwen / Gemini / Kimi / Zhipu / Baichuan / Ollama / Custom
- **Human-in-the-Loop Security Control**: Sensitive operations (DoS / Injection / RCE) require manual confirmation, 300s timeout auto-reject

### Installation

1. Download `Hemlock_0.2.0_x64-setup.exe`
2. Double-click to install and launch
3. Configure LLM API Key in Settings
4. Start proxy, configure browser proxy and install CA certificate
5. Create a project and start testing

### System Requirements

- Windows 10/11 x64
- 4GB+ RAM
- 200MB disk space
- Access to LLM API service

---

### 技术栈 / Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI + uvicorn + SQLite + SQLAlchemy 2.0 |
| Proxy | mitmproxy 11.0 |
| LLM SDK | openai (compatible with DeepSeek / Qwen / Kimi etc.) |
| Desktop | Tauri v2 (Rust) |
| Frontend | React 18 + TypeScript + Vite |
| Packaging | PyInstaller (backend) + Tauri bundler (frontend) |

### 免责声明 / Disclaimer

Hemlock is for security research and authorized testing only. Users must ensure they have legal authorization for target systems and comply with local laws. The developers are not responsible for any unauthorized testing activities.
