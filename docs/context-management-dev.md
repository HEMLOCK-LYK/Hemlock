# Hemlock 上下文管理系统开发文档

## 1. 背景与目标

### 1.1 问题分析

当前 Hemlock Agent 的上下文管理存在严重缺陷：

- **消息列表无限增长**：`_build_chat_messages()` 从 `task.steps` 全量恢复所有历史消息，不做任何截断
- **工具输出原文注入**：nmap/sqlmap 等工具的完整输出（数千 tokens）直接塞入 messages
- **无 Token 预计数**：不使用 tiktoken 或任何本地 token 计数，无法在发送前评估上下文大小
- **无压缩/摘要机制**：对话越长，messages 越大，最终超出模型上下文窗口导致 API 失败

### 1.2 设计目标

- **降低 Token 消耗**：通过工具输出蒸馏和消息压缩，减少 60-80% 的 token 浪费
- **保持 Agent 能力**：关键信息（漏洞发现、证据、测试进度）不丢失
- **自动透明**：对现有 Agent 流程侵入最小，开发者无需手动管理上下文
- **按需检索**：完整数据存储在冷记忆中，需要时按需提取

### 1.3 设计原则

参考 Claude Code 的三层压缩机制（MicroCompact → Session Memory Compact → AutoCompact），但针对渗透测试场景做领域专用优化：

1. **Token 预算制**（不按轮次计数）：热记忆 4K tokens，温记忆 8K tokens
2. **发现为中心**（Finding-Centric）：围绕漏洞发现组织记忆，而非对话轮次
3. **工具输出蒸馏**：安全工具输出做领域专用压缩（95%+ 压缩率）
4. **渐进式压缩**：先用零成本策略（蒸馏旧工具输出），再用高成本策略（LLM 摘要）

---

## 2. 架构设计

### 2.1 三级记忆模型

```
┌─────────────────────────────────────────────┐
│              LLM 推理引擎                    │
│         (deepseek-v4-flash)                 │
└─────────────────┬───────────────────────────┘
                  │
┌─────────────────▼───────────────────────────┐
│           上下文组装器 (ContextAssembler)     │
│     Token 预算分配 · 按需检索 · 消息压缩      │
└──────┬──────────┬──────────────┬────────────┘
       │          │              │
┌──────▼──┐ ┌────▼─────┐ ┌──────▼──────┐
│ 热记忆  │ │ 温记忆   │ │  冷记忆     │
│ Hot     │ │ Warm     │ │  Cold       │
│ ~4K tok │ │ ~8K tok  │ │  无上限     │
│         │ │          │ │             │
│ 最近对话│ │ 发现摘要 │ │ 全量流量包  │
│ 最近工具│ │ 阶段进度 │ │ 证据存储库  │
│ 当前任务│ │ 目标画像 │ │ 历史发现索引│
└─────────┘ └──────────┘ └─────────────┘
```

### 2.2 模块划分

新增模块 `core/context_manager.py`，包含以下组件：

| 组件 | 职责 |
|------|------|
| `TokenEstimator` | 本地 Token 估算（字符数 / 3.5 近似） |
| `ToolOutputDistiller` | 工具输出蒸馏（nmap/sqlmap/HTTP 响应等） |
| `FindingRegistry` | 漏洞发现注册表（结构化摘要） |
| `WarmMemoryBuilder` | 温记忆构建器（目标画像 + 发现注册表 + 阶段进度） |
| `ContextAssembler` | 上下文组装器（整合三级记忆，管理 Token 预算） |

### 2.3 与现有系统的集成点

```
agent_service.py
├── _build_chat_messages()  ← 集成点1：调用 ContextAssembler 组装消息
├── chat_with_agent()       ← 集成点2：每轮工具调用后调用 ContextAssembler 压缩
├── AgentOrchestrator.run() ← 集成点3：自主模式同样使用 ContextAssembler
├── run_subagent()          ← 集成点4：子 Agent 每 5 步动态压缩
└── run_coordinator_task()  ← 集成点5：指挥官每 5 步动态压缩

core/pentest_context.py     ← 已有：PentestContext 提供测试进度数据
core/context_manager.py     ← 新增：上下文管理核心模块
```

