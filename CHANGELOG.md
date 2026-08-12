# Changelog

本文件记录 Hemlock 项目的所有重要变更。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [0.5.0] - 2026-08-12

### 新增
- **抓包拦截（Burp 风格 Intercept）**：流量流经代理时按需暂停，展示并编辑原始数据包后放行（Forward）/ 丢弃（Drop）；「拦截请求」「拦截响应」两层可分别开关，全局总开关控制，代理重启不丢失配置
- 独立「拦截器」视图：开关工具栏 + 实时队列计数 + 所见即所得编辑区，报文排版 / 高亮与重放器、流量详情完全一致
- 编辑后 Forward 自动修正报文（重写 Content-Length、删除 Transfer-Encoding，与 Burp 行为一致）；拦截挂起超时（默认 300s）自动放行，避免流量永久卡住

## [0.4.3] - 2026-08-09

### 新增
- Provider 上下文窗口配置：API Key 管理页可填写 `context_window`（如 1M / 128K），决定 Agent 自动压缩触发时机（窗口的 50%）；留空按服务商兜底（DeepSeek 类 1M / 其余 128K）

### 优化
- **上下文压缩分界线机制**：压缩结果固化到任务 steps（compaction 步骤），恢复时线性展开、不再每轮全量重算——修复「热窗口滑动截取导致历史段命中率 0」的根因；平时纯追加保持历史前缀逐字节稳定，命中 DeepSeek 自动前缀缓存；仅当追加区超预算（context_window × 50%）才推进压缩分界线
- 分界线推进复用 AutoCompact 三层（Session Memory → LLM 摘要 → 确定性删减），失败自动降级；被压缩的原始步骤保留于 compaction 副本（落库完整），前端展示「上下文压缩分界线」摘要
- 记忆快照 + 分级过滤：对话开始时固化 global + 当前项目记忆为独立 system 快照消息，期间不可变、中途 save_memory 不回流，快照逐字节稳定可命中缓存；跨项目记忆不再串扰当前任务
- 动态段只注入技能 / MCP 索引（MCP 增加确定性排序），具体定义走工具按需加载、不进 system

## [0.4.2] - 2026-08-07

### 优化
- 缓存友好的 system 拆分：system prompt 拆为「稳定段 + 动态段」两条消息，稳定段（角色 + 渗透策略）逐字节不变、位于缓存断点之前，命中 Anthropic 显式断点 / DeepSeek-OpenAI 自动前缀缓存；动态段（技能索引 + MCP 列表 + 长期记忆）位于断点之后，改动不影响稳定前缀
- 技能 / 记忆索引确定性排序：按名称排序，集合不变时注入文本逐字节一致，缓存不会因排列不同而失效
- 长期记忆移到 system 动态段：记忆留在 system（auto-compact 不压缩）且位于缓存断点之外，技能 / 记忆增删不影响缓存命中

## [0.4.1] - 2026-08-07

### 新增
- 数据转发补全：漏洞管理页、数据详情页可直接把数据包发送到重放器 / 爆破器 / AI 助手（与流量列表右键菜单一致）
- 统一流量显示：漏洞详情、数据详情复用同一套报文渲染组件，显示效果一致

### 优化
- 子 Agent 中途消息广播：对话中给 Agent 的补充消息会同步送达当前运行的子 Agent，下一边界生效
- 子 Agent 思考流式实时显示：思考内容逐段推送，不再等整轮结束才出现；停止秒级生效
- 默认请求头对齐主流浏览器：代理发出的请求使用 Chrome 默认 User-Agent 与完整浏览器请求头，移除自定义标识头

### 修复
- 子 Agent 思考链与工具记录在刷新后不丢失，页面不重叠
- 记忆详情、信息收集报告查看、收集摘要、报告预览的 Markdown 内容正确渲染（标题 / 表格 / 代码高亮 / 代码块复制）

## [0.4.0] - 2026-08-07

### 新增
- 子 Agent 能力（对话模式）：主 Agent 可调用 `spawn_subagent` 启动专职角色（侦察/攻击/验证/情报/报告）分工执行独立子任务，共享测试上下文、发现自动记录、完成后返回执行摘要
- `spawn_subagent` 角色**可选不强制**：不指定角色时按通用执行单元运行，任务目标优先、角色只是专业背景参考（防止"岗位说明书"式角色 prompt 限制任务执行）
- 子 Agent 内部步骤持久化：`subagent_started` / `subagent_tool_call` / `subagent_tool_result` / `subagent_complete` 写入父任务 steps，刷新后嵌套工具调用不丢失（`_build_chat_messages` 自动跳过这些步骤，不污染 LLM 历史）

### 优化
- 对话中途插入消息即时识别：Agent 运行中发送的新消息非阻塞注入，下一思考边界即被识别，不再等整轮结束
- 终止信号立即生效：流式读取逐 chunk 检查取消标志，点停止即刻中断"继续思考"，并清空未决消息队列
- 工具调用卡片始终可展开：折叠为一行摘要（含执行状态），点击展开查看参数与结果，与思维链交互一致
- 思维链展示：连续思考步骤合并为一个折叠块 + 折叠态显示字数，弱化"一堆思维链条堆叠"的压迫感
- 指挥官工具集收敛：只保留只读监控工具 + spawn_subagent，编排者不直接执行测试（防越权 / 与系统提示词「不直接发 HTTP」对齐 / 省 Token）
- 子 Agent 工具定义剔除人工确认类工具（run_intruder / run_command / run_script），避免模型试调被挡（执行层保护保留）
- 子 Agent 每 5 步重注入最新测试上下文，避免启动时快照过期
- 子 Agent 推理持久化：`subagent_thinking` 步骤落库，刷新后思考链以折叠块还原，不再丢失
- 指挥官工具列表构建一次提出循环外，减少重复开销

### 修复
- 协作模式：指挥官 spawn 子 Agent 后本地 steps 过期，覆盖子 Agent 内部步骤导致刷新后嵌套卡片丢失（spawn 后重载）
- 协作模式：子 Agent 无法被终止（停止信号不传播，最坏跑满步数上限）——LLM 调用支持停止回调，子 Agent 循环边界逐处检查，指挥官 / 对话模式统一传入取消回调
- 协作模式：任务被取消（如服务关停）无兜底导致僵尸任务——后台运行器补取消处理、标记失败、清理注册与上下文

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
