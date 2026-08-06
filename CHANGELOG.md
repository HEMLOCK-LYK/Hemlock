# Changelog

本文件记录 Hemlock 项目的所有重要变更。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [0.3.1] - 2026-08-06

### 新增
- N-day 漏洞情报：`cve_lookup` 工具（组件+版本查已知 CVE，NVD 主源 + cve.circl.lu 回退）+ `web_search` 工具（DuckDuckGo 联网搜索）
- `cve-search` 内置技能（修复 radar 角色死引用，内置技能 98→99）
- 外部情报请求默认直连，直连失败自动改用工具上游代理（upstream_proxy）重试，不是所有流量强制走代理

### 优化
- SYSTEM_PROMPT 新增「情报工具纪律」：情报工具是验证假设用、不是替代思考用，先推理后查证
- `cve_lookup` / `web_search` 移入 discovery 按需组（Agent 需先 `load_tools("discovery")` 才能用，强化联网门槛）
- SYSTEM_PROMPT 告知 Agent 情报请求的上游代理兜底机制
- 文档全面重写对齐 0.3.x（README 定位「全栈一体化+人工验证闭环」、部署指南、技术文档、RELEASE_NOTES）

## [Unreleased]

### 计划中
- Nuclei 集成
- Decoder / Comparer / Sequencer 工具
- 多项目管理增强
- 渗透测试报告自动生成
- 插件系统

## [0.3.0] - 2026-08-05

### 新增
- 发现工具：JS 接口提取（analyze_js）+ 路径字典爆破（fuzz_paths），支持指令驱动式信息收集
- Session Memory 免费压缩层（_auto_compact_messages 优先级：session_memory → LLM → deterministic）
- cache_control 显式缓存注入（HEMLOCK_LLM_CACHE_CONTROL_ENABLED，仅 claude- 模型启用 ephemeral）
- checkpoint/resume：任务消息持久化，每 3 步自动保存，支持中断后续跑
- Agent 执行目录隔离：run_command/run_script 固定在工作区目录
- 新增作者署名（IHateTheWorld）

### 优化
- 任务结束统一补发终止事件，前端 runningTasks 对账清除，杜绝"任务结束仍显示工作中"
- send_to_repeater 改为复用安全确认 UI，用户点击"前往查看"才跳转
- 项目全面更名 secforge → Hemlock（前端文案 / 应用标识 / 打包元数据）
- 后端打包清理 build/dist 残留，PyInstaller 重新构建
- 工具按需加载（load_tools）：核心 30 个始终注入，discovery/file 12 个按需启用，单次请求工具定义 -24%
- 缓存前缀稳定：system prompt 静态化，温记忆不再注入，自动前缀缓存「会话内只付一次」
- 工具输出截断（send_request headers≤1KB/body≤3KB，DB 落库完整）+ 消息微压缩（>6000 字符蒸馏）
- SYSTEM_PROMPT 瘦身 43%（3,621 → 2,070 tokens）；技能/记忆改索引注入 + get_memory 按需读

### 修复
- 悬挂任务：run_agent_task 补 asyncio.CancelledError 处理 + cleanup_zombie_tasks() + lifespan 60s 看门狗
- verify_vulnerability：auto_verify 规则始终执行，llm_assisted_verify_enabled 仅控制是否追加 LLM
- 前端版本号硬编码 v0.1.0 统一为 v0.3.0

## [0.2.0] - 2026-08-04

### 新增
- AI Agent 自主渗透测试引擎（ReAct 循环编排器，40 步上限）
- 三级记忆上下文管理系统（Hot/Warm/Cold Memory）
  - 4K Token 热记忆预算，自动蒸馏旧消息
  - 工具输出自动摘要（95%+ 压缩率）
  - 文件化冷记忆持久化存储
- 多 Agent 协作模式（5 内置角色：侦察员/攻击手/验证员/情报员/报告员）
- 动态上下文压缩（每 5 步检查，超 6K Token 自动触发）
- WebSocket 实时通信（思考链流式推送、工具调用实时展示）
- LLM reasoning_content 提取（支持 DeepSeek-R1 / Qwen-QwQ 等推理模型）
- 多 LLM Provider 管理（OpenAI / DeepSeek / Anthropic / 通义千问 / Gemini / Kimi / 智谱 / 百川 / Ollama / 自定义）
- 敏感操作人工确认机制（DoS / 注入 / RCE / 数据删除，300s 超时）
- 漏洞验证引擎（三态语义判定 + 7 种验证方法）
- Payload 库（BASIC / STANDARD / BYPASS 三级递进，覆盖 7 种漏洞类型）
- 渗透测试上下文追踪器（技术栈识别 + OWASP Top 10 策略推荐）
- MCP 服务器集成（外部工具动态注入）
- FlowTag 正则标注引擎
- 信息收集模块（FOFA / Quake / Hunter / ZoomEye 等 9 引擎并行搜索）
- 项目数据隔离（project_id 过滤）
- 临时项目模式（退出自动清理，切换提示保存）

### 优化
- 数据包查看器（单击底部面板 / 双击浮动窗口）
- 全局关键字搜索 + Ctrl+F 包内搜索
- 6 套主题配色（GitHub Dark/Light / Matrix / Burp Suite / Midnight Blue / Solarized）
- 数据包语法高亮适配明暗主题（CSS 变量驱动，参考 Burp Suite 配色）
- Agent 思考链折叠展示（灰色小框，逐句显示，点击展开）
- Agent 消息气泡布局
- 代码块语法高亮（GitHub Light / One Dark）
- 历史对话切换加载动画 + 性能优化
- 前端性能优化（memo / useCallback / requestAnimationFrame 批量更新）

### 修复
- 主题切换后数据包颜色不适配问题
- Agent 对话输入框长时间使用后卡顿问题
- 历史对话切换卡顿问题（旧消息卸载 + 新消息渲染）
- 抓包流量通过但数据列表无记录问题
- AI 发送数据包被噪声过滤器误过滤问题
- duration_ms 类型校验导致 400 错误
- 后端重启后 active_project_id 丢失问题

## [0.1.0] - 2026-07-28

### 新增
- HTTP/HTTPS 代理抓包（基于 mitmproxy）
- 流量列表展示（自动拆分字段、杂包检测过滤）
- Repeater 请求重放与编辑
- Intruder 批量 payload 爆破
- AI Agent 基础对话（WebSocket 流式通信）
- LLM Provider 配置管理
- Agent 角色管理（Markdown 文件化配置）
- 技能注册表系统
- 项目管理（创建 / 切换 / 删除）
- SQLite 数据持久化
- Tauri v2 桌面应用打包