### 2.4 多 Agent 协作模式的上下文管理

协作模式涉及两类 Agent，各自独立的 messages 列表都会无限增长：

| Agent 类型 | 最大步数 | 典型工具调用数 | 未压缩 token 量 | 压缩策略 |
|-----------|---------|--------------|----------------|---------|
| 指挥官 (Coordinator) | 30 步 | spawn_subagent + 查询工具 | ~60K tokens | 每 5 步检查，超 6K tokens 时压缩 |
| 子 Agent (Scout/Striker 等) | 30 步 | send_request + quick_test 等 | ~120K tokens | 每 5 步检查，超 6K tokens 时压缩 |

**为什么协作模式更需要上下文管理**：

1. **Token 成本倍增**：协作模式下多个 Agent 独立运行，每个 Agent 都有自己的 messages 列表。5 个角色 Agent + 1 个指挥官 = 6 倍 token 消耗
2. **工具输出更密集**：子 Agent 专注执行工具调用（侦察员密集发请求、攻击手密集测试参数），工具输出增长更快
3. **共享上下文优势**：所有子 Agent 共享同一个 PentestContext，温记忆可以自动同步各角色的发现

**集成方式**：

```python
# 子 Agent (run_subagent) 和指挥官 (run_coordinator_task) 中统一添加：
if step_num > 0 and step_num % 5 == 0:
    current_tokens = TokenEstimator.estimate_messages(messages)
    if current_tokens > HOT_BUDGET_TOKENS + 2000:  # 6K tokens 阈值
        assembler = ContextAssembler()
        messages = assembler.compress_messages(messages, pentest_context=context)
```

**为什么单次渗透测试也需要上下文管理**：

即使是一次性任务，渗透测试的 ReAct 循环也会产生大量工具调用：
- 侦察阶段：10-20 次 send_request（探测端点、目录扫描）
- 测试阶段：30-50 次 quick_test_param（每个参数 × 漏洞类型）
- 验证阶段：10-15 次 verify_vulnerability

每次工具调用的 HTTP 响应 body 通常 2-5KB（~1000-1500 tokens），30 步后 messages 可达 100K+ tokens。

---

## 3. 详细设计

### 3.1 Token 估算器 (TokenEstimator)

**策略**：不引入 tiktoken 依赖（安装复杂、模型特定），使用字符数近似估算。

```python
class TokenEstimator:
    # 经验值：英文 ~4 字符/token，中文 ~1.5 字符/token，混合 ~3.5 字符/token
    CHARS_PER_TOKEN = 3.5

    @staticmethod
    def estimate(text: str) -> int:
        if not text:
            return 0
        return max(1, int(len(text) / CHARS_PER_TOKEN))

    @staticmethod
    def estimate_messages(messages: list[dict]) -> int:
        total = 0
        for msg in messages:
            total += TokenEstimator.estimate(msg.get("content", "") or "")
            if msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    total += TokenEstimator.estimate(
                        tc.get("function", {}).get("arguments", "")
                    )
        # 每条消息的元数据开销 ~4 tokens
        return total + len(messages) * 4
```

### 3.2 工具输出蒸馏器 (ToolOutputDistiller)

**核心创新**：针对渗透测试工具的领域专用压缩。

```python
class ToolOutputDistiller:
    """工具输出蒸馏器：将原始工具输出压缩为关键信息摘要。"""

    # 可蒸馏的工具列表（一次性消费型）
    DISTILLABLE_TOOLS = {
        "send_request", "send_session_request", "send_raw_request",
        "analyze_flow", "get_flow_detail", "quick_test_param",
        "run_intruder", "generate_payloads", "get_payload_list",
        "read_file", "call_api",
    }

    @classmethod
    def distill(cls, tool_name: str, tool_args: dict, tool_result: dict) -> str:
        """将工具输出蒸馏为一行摘要。"""
        if tool_name in ("send_request", "send_session_request", "send_raw_request"):
            return cls._distill_http_request(tool_args, tool_result)
        elif tool_name == "analyze_flow":
            return cls._distill_flow_analysis(tool_result)
        elif tool_name == "quick_test_param":
            return cls._distill_quick_test(tool_args, tool_result)
        elif tool_name == "read_file":
            return cls._distill_file_read(tool_args, tool_result)
        else:
            return cls._distill_generic(tool_name, tool_result)
```

