# Hemlock v0.3.1 Release Notes

## 中文说明

Hemlock 是一款**全栈一体化**渗透测试工具：AI Agent 自动渗透 + 人工验证闭环。小白友好、操作简单——配好 LLM Key 后说一句「帮我测试 https://xxx.com」，Agent 自主完成侦察、测试、验证、报告全流程；每个漏洞都带原始请求/响应证据，可随时人工复核。

### 核心特性

- **ReAct 自主编排引擎**：41 个内置工具，支持 "帮我测试 https://xxx.com" 式自然语言指令，Agent 按 侦察 → 测试 → 验证 → 报告 四阶段自主执行
- **工具按需加载**：核心工具始终可用，发现 / 文件类工具通过 `load_tools` 按需启用，降低每次请求的 Token 开销
- **攻击面发现工具**：`analyze_js` 从 JS 提取接口、`fuzz_paths` 路径字典爆破，指令驱动式深挖攻击面
- **N-day 漏洞情报**：`cve_lookup` 按组件+版本查已知 CVE（NVD + cve.circl.lu），`web_search` 联网搜利用细节；先思考后查证，需要时才走代理
- **上下文智能管理**：缓存前缀稳定 + 索引按需注入 + 渐进式压缩（Session Memory → LLM AutoCompact），长任务不爆上下文
- **checkpoint 断点续跑**：任务自动保存快照，崩溃或中断后可一键续跑
- **多 Agent 协作**：5 个内置角色（侦察员/攻击手/验证员/情报员/报告员）共享渗透测试上下文，Coordinator 编排全流程
- **渐进式技能披露**：98 个内置安全技能，system prompt 只注入索引，完整指令按需加载
- **漏洞证据链闭环**：每个漏洞附带原始请求/响应包证据，三态验证 + 自动复测
- **抓包代理**：mitmproxy 代理，FlowTag 正则标注，单击底部面板 / 双击浮动窗口查看数据包
- **多 LLM 接入**：OpenAI / DeepSeek / Anthropic / 通义千问 / Gemini / Kimi / 智谱 / 百川 / Ollama / 自定义
- **人在环安全控制**：敏感操作（DoS / 注入 / RCE / 数据删除）强制人工确认，300s 超时自动拒绝

### 安装方式

1. 下载 `Hemlock_0.3.1_x64-setup.exe`
2. 双击安装并启动
3. 进入设置页面配置 LLM API Key（模型建议 `deepseek-v4-flash`）
4. 启动抓包代理，浏览器配置代理并安装 CA 证书
5. 创建项目，开始使用

### 系统要求

- Windows 10/11 x64
- 4GB+ 内存
- 200MB 磁盘空间
- 需访问 LLM API 服务

---

## English

Hemlock is a full-stack integrated penetration testing tool: AI Agent autonomous pentesting with a human-verification loop. Beginner-friendly and simple — configure an LLM API key, say "test https://xxx.com", and the Agent autonomously completes recon → test → verify → report, while every finding carries raw request/response evidence for manual review.

### Key Features

- **ReAct Autonomous Orchestration Engine**: 41 built-in tools, supports natural language instructions like "test https://xxx.com", Agent executes Recon → Test → Verify → Report four-phase autonomous workflow
- **On-Demand Tool Loading**: core tools always available, discovery/file tools enabled via `load_tools` to reduce token overhead
- **Attack Surface Discovery**: `analyze_js` extracts endpoints from JS, `fuzz_paths` brute-forces paths
- **Smart Context Management**: stable cache prefix + index-based injection + progressive compaction (Session Memory → LLM AutoCompact)
- **Checkpoint / Resume**: tasks auto-save snapshots, resume after crash or interruption
- **Multi-Agent Collaboration**: 5 built-in roles (Recon/Attacker/Verifier/Intelligence/Reporter) sharing context, Coordinator orchestrates the workflow
- **Progressive Skill Disclosure**: 98 built-in security skills, only index injected into system prompt
- **Vulnerability Evidence Chain**: every finding includes raw request/response evidence, three-state verification + auto-retest
- **Packet Capture**: mitmproxy proxy, FlowTag regex annotation, click for bottom panel / double-click for floating window
- **Multi-LLM Support**: OpenAI / DeepSeek / Anthropic / Qwen / Gemini / Kimi / Zhipu / Baichuan / Ollama / Custom
- **Human-in-the-Loop Security Control**: sensitive operations (DoS / Injection / RCE / Data Deletion) require manual confirmation, 300s timeout auto-reject

### Installation

1. Download `Hemlock_0.3.1_x64-setup.exe`
2. Double-click to install and launch
3. Configure LLM API Key in Settings (model: `deepseek-v4-flash` recommended)
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
| Frontend | React + TypeScript + Vite |
| Packaging | PyInstaller (backend) + Tauri bundler (frontend) |

### 免责声明 / Disclaimer

Hemlock is for security research and authorized testing only. Users must ensure they have legal authorization for target systems and comply with local laws. The developers are not responsible for any unauthorized testing activities.
