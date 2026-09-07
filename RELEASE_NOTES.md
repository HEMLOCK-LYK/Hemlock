# Hemlock v0.6.0 Release Notes

## 本版本亮点 / What's New

- **SRC 漏洞挖掘模式**：新增独立开关，Agent 按赏金猎人工作流挖洞，默认关闭
- **浏览器自动化**：Agent 可驱动本机 Chrome/Edge，渲染页面、截图取证、核查登录态
- **命令执行/文件管理增强**：工作目录、后台运行、分页读取、列目录
- **上游代理对 Agent 生效**：Agent 请求、命令、浏览器均可走上游代理
- **报告格式全局可用**：写报告前可加载完整格式规范
- **修复**：测试中途切换页面不再导致测试自动结束、历史丢失

## 中文说明

Hemlock 是一款**全栈一体化**渗透测试工具：AI Agent 自动渗透 + 人工验证闭环。小白友好、操作简单——配好 LLM Key 后说一句「帮我测试 https://xxx.com」，Agent 自主完成侦察、测试、验证、报告全流程；每个漏洞都带原始请求/响应证据，可随时人工复核。

### 核心特性

- **SRC 漏洞挖掘模式**：橙色开关独立于自动化测试，开启后按赏金猎人节奏工作（种子队列 → 资产确认 → 单站打穿 → 落盘报告），`load_src_doc` 按需加载工作流与漏洞知识库，默认关闭不影响原有模式
- **浏览器自动化**：`browser_navigate` / `browser_evaluate` / `browser_screenshot` / `browser_cookies` 直连本机 Chrome/Edge（CDP，零新增依赖），JS 渲染页面、过前端挑战、截图取证、登录态核查
- **抓包拦截器**：请求/响应双层拦截、所见即所得改包、Forward/Drop、超时自动放行——手动调试与 AI 自动测试无缝衔接
- **ReAct 自主编排引擎**：支持 "帮我测试 https://xxx.com" 式自然语言指令，Agent 按侦察 → 测试 → 验证 → 报告四阶段自主执行；`run_command` 后台运行 + `check_background` 轮询，长扫描不阻塞
- **工具按需加载**：core 常驻，discovery / file / browser 组经 `load_tools` 按需启用，降低 Token 开销
- **攻击面发现工具**：`analyze_js` 从 JS 提取接口、`fuzz_paths` 路径字典爆破，指令驱动式深挖攻击面
- **N-day 漏洞情报**：`cve_lookup` 按组件+版本查已知 CVE（NVD + cve.circl.lu），`web_search` 联网搜利用细节；先思考后查证，需要时才走代理
- **上游代理出口**：Agent 请求（显式 `use_proxy`，默认直连防回环）、子进程环境变量、内置浏览器 `--proxy-server` 三条路径跟随上游代理，情报查询失败自动兜底
- **上下文智能管理**：缓存前缀稳定 + 索引按需注入 + 渐进式压缩（Session Memory → LLM AutoCompact），长任务不爆上下文
- **checkpoint 断点续跑**：任务自动保存快照，崩溃或中断后可一键续跑
- **多 Agent 协作**：5 个内置角色（侦察员/攻击手/验证员/情报员/报告员）共享渗透测试上下文，Coordinator 编排全流程；对话模式主 Agent 也可直接分工
- **渐进式技能披露**：99 个内置安全技能，system prompt 只注入索引，完整指令按需加载
- **漏洞证据链闭环**：每个漏洞附带原始请求/响应包证据，三态验证 + 自动复测；`vuln-report-writing` 技能规范报告格式与定级
- **抓包代理**：mitmproxy 代理，FlowTag 正则标注，单击底部面板 / 双击浮动窗口查看数据包
- **多 LLM 接入**：OpenAI / DeepSeek / Anthropic / 通义千问 / Gemini / Kimi / 智谱 / 百川 / Ollama / 自定义，支持 OpenAI 兼容与 Anthropic 原生双协议中转站
- **人在环安全控制**：敏感操作（DoS / 注入 / RCE / 数据删除）强制人工确认，300s 超时自动拒绝

### 安装方式

1. 下载 `Hemlock_0.6.0_x64-setup.exe`
2. 双击安装并启动
3. 进入设置页面配置 LLM API Key（模型建议 `deepseek-chat`）
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

- **SRC Bug-Bounty Hunting Mode**: an independent toggle on the AI testing page — the Agent hunts with a bug-bounty workflow (seed queue → asset confirmation → per-host full coverage → immediate reporting on medium+ findings), with an on-demand document library (`load_src_doc`) covering workflow rules and 30+ vulnerability playbooks; off by default, existing test modes unaffected
- **Browser Automation (Global)**: `browser_navigate` / `browser_evaluate` / `browser_screenshot` / `browser_cookies` drive the local Chrome/Edge via CDP (zero new dependencies) — JS-rendered pages, in-page scripting, screenshot evidence, session verification
- **Claude Code-style Shell & Files**: `run_command` with persistent working directory and background execution (`check_background` poll/kill, logs on disk), paginated `read_file` with binary detection, `list_files` for directory listing; workspace-escape guarded
- **Upstream Proxy, End to End**: Agent HTTP requests (explicit `use_proxy`, direct by default to avoid loops), subprocess environment injection, and the built-in browser (`--proxy-server`) all honor the configured upstream proxy; intel lookups fall back through the proxy automatically
- **Report Format, Globally**: before writing any report the Agent can load the `vuln-report-format` specification (full format + grading standard) — available in every test mode, so findings come out submission-ready
- **Packet Intercept (Burp-style)**: request/response layer interception, WYSIWYG packet editing, Forward/Drop, 300s timeout auto-forward — manual debugging and AI-autonomous testing in one flow
- **ReAct Autonomous Orchestration Engine**: natural language instructions like "test https://xxx.com", Agent executes Recon → Test → Verify → Report four-phase autonomous workflow
- **On-Demand Tool Loading**: core tools always available, discovery/file/browser groups enabled via `load_tools` to reduce token overhead
- **Attack Surface Discovery**: `analyze_js` extracts endpoints from JS, `fuzz_paths` brute-forces paths
- **Smart Context Management**: stable cache prefix + index-based injection + progressive compaction (Session Memory → LLM AutoCompact)
- **Checkpoint / Resume**: tasks auto-save snapshots, resume after crash or interruption
- **Multi-Agent Collaboration**: 5 built-in roles (Recon/Attacker/Verifier/Intelligence/Reporter) sharing context, Coordinator orchestrates the workflow
- **Progressive Skill Disclosure**: 99 built-in security skills, only the index injected into the system prompt
- **Vulnerability Evidence Chain**: every finding includes raw request/response evidence, three-state verification + auto-retest
- **Packet Capture**: mitmproxy proxy, FlowTag regex annotation, click for bottom panel / double-click for floating window
- **Multi-LLM Support**: OpenAI / DeepSeek / Anthropic / Qwen / Gemini / Kimi / Zhipu / Baichuan / Ollama / Custom — both OpenAI-compatible and Anthropic-native relay protocols
- **Human-in-the-Loop Security Control**: sensitive operations (DoS / Injection / RCE / Data Deletion) require manual confirmation, 300s timeout auto-reject

### Installation

1. Download `Hemlock_0.6.0_x64-setup.exe`
2. Double-click to install and launch
3. Configure LLM API Key in Settings (model: `deepseek-chat` recommended)
4. Start proxy, configure browser proxy and install CA certificate
5. Create a project and start testing

### System Requirements

- Windows 10/11 x64
- 4GB+ RAM
- 200MB disk space
- Access to an LLM API service
