# Hemlock 开发日志

按日期记录日常开发过程（面向开发者的流水账）。
面向用户的版本变更记录见 [CHANGELOG](../CHANGELOG.md)。

---

## 2026-08-05（下午 · 第二轮：round4 回归修复 + Token 优化）

### 背景
用户提供 round4 功能回归报告：4 个未修复 Bug（BUG-01/02/04/07）+ 2 个新需求（Agent 不在沙箱执行、文件落用户安装目录）+ 2 个后续追加（token 消耗过大、记忆/skill 索引机制）。

### Bug 处置结论

| Bug | 状态 | 说明 |
|-----|------|------|
| BUG-01 send_* 工具 TypeError | ✅ 源码已修复（验证） | 直接调用 6 个工具全部正常、flow 落库完整。round4 报告应为打包 exe 旧代码所致。已补回归测试锁定 |
| BUG-02 悬挂任务 | ✅ 已修复 | 根因：`asyncio.CancelledError` 是 BaseException，`except Exception` 捕不到，协程取消时 DB 状态不更新。①run_agent_task 增加 CancelledError 处理 ②新增 `cleanup_zombie_tasks()` 运行时清理（DB running/paused 但 `_active_agents` 无编排器 → failed）③lifespan 启动 `_watchdog_loop()` 每 60s 巡检 |
| BUG-04 /request/send dict headers | ✅ 源码已修复（验证） | schema 已有 Union[str,dict] 归一化，实测 dict→200。已补回归测试 |
| BUG-07 verify_vulnerability 纯 AI 判断 | ✅ 已修复 | 根因：`llm_assisted_verify_enabled=false` 时 `auto_verify` 直接返回 ai_judgment（不发请求不跑规则）。改为：规则验证**始终执行**，开关只控制不确定时是否追加 LLM 分析（新增 `_maybe_llm_assist` 包装，8 处调用替换）。实测 XSS 命中反射规则 verified=True、SQL 走布尔盲注 |

### Agent 执行目录（需求）

- `run_command` / `run_script` 增加 `cwd = workspace`（= 用户数据目录 `HEMLOCK_DATA_DIR/workspace`），命令/脚本产物落用户安装目录，不再落在打包只读目录。
- 配置类文件（DB/skills/roles/memories/settings/flowtag）本就统一在 `get_data_dir()` 下，保持不变。

### Token 优化（需求）

- **工具输出截断**（最大头）：`send_request`/`send_session_request` 之前把完整响应体返回 LLM（大页面动辄 MB）。改为 headers≤1KB / body≤3KB，带 `truncated` 标记 + 提示用 `get_flow_detail` 取全量。**DB 落库仍为完整包**（实测 5000B 源响应 → 工具返回 3000 / DB 保留 5000），不影响 Repeater/证据链/重放。
- `get_flow_detail` 内容上限 10K 字符（完整数据在 DB）。
- **技能索引**：`build_skills_prompt` 描述截断至 90 字符，索引体积 24335→12030 字符（8.1K→4.0K tokens）。
- **记忆索引**：`format_memory_for_prompt` 只注入 name+description+trigger（正文不再全量注入）；新增 Agent 工具 `get_memory` 按需读正文；更新 SYSTEM_PROMPT 记忆协议。

### 上下文管理改造：Claude Code 式（降 token + 提缓存命中率）

用户要求对齐 Claude Code 的上下文管理方法。分析发现两个核心问题：
1. **缓存命中率杀手**：`ContextAssembler` 把温记忆（测试进度）注入 **system prompt**——进度每几轮就变，导致整个 system（约 10K tokens）每次请求都变，OpenAI 兼容 API（DeepSeek 等）的自动前缀缓存整段失效。
2. **缺少微压缩**：工具输出要等超预算才批量蒸馏，期间原始大输出堆积。

**改造（对齐 Claude Code 的 缓存前缀稳定 + Microcompact + 索引）：**
- **system prompt 完全稳定**：`assemble`/`compress_messages` 不再注入温记忆。进度由 Agent 用 get_test_coverage/get_strategy 工具按需获取；自主模式每 5 步以 user 消息追加（append-only，不破坏前缀）。
- **压缩标记改 user 角色**：原中途插入 system 消息破坏缓存 + 违反 OpenAI 消息规范，改 user。
- **微压缩（Microcompact）**：新增 `_tool_result_to_llm_content`，工具结果插入消息时若超 6000 字符即蒸馏（阈值高于工具级截断的 ~5KB，小结果透传保留 flow_id/body 预览）；5 处工具消息插入 + 历史重建 `_append_tool_group` 全部接入。
- **HTTP 蒸馏保留 flow_id**：`_distill_http_request` 输出带 `flow=xxx`，确保批量压缩后 Agent 仍能引用 flow 调 get_flow_detail/report_finding。
- skill/记忆保持索引机制（前述）。