**蒸馏规则示例**：

| 工具 | 原始输出 | 蒸馏后 |
|------|---------|--------|
| send_request | ~1500 tokens | `GET /admin 302 → nginx/1.18, Set-Cookie:session=abc, body:2.3KB` |
| analyze_flow | ~2000 tokens | `Flow #42: POST /api/login, param:user/pass, resp:200, 1.2KB JSON` |
| quick_test_param | ~3000 tokens | `SQLi test /api/user?id=: boolean-blind detected, payload:' AND 1=1--` |
| read_file | ~5000 tokens | `[read_file] config.yml (4.8KB), key sections: database, secrets` |
| 通用 | ~1000 tokens | `[tool_name] result: success, 2 items, 1.5KB` |

### 3.3 发现注册表 (FindingRegistry)

```python
class FindingRegistry:
    """漏洞发现注册表：结构化存储所有发现，每条仅 1-2 行。"""

    def __init__(self):
        self.findings: list[FindingEntry] = []

    def add(self, finding_id, vuln_type, endpoint, severity, status, title=""):
        """添加或更新一个发现。"""

    def build_summary(self) -> str:
        """构建发现注册表摘要文本（注入温记忆）。"""
        # 输出格式：
        # ## 发现注册表 (3 个)
        # F-001 | SQL注入 | /api/user?id= | 高危 | 已验证
        # F-002 | XSS | /search?q= | 中危 | 待验证
```

### 3.4 温记忆构建器 (WarmMemoryBuilder)

```python
class WarmMemoryBuilder:
    """温记忆构建器：整合目标画像、发现注册表、阶段进度。"""

    @staticmethod
    def build(pentest_context: PentestContext, findings: list) -> str:
        """构建温记忆文本（~8K tokens 上限）。"""
        sections = []
        sections.append(WarmMemoryBuilder._build_target_profile(pentest_context))
        sections.append(FindingRegistry.from_context(pentest_context).build_summary())
        sections.append(WarmMemoryBuilder._build_stage_progress(pentest_context))
        return "\n\n".join(sections)
```

### 3.5 上下文组装器 (ContextAssembler)

**核心组件**：整合三级记忆，管理 Token 预算。

```python
class ContextAssembler:
    """上下文组装器：管理三级记忆，按 Token 预算组装 LLM 消息。"""

    HOT_BUDGET = 4000   # 热记忆预算
    WARM_BUDGET = 8000  # 温记忆预算
    SAFETY_MARGIN = 2000  # 安全余量

    def assemble(self, system_prompt, steps, pentest_context, findings) -> list[dict]:
        """
        组装发送给 LLM 的完整消息列表。

        策略：
        1. system prompt 原样保留（不压缩）
        2. 温记忆注入为 system prompt 的一部分
        3. 热记忆：从 steps 末尾向前取，直到用完 HOT_BUDGET
        4. 旧的工具输出用蒸馏摘要替换
        5. 当热记忆超预算时，最旧的消息被压缩为摘要
        """
```

**组装流程**：

```
1. 构建 system_prompt（含温记忆摘要）
   → system_prompt + target_profile + finding_registry + stage_progress
   → 估算 token 数，若超 WARM_BUDGET 则截断发现详情

2. 从 steps 末尾向前扫描，构建热记忆
   → 遇到 tool_call：检查是否在热记忆窗口内
     - 在窗口内：保留原文
     - 超出窗口：蒸馏为一行摘要，替换原文
   → 遇到 user/assistant message：保留原文
   → 累计 token 数，达到 HOT_BUDGET 时停止向前扫描

3. 输出消息列表
   [system(warm)] + [distilled_old_messages] + [recent_hot_messages] + [new_user_message]
```

### 3.6 消息压缩策略（渐进式）

参考 Claude Code 的三层压缩，但适配渗透测试场景：

