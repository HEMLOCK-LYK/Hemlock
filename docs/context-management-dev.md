# Hemlock 上下文管理系统开发文档

> 本文档面向开发者，描述当前（v0.3.0）上下文管理的实际架构与实现。
> 设计演进见 [DEVLOG](./DEVLOG.md)，面向用户的概述见 [README](../README.md)。

## 1. 背景与目标

### 1.1 问题分析

Hemlock 的 Agent 在渗透测试中会持续调用工具（发请求、跑爆破、读文件），消息列表快速增长：

- **消息列表无限增长**：`task.steps` 全量恢复历史消息，不做截断
- **工具输出原文注入**：HTTP 响应、扫描结果的完整输出（数千 tokens）直接塞入 messages
- **无 Token 预计数**：发送前无法评估上下文大小，超出模型窗口导致 API 失败
- **缓存命中率低**：system prompt 若随测试进度变化，OpenAI 兼容 API 的自动前缀缓存整段失效

### 1.2 设计目标

- **降低 Token 消耗**：通过蒸馏、压缩、按需加载，减少 60-80% 的 token 浪费
- **保持 Agent 能力**：关键信息（漏洞发现、证据、测试进度）不丢失
- **缓存友好**：保持请求前缀稳定，最大化前缀缓存命中
- **自动透明**：对现有 Agent 流程侵入最小，开发者无需手动管理上下文

### 1.3 设计原则

对齐 Claude Code 的上下文管理思路，针对渗透测试场景做领域专用优化：

1. **缓存前缀稳定**（最重要）：system prompt 完全静态化，变化的内容用 user 消息追加
2. **索引 + 按需**：技能 / 记忆 / 工具只注入索引，完整内容按需读取
3. **渐进式压缩**：先零成本策略（蒸馏/索引），再高成本策略（LLM 摘要），失败自动回退
4. **证据为中心**：压缩必须保留 `flow_id` / `finding_id` 等关键引用，Agent 可随时深查

---

## 2. 架构总览

上下文管理由**五道防线**协同工作，每一道都比前一道更"重"：

```
① 缓存前缀稳定 ── system prompt 静态化 → 前缀缓存会话内只付一次
② 索引 + 按需   ── 技能/记忆/工具只注入索引，完整内容按需读取
③ 输出蒸馏     ── 工具级截断 + 微压缩，超大输出进不了上下文
④ 渐进式压缩   ── Session Memory(免费) → LLM AutoCompact → 确定性压缩(回退)
⑤ checkpoint   ── 每 3 步保存快照，崩溃后断点续跑
```

### 2.1 模块划分

| 组件 | 位置 | 职责 |
|------|------|------|
| `TokenEstimator` | `core/context_manager.py` | 本地 Token 估算（字符数 / 3.5 近似） |
| `ToolOutputDistiller` | 同上 | 工具输出领域专用蒸馏（HTTP/扫描/文件等 12 类） |
| `FindingRegistry` | 同上 | 漏洞发现注册表（结构化摘要，保留 finding_id + flow_id） |
| `ContextAssembler` | 同上 | 组装消息：保持 system 稳定 + 热记忆预算 + 旧工具输出蒸馏 |
| `_auto_compact_messages` | `services/agent_service.py` | 渐进式压缩（Session Memory → LLM 摘要 → 确定性） |
| `_tool_result_to_llm_content` | 同上 | 微压缩：工具结果插入消息时 >6000 字符即蒸馏 |
| `_split_for_autocompact` | 同上 | 工具对完整性保护（不拆开 tool_call 与结果） |
| `PentestContext` | `core/pentest_context.py` | 测试进度追踪，提供 Session Memory / checkpoint 数据源 |

### 2.2 集成点

```
services/agent_service.py
├── run()                  ← 自主模式：每 5 步 check 超预算 → _auto_compact_messages
│                             每 3 步保存 checkpoint；恢复时还原消息 + 上下文
├── chat_with_agent()      ← 对话模式：每 10 次工具调用压缩
├── 消息插入处（5 处）      ← _tool_result_to_llm_content 微压缩
└── run_subagent / run_coordinator_task
                           ← 短生命周期子 Agent 保持确定性压缩（更省）
```

---

## 3. 详细设计

### 3.1 缓存前缀稳定（第一道防线）