### LLM 驱动 AutoCompact（学习自 Claude Code 泄露源码）

用户指出 Claude Code 源码曾泄露（2026-03-31，59.8MB source map 流出，~1900 文件），可直接学习。网上公开分析确认了其压缩架构（`src/services/compact/compact.ts` / `microCompact.ts` / `sessionMemoryCompact.ts`）：

- **渐进升级按成本**：MicroCompact（本地免费）→ Session Memory Compact（预提取）→ Full Compact（LLM 摘要，贵）
- **AutoCompact**：LLM 生成结构化摘要（`<analysis>` 思考 + `<summary>` 输出），`NO_TOOLS` 前缀（"只用文本，不调工具"）+ `maxTurns:1` 防摘要时误调工具，熔断器 `MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES=3`，触发阈值提前 ~13K tokens
- **工具对完整性**：切分不拆开 assistant(tool_calls) 与其 tool 结果（API 不变量）
- **完整历史永不删除**：JSONL append-only，压缩只影响发送 API 的内容

**落地到 Hemlock**（`agent_service.py`）：
- 新增 `_auto_compact_messages`：LLM 生成中文结构化进展摘要（目标/画像/漏洞含 flow_id/进度/会话状态/待办/关键引用），`<analysis>/<summary>` 两段式，NO_TOOLS（摘要调用不带 tools），熔断器（连续失败 3 次回退确定性压缩），无 LLM 时直接跳过 API 调用
- 新增 `_split_for_autocompact`：工具对完整性保护（切分点落在 tool 结果上时前移归组）
- 接入两个主循环：`run`（自主模式，每 5 步）与 `chat_with_agent`（对话模式，每 10 次工具调用）；子 Agent/指挥官保持确定性压缩（短生命周期更省）
- 边界消息：`[上下文压缩] 早期对话已被 LLM 压缩为进展摘要...`（user 角色，不破坏缓存前缀）

### Session Memory / cache_control / checkpoint-resume（"都做"三项）

**① Session Memory 预提取（免费压缩层，对齐 sessionMemory.ts）**
- PentestContext 已被 `_auto_track_context` 持续追踪（端点/参数/漏洞/技术栈/进度），可零成本生成结构化进展摘要。
- `_auto_compact_messages` 新增**压缩层优先级**（对齐 compact.ts 渐进升级）：Session Memory（免费）→ LLM 摘要（贵）→ 确定性压缩（回退）。
- `record_finding` 现在保留 finding_id + flow_id；FindingRegistry 摘要输出带 `flow=xxx`，Session Memory 不丢证据引用。

**② cache_control 显式缓存（Claude provider）**
- 新增设置 `HEMLOCK_LLM_CACHE_CONTROL_ENABLED`（默认关闭）。
- `llm_client._apply_cache_control`：仅当开启且模型为 `claude-` 前缀时，将 system 消息转为 Anthropic 内容块格式并附加 `cache_control: ephemeral`，命中 Claude prompt 缓存。
- DeepSeek/OpenAI 用自动前缀缓存，不接受此字段，默认关闭不受影响。

**③ Agent checkpoint/resume（对齐 Claude Code 会话持久化）**
- Task 模型新增 `messages`（LLM 消息历史 JSON）+ `checkpoint`（PentestContext 快照 JSON）列，DB 迁移自动添加。
- `PentestContext.to_dict/from_dict` 序列化（端点/参数/技术栈/漏洞/覆盖度）。
- `run()` 启动时检测 checkpoint 自动恢复（消息 + 上下文 + 步数），崩溃后重跑同一任务可续跑。
- 每 3 步周期保存 checkpoint；`_save_checkpoint` 幂等。
- **修复潜在 bug**：自主模式 `run()` 启动即置 running（原始终 idle，导致看门狗管不到、崩溃不标 failed）。
- 新增 `POST /api/agent/tasks/{task_id}/resume`：仅可恢复 failed/paused，后台续跑。

### 验证
- `pytest` **16/16 通过**。
- 实测：Session Memory 层零 LLM 调用压缩（42→9 消息，保留 finding_id+flow_id）；cache_control 三种情形（关闭/claude/deepseek）正确；checkpoint 保存/恢复（消息数、step、端点、漏洞 flow_id 全部还原）；resume 端点对非 failed/paused 任务正确返回 400。

---

## 2026-08-05（傍晚 · 第三轮：发现工具 + 两个体验 Bug）