**第一层：工具输出蒸馏（零 API 调用）**
- 每次构建消息时自动执行
- 旧工具输出（超出热记忆窗口的）替换为蒸馏摘要
- 类似 Claude Code 的 MicroCompact

**第二层：温记忆注入（零 API 调用）**
- 从 PentestContext 提取结构化摘要
- 包含目标画像、发现注册表、阶段进度
- 类似 Claude Code 的 Session Memory Compact

**第三层：LLM 摘要（一次 API 调用，可选）**
- 当热记忆+温记忆仍超预算时触发
- 调用轻量模型将旧对话压缩为结构化摘要
- 类似 Claude Code 的 AutoCompact
- 预留接口，暂不实现（前两层已能覆盖 90%+ 场景）

---

## 4. 实现计划

### 4.1 新增文件

| 文件 | 内容 |
|------|------|
| `core/context_manager.py` | 上下文管理核心模块（所有组件） |

### 4.2 修改文件

| 文件 | 修改点 |
|------|--------|
| `services/agent_service.py` | `_build_chat_messages()` 调用 ContextAssembler；`chat_with_agent()` 在工具循环中动态压缩；`AgentOrchestrator.run()` 自主模式压缩；`run_subagent()` 子 Agent 动态压缩；`run_coordinator_task()` 指挥官动态压缩 |

### 4.3 实现步骤

1. **创建 `core/context_manager.py`**
   - TokenEstimator
   - ToolOutputDistiller（含 HTTP 请求/响应/文件读取/通用蒸馏器）
   - FindingRegistry
   - WarmMemoryBuilder
   - ContextAssembler

2. **修改 `_build_chat_messages()`**
   - 在构建完原始 messages 后，调用 ContextAssembler 压缩
   - 保持 API 向后兼容（返回值仍为 `list[dict]`）

3. **修改 `chat_with_agent()`**
   - 在工具循环中，每 N 次工具调用后检查 messages 大小
   - 超预算时触发压缩

4. **测试**
   - 单元测试：TokenEstimator、ToolOutputDistiller、ContextAssembler
   - 集成测试：模拟长对话场景，验证压缩效果

---

## 5. 关键设计决策

### 5.1 为什么不用 tiktoken？

- tiktoken 安装在某些环境有问题（Windows + Python 3.12）
- tiktoken 是模型特定的（不同模型 tokenizer 不同）
- 字符数近似（/3.5）在预算管理场景下精度足够
- Claude Code 也不在本地精确计数，而是用 API 返回的 usage

### 5.2 为什么温记忆注入 system prompt 而非 user message？

- system prompt 在整个对话中只出现一次，不随消息增长
- 温记忆是"全局参考"性质的信息，不是某个对话轮次的内容
- 避免 user message 中出现大段系统信息干扰 LLM

### 5.3 为什么蒸馏而非删除旧工具输出？

- 渗透测试是证据驱动的：之前的扫描结果可能影响后续决策
- 完全删除会导致 LLM "失忆"，重复执行相同测试
- 蒸馏保留了关键信息（状态码、参数、发现），只去掉原始数据
- 原始数据仍存储在 task.steps（冷记忆），需要时可恢复

### 5.4 为什么暂不实现 LLM 摘要（第三层）？

- 前两层（蒸馏+温记忆）已能覆盖 90%+ 场景
- LLM 摘要需要额外 API 调用，增加延迟和成本
- 留出接口，未来需要时可快速实现

---

## 6. 预期效果

### 6.1 Token 消耗对比

| 场景 | 当前 | 优化后 | 节省 |
|------|------|--------|------|
| 10 轮对话 + 20 次工具调用 | ~80K tokens | ~15K tokens | 81% |
| 30 轮对话 + 60 次工具调用 | ~240K tokens（超限） | ~20K tokens | 92% |
| 简单问答（5 轮无工具） | ~5K tokens | ~5K tokens | 0% |

### 6.2 Agent 能力保持

- 漏洞发现：100% 保留（FindingRegistry 结构化存储）
- 测试进度：100% 保留（PentestContext → 温记忆）
- 证据检索：100% 保留（冷记忆 + 按需提取）
- 对话上下文：最近 N 轮完整保留（Token 预算制）