**核心问题**：`ContextAssembler` 曾把温记忆（测试进度）注入 system prompt——进度每几轮就变，导致整个 system（约 10K tokens）每次请求都变，DeepSeek / OpenAI 的自动前缀缓存整段失效。

**当前方案**：
- `assemble()` / `compress_messages()` **不再向 system prompt 注入任何动态内容**
- 测试进度由 Agent 通过 `get_test_coverage` / `get_strategy` 工具**按需获取**
- 自主模式每 5 步以 **user 角色** 消息追加进度（append-only，不破坏前缀）
- 压缩标记、边界消息一律用 **user 角色**（中间插入 system 消息会破坏缓存 + 违反 OpenAI 消息规范）

**收益**：system + 工具定义前缀跨任务、跨回合稳定，前缀缓存「会话内只付一次」。

### 3.2 索引 + 按需（第二道防线）

对齐"技能 / 记忆 / 工具"三类资源，都采用**索引注入 + 按需读取**：

| 资源 | 索引注入内容 | 按需获取方式 |
|------|-------------|-------------|
| 技能 | 名称 + 描述（截断至 90 字符） | `invoke_skill` 工具 |
| 记忆 | 名称 + description + trigger | `get_memory` 工具 |
| 工具 | 核心 30 个始终注入 | `load_tools(组名)` 加载 discovery / file 组 |

**工具按需加载**（对齐技能/记忆模式）：
- **discovery 组（8 个）**：`analyze_js` / `fuzz_paths` / `http_probe` / `dns_lookup` / `subdomain_enum` / `recon_search` / `list_recon_reports` / `get_recon_report`
- **file 组（4 个）**：`run_command` / `run_script` / `read_file` / `write_file`
- 41 个内置工具中，始终注入的核心工具从 42 降到 **30**（-24%）；用户说一句话不再全量塞满工具定义

### 3.3 工具输出蒸馏与微压缩（第三道防线）

**工具级截断**（源头控制，`send_request` / `send_session_request`）：
- headers ≤ 1KB，body ≤ 3KB，带 `truncated` 标记，提示用 `get_flow_detail` 取全量
- **DB 落库仍为完整包**，不影响 Repeater / 证据链 / 重放

**微压缩（Microcompact）**：`_tool_result_to_llm_content`
- 工具结果插入消息时，若 > 6000 字符即蒸馏为摘要
- HTTP 蒸馏保留 `flow=xxx` 引用，Agent 压缩后仍能 `get_flow_detail` / `report_finding`

**`ToolOutputDistiller` 领域蒸馏**：12 类工具各自有蒸馏规则（HTTP 请求 / 流量分析 / 快速测试 / 爆破 / payload / 文件读取 / call_api / 验证 / 对比 / 请求链…）。

### 3.4 渐进式压缩（第四道防线，`_auto_compact_messages`）

对齐 Claude Code 的渐进升级，按成本排序：

```
Session Memory（免费） → LLM 摘要（贵） → 确定性压缩（回退）
```

- **① Session Memory**：`PentestContext` 已被 `_auto_track_context` 持续追踪（端点/参数/漏洞/技术栈/进度），零成本生成结构化进展摘要。`record_finding` 保留 `finding_id` + `flow_id`，摘要不丢证据引用
- **② LLM AutoCompact**：LLM 生成中文结构化进展摘要（目标 / 画像 / 漏洞含 flow_id / 进度 / 会话状态 / 待办 / 关键引用），`<analysis>` / `<summary>` 两段式；**NO_TOOLS**（摘要调用不带 tools）+ `maxTurns:1` 防摘要时误调工具；熔断器连续失败 3 次回退确定性压缩；无 LLM 时直接跳过 API 调用
- **③ 确定性压缩**：`ContextAssembler.compress_messages` 蒸馏旧工具输出，兜底
- 压缩结果以 **user 角色**边界消息提示 `[上下文压缩] 早期对话已被压缩为进展摘要…`

**工具对完整性**：`_split_for_autocompact` 保证切分不拆开 assistant(tool_calls) 与其 tool 结果（API 不变量）。

### 3.5 checkpoint / resume（第五道防线）