### 攻击面发现工具（解决"指令驱动"问题）
新增 `services/discovery_service.py`，给 Agent 装上"眼睛"（对齐 Claude Code 的 Glob/Grep/Read）：
- **`analyze_js`**：抓页面 → 下载 JS → 正则提取真实接口（fetch/axios/url 字段/路径字面量），过滤 CSS/图片噪音、仅保留同主机目标。现代网站接口大多藏 JS 里。
- **`fuzz_paths`**：内置字典爆破常见路径（/admin、/api、/.git、/backup、/swagger 等 ~100 条），只报非 404 命中。
- 均过 `check_scope` 授权校验 + 限速；已加 Agent 工具 schema、分发、`_tool_*` 方法；补 2 项回归测试。

### 修复：send_to_repeater 强制跳转
原实现收到 `send_to_repeater` 事件直接 `navigate("/repeater")`，用户无感知被拽走。改为：
- 请求**静默载入** Repeater（PacketContext）；
- 复用**安全确认栏同款 UI**（内联确认条），显示"请求已放入重放器… 是否前往查看？"；
- 用户点**「前往查看」**才跳转，点**「稍后再说」**或 8s 后自动消失。

### 修复：任务已结束但仍显示"工作中"
**根因（前端）**：`refreshTasks` 只刷新任务列表，**不清 `runningTasks`**；自主模式任务加入 runningTasks 后，`task_complete` 触发的刷新不清除 → 永远显示工作中。
**根因（后端）**：`run()` 多个终止路径（取消/无LLM/超时/空响应/达上限）**不发终止事件**，前端收不到刷新信号。
修复：
- 后端：`run_agent_task` 的 `finally` 按最终状态统一补发 `task_complete`/`task_error`（一处覆盖所有路径）。实测 FAILED 路径发出 `task_error`。
- 前端：WS 处理器收到 `task_complete`/`task_error`/`coordinator_finished` 直接清除该任务 runningTasks；`refreshTasks` 对 done/failed 任务对账清除。

### 验证
- `pytest` **18/18 通过**（新增 2 项发现工具测试）。
- 前端 `tsc` + `vite build` 通过。
- 实测：analyze_js 从 JS 提取 5 接口并滤噪、fuzz_paths 报非 404 命中、无 LLM 任务 FAILED 路径正确发出 `task_error`。

---

## 2026-08-05（深夜 · 第四轮：Token 开销量化与修复）

### 量化结果
| 固定开销（每请求） | 大小 |
|---|---|
| 41 个工具定义 | 23,680 字符 ≈ **6,765 tokens**（参数 schema 占 75%，描述占 25%） |
| SYSTEM_PROMPT | 3,621 tokens |
| + 策略库/技能索引/记忆索引 | ~10K |
| **每次请求固定 ≈ 17-18K tokens** | |

**缓解**：稳定前缀缓存（之前已做）让 DeepSeek 自动前缀缓存在这 17K 上"会话内只付一次"，后续回合只付增量 + 缓存读（~0.1x）。长任务内是摊销的。

### 修复
1. **跨任务缓存失效（最大的浪费）**：对话模式把 `task.goal` 注入 system prompt → 每个任务前缀不同 → 跨任务缓存全失效（17K × 任务数）。移出后 system+工具前缀跨任务一致，**17K 从"每任务付"变"每会话付一次"**。
2. **裁剪工具描述**：call_api(699→200)、recon_search(374→170)、verify_vulnerability(293→120)、analyze_js(226→90) 等 → 工具定义省 ~285 tokens。**收益有限**（描述只占工具定义 25%）。

### 系统提示词瘦身（43%）
逐段审查发现大量与工具描述/策略库重复的内容，全部裁掉：
- 记忆协议 74 行 → 3 行（save_memory/get_memory 工具描述已覆盖）
- 工具选择策略 → 删（38 工具描述各自已说明何时用）
- FlowTag 段 → 删（flowtag 工具描述已覆盖）
- 分阶段测试体系 46 行 → 3 行流程（细节全在策略库）
- 异常处理 → 1 行；输出/终止 → 合并
结果：SYSTEM_PROMPT 12,641→7,246 字符（3,621→2,070 tokens，**-43%**）。

### 工具分级加载（"索引+按需"模式，对齐技能/记忆）
把 41 工具按"大类 + 按需"拆分，杜绝"用户说一句话就全量塞 41 个工具"：
- **核心工具组（始终注入，30 个）**：HTTP 主循环（send/verify/report/auth/chain/call_api 等）
- **discovery 组（按需）**：analyze_js/fuzz_paths/http_probe/dns/subdomain/recon_search/侦察报告
- **file 组（按需）**：run_command/run_script/read_file/write_file
- 新增 `load_tools(组名)` 工具（始终注入）：Agent 需要某组时调用 → 下一轮该组工具才加入
- `build_agent_tools(loaded_groups=...)` 过滤；run() 与 chat_with_agent 接入；_execute_tool_standalone 共享 loaded_groups
- system prompt 加一行工具加载说明
结果：始终注入的工具 42→30（**-24%**），实测 load_tools 后 38 个（discovery 生效）。