- `Task` 模型新增 `messages`（LLM 消息历史 JSON）+ `checkpoint`（PentestContext 快照 JSON）列
- `PentestContext.to_dict / from_dict` 序列化（端点 / 参数 / 技术栈 / 漏洞 / 覆盖度）
- `run()` 启动时检测 checkpoint 自动恢复（消息 + 上下文 + 步数）；**每 3 步**周期保存
- 新增 `POST /api/agent/tasks/{task_id}/resume`：仅可恢复 failed / paused 任务，后台续跑

### 3.6 Token 估算器

不引入 tiktoken（Windows + Python 3.12 安装复杂、模型特定），用字符数近似：

```python
class TokenEstimator:
    CHARS_PER_TOKEN = 3.5  # 英文 ~4 字符/token，中文 ~1.5，混合 ~3.5

    @staticmethod
    def estimate(text: str) -> int:
        return max(1, int(len(text or "") / TokenEstimator.CHARS_PER_TOKEN))

    @staticmethod
    def estimate_messages(messages: list[dict]) -> int:
        # 每条消息的元数据开销 ~4 tokens
        ...
```

精度在预算管理场景下足够；Claude Code 同样不在本地精确计数，而是用 API 返回的 usage。

---

## 4. 关键设计决策

### 4.1 为什么温记忆不再注入 system prompt？

> 这是相对早期设计最大的变更。

早期把温记忆注入 system prompt 是为了"全局参考信息只出现一次"。但实测发现它是**缓存命中率杀手**：进度每几轮就变 → 整个 system（约 10K tokens）每次请求都变 → 自动前缀缓存整段失效，每次请求全价重付。

改为：system 完全静态；进度按需取（工具）或按周期追加（user 消息）。**牺牲一次显式注入，换回整个会话的前缀缓存命中。**

### 4.2 为什么蒸馏而非删除旧工具输出？

- 渗透测试是证据驱动的：之前的扫描结果可能影响后续决策
- 完全删除导致 LLM "失忆"，重复执行相同测试
- 蒸馏保留关键信息（状态码、参数、`flow_id`、发现），只去掉原始数据
- 原始数据仍存储在 DB / task.steps，需要时 `get_flow_detail` 恢复

### 4.3 为什么 LLM AutoCompact 用 NO_TOOLS？

- 摘要过程是纯文本整理，不需要也不应该调用工具
- 摘要时误调工具会：①污染摘要进程 ②额外消耗 token ③可能触发敏感操作
- 加上 `maxTurns:1` 双保险，确保摘要一次完成

### 4.4 为什么需要熔断器？

LLM 摘要可能连续失败（API 不稳定 / 上下文异常）。连续失败 3 次直接回退确定性压缩，避免"压缩本身卡死任务"。无 LLM 配置时完全跳过摘要层。

### 4.5 为什么子 Agent / 指挥官保持确定性压缩？

子 Agent 和指挥官生命周期短（30 步内），上下文规模有限。用 LLM 摘要反而增加延迟和成本，确定性压缩已足够；LLM AutoCompact 只用于长生命周期的主 Agent 循环。

---

## 5. 预期效果

### 5.1 Token 消耗对比（早期实测）

| 场景 | 优化前 | 优化后 | 节省 |
|------|--------|--------|------|
| 10 轮对话 + 20 次工具调用 | ~80K tokens | ~15K tokens | 81% |
| 30 轮对话 + 60 次工具调用 | ~240K（超限） | ~20K tokens | 92% |

### 5.2 固定开销（每次请求）

| 项 | 大小 |
|----|------|
| 41 个工具定义（核心注入 30） | ~24K 字符 ≈ 6.7K tokens |
| SYSTEM_PROMPT（已瘦身 43%） | ~7.2K 字符 ≈ 2K tokens |
| 技能索引 / 记忆索引 | ~4K tokens |

缓解：稳定前缀缓存让这些固定开销"会话内只付一次"；跨任务一致的前缀让 17K 从"每任务付"变"每会话付一次"。

### 5.3 Agent 能力保持

- 漏洞发现：100% 保留（FindingRegistry 结构化存储 + flow_id 引用）
- 测试进度：按需获取不丢失（get_test_coverage / Session Memory）
- 证据检索：100% 保留（DB 完整落库 + get_flow_detail 按需提取）
- 崩溃恢复：checkpoint 自动续跑，不丢消息与上下文