### 遗留（待用户确认）
- 冗余工具（FlowTag×3、generate_pentest_report）仍可删（call_api 可替代），预计再省 ~900 tokens
- 技能索引（98 技能 ~4K tokens）已成固定开销最大单块，可考虑精简描述或动态注入

---

## 2026-08-05（深夜 · 第五轮：发布准备 + 逻辑 Bug 修复）

### 版本统一
main.py(0.2.1/0.2.3 两处不一致)、pyproject.toml(0.1.0)、tauri.conf.json、__init__.py → 统一 **0.3.0**。

### 修复的逻辑 Bug
1. **开放重定向验证永远失败**（真 Bug）：客户端 `follow_redirects=True` 自动跟随 302，Location 头被吞 → verify_open_redirect 永远验不出。修复：`send_raw_request` 加 `follow_redirects` 参数，验证时传 False。实测 verified=True。
2. **复测/流量分析/生成payload 不走 UI Provider**（系统性 Bug）：`retest_service` 和 `ai_service` 的 LLM 检查只查 `settings.llm_api_key`（.env），不查 UI 配置的数据库 Provider → 用 UI 配 key 的用户，这些 AI 功能全部静默降级为规则版。修复：`_has_llm(db)` 同时查 settings + LLMProvider 表，并传 db 给 chat_json。
3. **（记录未修，边缘）** Intruder sniper 多标记位：组合只替换一个标记，其他标记以字面 `§x§` 留在 URL。影响前端多位置 sniper 场景。

### 发布可用性验证
- 后端 API 面冒烟 **21/21 通过**（health/projects/flows/request/flowtag/settings/llm/recon/agent/payloads/strategies/findings/memories 等）
- `pytest` **18/18**
- 前端 `tsc` + `vite build` 通过
- 服务器以 v0.3.0 正常启动

### 发布清单（用户需做）
1. **重建安装包**（build.ps1）——所有修复都在源码，已装的 0.2.3 exe 不含这些改动
2. **UI「LLM Provider 管理」把模型改成 `deepseek-v4-flash`**——deepseek-chat 已于 2026-07-24 废弃，会报错
3. 版本已对齐 0.3.0

---

## 2026-08-05（上午 · 第一轮：接管项目）

### 接管项目，全面梳理代码库

**目标：** 接管 Hemlock 项目，熟悉全部代码结构，建立可用的开发基线。

**完成：**

1. **全库走查**：读遍后端 `hemlock/`（核心编排器 agent_service.py 5491 行、三级记忆 context_manager、三态验证 vuln_verifier、MCP 管理、技能/角色/记忆三套文件化注册表）、前端 React 全部视图与 API 封装、mitmproxy addon、Tauri sidecar（src-tauri）。
2. **确认工程状态**：项目后端包目录此前曾更名为 `backend/hemlock/`（旧目录名已废弃），整个新包 + 大部分前端文件均未提交（git 停在 `2f165de`）。

### 修复 4 个真实缺陷（当前代码已修，测试全绿）

| # | 位置 | 问题 | 影响 |
|---|------|------|------|
| 1 | `core/logging_config.py` | `setup_logging()` 使用 `settings.debug` 但未 import `settings` | 后端启动（lifespan）即抛 NameError 崩溃 |
| 2 | `core/database.py` | 临时项目清理 SQL：①括号不匹配 ②表名 `evidence`（实际是 `evidences`）③列 `finding_id`（实际经 `findings.evidence_id` 反查） | `init_db()` 必崩，数据库无法初始化 |
| 3 | `tests/test_flows_api.py` | 两个测试过时：`/api/flows` 已强制按 `project_id` 隔离，测试未建项目也没传参 | 测试失败 |
| 4 | `dev.ps1` + `.env.example` | 用 8000 端口，与 vite 代理 / config 默认 9876 不一致 | `dev.ps1` 启动后前端连不上后端 |

### 基线

- 后端：`pytest` **10/10 通过**；真实启动冒烟通过（lifespan 全流程：角色创建、98 个内置技能复制、MCP 连接、`/health` 200）。
- 前端：`npm run build` 构建成功（仅 chunk >500KB 警告，非错误）。

### 备注 / 待办

- 后端 `.env` 中 `HEMLOCK_LLM_API_KEY` 为空，Agent 功能需配置 LLM Key（或在前端 LLM Provider 管理中添加）才能运行。
- git 工作区有大量未提交改动（新 `hemlock` 包 + 前端），建议尽快提交一次，避免重命名迁移丢失。
- 测试中 `logging_config` 会把日志写到真实 `%APPDATA%\Hemlock\logs`（测试未隔离数据目录），可后续优化。
- Pydantic 告警：`model_routing_enabled` 与 protected namespace `model_` 冲突（settings schema），可加 `protected_namespaces=()` 消除。
