import { useState, useEffect, useCallback, useRef, memo, useMemo } from "react";
import type { ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import {
  Plus,
  RefreshCw,
  Send,
  Bot,
  User as UserIcon,
  Wrench,
  ChevronDown,
  ChevronRight,
  CheckCircle2,
  Info,
  X,
  Trash2,
  Zap,
  Square,
  Brain,
  Users,
  Layers,
  ShieldAlert,
  FileText,
  Copy,
  Check,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { useWebSocket } from "../hooks/useWebSocket";
import { agentApi, agentChatApi } from "../api/agent";
import { settingsApi } from "../api/settings";
import { collabApi, type RoleSummary } from "../api/collab";
import { llmProvidersApi, type LLMProvider } from "../api/llmProviders";
import { useProject } from "../context/ProjectContext";
import { usePacket } from "../context/PacketContext";
import type {
  TaskSummary,
  TaskResponse,
  WSEvent,
  StepData,
  ChatToolCall,
} from "../types";
import { Spinner, EmptyState } from "../components/Common";

// ===== 常量 =====

const STATUS_LABELS: Record<string, string> = {
  idle: "待开始",
  running: "运行中",
  paused: "等待确认",
  done: "已完成",
  failed: "失败",
};

const STATUS_COLORS: Record<string, string> = {
  idle: "var(--text-muted)",
  running: "var(--accent-green)",
  paused: "var(--accent-yellow)",
  done: "var(--accent-blue)",
  failed: "var(--accent-red)",
};

// ===== 类型定义 =====

interface ParsedMessage {
  id: string;
  role: "user" | "assistant" | "system" | "tool_call" | "thinking";
  content: string;
  tool?: { tool: string; args: unknown; result: unknown };
  timestamp: string;
  streaming?: boolean; // 标记是否正在流式输出
}

interface ToolInfo {
  tool: string;
  args: unknown;
  result: unknown;
}

// ===== 工具函数 =====

/** 解析 step.data，兼容字符串(JSON)与对象两种形态 */
function parseStepData(data: unknown): {
  tool?: string;
  args?: unknown;
  result?: unknown;
  text?: string;
} {
  if (typeof data === "string") {
    try {
      const parsed = JSON.parse(data);
      if (
        parsed &&
        typeof parsed === "object" &&
        (parsed.tool !== undefined ||
          parsed.args !== undefined ||
          parsed.result !== undefined)
      ) {
        return {
          tool: parsed.tool,
          args: parsed.args,
          result: parsed.result,
        };
      }
      // 解析成功但不是工具调用结构，回退为文本
      return { text: typeof parsed === "string" ? parsed : data };
    } catch {
      return { text: data };
    }
  }
  if (data && typeof data === "object") {
    const obj = data as Record<string, unknown>;
    if (
      obj.tool !== undefined ||
      obj.args !== undefined ||
      obj.result !== undefined
    ) {
      return { tool: obj.tool as string, args: obj.args, result: obj.result };
    }
    return { text: JSON.stringify(obj, null, 2) };
  }
  return { text: String(data ?? "") };
}

/** 将 task.steps 转换为可渲染的消息列表 */
function stepsToMessages(steps: StepData[]): ParsedMessage[] {
  return steps.map((step, i) => {
    if (step.type === "tool_call") {
      const d = parseStepData(step.data);
      return {
        id: `step-${i}`,
        role: "tool_call",
        content: "",
        tool: {
          tool: d.tool || "unknown",
          args: d.args ?? null,
          result: d.result ?? null,
        },
        timestamp: step.timestamp,
      };
    }

    // thinking 类型：中间思考过程，默认折叠，点击可展开
    if (step.type === "thinking") {
      const d = parseStepData(step.data);
      return {
        id: `step-${i}`,
        role: "thinking",
        content: d.text ?? "",
        timestamp: step.timestamp,
      };
    }

    let role: ParsedMessage["role"] = "system";
    if (step.type === "user_message" || step.type === "user") {
      role = "user";
    } else if (
      step.type === "assistant_message" ||
      step.type === "assistant" ||
      step.type === "complete"
    ) {
      role = "assistant";
    } else if (step.type === "system_message" || step.type === "system") {
      role = "system";
    } else if (step.type === "error" || step.type === "limit") {
      role = "system";
    }

    const d = parseStepData(step.data);
    return {
      id: `step-${i}`,
      role,
      content: d.text ?? "",
      timestamp: step.timestamp,
    };
  });
}

/** 格式化字节数 */
function formatBytes(bytes: number): string {
  if (!bytes || bytes < 0) return "0B";
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`;
}

/** 从工具结果中提取简短预览摘要 */
function summarizeResult(result: unknown): string {
  if (result == null) return "";
  let obj: Record<string, unknown> | null = null;
  if (typeof result === "string") {
    try {
      const parsed = JSON.parse(result);
      if (parsed && typeof parsed === "object") obj = parsed as Record<string, unknown>;
      else return result.length > 60 ? result.slice(0, 60) + "…" : result;
    } catch {
      return result.length > 60 ? result.slice(0, 60) + "…" : result;
    }
  } else if (typeof result === "object") {
    obj = result as Record<string, unknown>;
  }
  if (!obj) return "";

  if (obj.status !== undefined) {
    let s = `${obj.status}`;
    if (obj.length !== undefined) s += ` | ${formatBytes(Number(obj.length))}`;
    if (obj.flow_id) s += ` | flow`;
    return s;
  }
  if (obj.error) return `错误: ${String(obj.error).slice(0, 50)}`;
  if (obj.vulnerable === true) return "检测到漏洞";
  if (obj.anomalies && Array.isArray(obj.anomalies))
    return obj.anomalies.length > 0
      ? `检测到 ${obj.anomalies.length} 个异常`
      : "未发现异常";
  if (obj.findings && Array.isArray(obj.findings))
    return `发现 ${obj.findings.length} 个漏洞`;
  return "";
}

/** 将结果文本中的 flow_id 渲染为可点击链接 */
function renderResultWithLinks(
  text: string,
  onFlowClick: (flowId: string) => void
): ReactNode {
  if (!text) return null;
  const regex = /"flow_id"\s*:\s*"([^"]+)"/g;
  const parts: ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let key = 0;
  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    const flowId = match[1];
    parts.push(
      <a
        key={`flow-${key++}`}
        className="agent-chat-flow-link"
        onClick={(e) => {
          e.stopPropagation();
          onFlowClick(flowId);
        }}
        title={`查看流量详情 ${flowId}`}
      >
        {match[0]}
      </a>
    );
    lastIndex = regex.lastIndex;
  }
  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }
  return <>{parts}</>;
}

// ===== 主组件 =====

export function AgentView() {
  const { currentProject } = useProject();
  const navigate = useNavigate();
  const { pendingPacket, target, consume, sendToRepeater } = usePacket();

  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [selectedTask, setSelectedTask] = useState<TaskResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [input, setInput] = useState("");
  // 多任务并行：每个任务独立的乐观消息
  const [optimisticByTask, setOptimisticByTask] = useState<Record<string, ParsedMessage[]>>({});
  // 多任务并行：正在等待 AI 回复的任务集合
  const [runningTasks, setRunningTasks] = useState<Set<string>>(new Set());
  // 从其他页面转发来的待发送数据包（预存到输入框，用户自行决定指令）
  const [pendingPreview, setPendingPreview] = useState<string | null>(null);
  // 删除操作中的提示信息
  const [deleteError, setDeleteError] = useState<string | null>(null);
  // Agent 放入 Repeater 的提示：用户点击"查看"才跳转，不强制跳转
  const [repeaterNotice, setRepeaterNotice] = useState<{ method: string; url: string } | null>(null);
  // 自动化渗透测试模式是否开启
  const [autoPentestEnabled, setAutoPentestEnabled] = useState(false);
  const [togglingAutoPentest, setTogglingAutoPentest] = useState(false);
  // P1 修复：敏感操作确认状态
  const [pendingConfirmation, setPendingConfirmation] = useState<{
    taskId: string;
    tool: string;
    args: Record<string, unknown>;
    message: string;
  } | null>(null);

  // ===== 多 Agent 协作模式 =====
  const [collabMode, setCollabMode] = useState(false);
  const [collabRoles, setCollabRoles] = useState<RoleSummary[]>([]);
  const [collabProviders, setCollabProviders] = useState<LLMProvider[]>([]);
  const [selectedRoles, setSelectedRoles] = useState<Set<string>>(new Set());
  const [roleProviders, setRoleProviders] = useState<Record<string, string>>({});
  const [collabTargetUrl, setCollabTargetUrl] = useState("");
  const [collabGoal, setCollabGoal] = useState("");
  const [collabStarting, setCollabStarting] = useState(false);

  // ===== Agent 测试页面 Provider 选择器 =====
  const [chatProviders, setChatProviders] = useState<LLMProvider[]>([]);
  const [selectedProviderId, setSelectedProviderId] = useState<string>("");
  const [defaultProviderId, setDefaultProviderId] = useState<string>("");

  const selectedTaskId = useRef<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const messagesContainerRef = useRef<HTMLDivElement | null>(null);
  const userScrolledUpRef = useRef(false);  // 用户是否向上翻看历史（此时不自动滚到底）
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  // 防止重复处理同一个 AI 数据包
  const aiPacketHandled = useRef(false);
  // 多任务并行：每个任务独立的 AbortController
  const abortControllersRef = useRef<Map<string, AbortController>>(new Map());
  // 跟踪组件是否已挂载，防止卸载后 setState
  const isMountedRef = useRef(true);
  // runningTasks 的 ref 镜像，供 WebSocket 回调读取最新值
  const runningTasksRef = useRef<Set<string>>(new Set());
  // 运行中收到的消息队列（对齐 Claude Code：不打断当前轮，执行完再读）
  const pendingQueueRef = useRef<{ taskId: string; message: string }[]>([]);
  const [queuedCount, setQueuedCount] = useState(0);
  // rAF 批量更新：思维链 delta 累积缓冲区（taskId -> 累积的 delta 字符串）
  const thinkingDeltaBufferRef = useRef<Map<string, string>>(new Map());
  // 定时器 ID，null 表示未调度
  const thinkingRafRef = useRef<number | null>(null);

  const projectId = currentProject?.id ?? null;

  // 加载任务列表
  const refreshTasks = useCallback(async () => {
    setLoading(true);
    try {
      const data = await agentApi.listTasks(50, projectId || undefined);
      if (!isMountedRef.current) return;
      setTasks(data);
      // 任务状态已终止（非 running / paused）→ 从运行集合中清除（修复：任务结束仍显示"工作中"）
      // 注意：对话模式任务完成后状态为 idle（而非 done/failed），也必须清除，
      // 否则输入框会一直锁定（BUG：任务完成但输入框无法输入，必须手动停止）
      const terminal = new Set(
        data
          .filter((t) => t.status !== "running" && t.status !== "paused")
          .map((t) => t.id)
      );
      if (terminal.size > 0) {
        setRunningTasks((prev) => {
          const n = new Set(prev);
          let changed = false;
          terminal.forEach((id) => {
            if (n.delete(id)) changed = true;
          });
          return changed ? n : prev;
        });
      }
      // 自动选中最近一条会话（无论状态），避免用户看到空白聊天区域
      if (data.length > 0 && !selectedTaskId.current) {
        selectedTaskId.current = data[0].id;
        await refreshTaskDetail(data[0].id);
      }
    } catch (e) {
      console.error(e);
    } finally {
      if (isMountedRef.current) setLoading(false);
    }
  }, [projectId]); // eslint-disable-line react-hooks/exhaustive-deps

  // 加载任务详情
  const refreshTaskDetail = useCallback(async (taskId: string) => {
    try {
      const data = await agentApi.getTask(taskId);
      if (!isMountedRef.current) return;
      setSelectedTask(data);
    } catch (e) {
      console.error(e);
    }
  }, []);

  useEffect(() => {
    refreshTasks();
  }, [refreshTasks]);

  // 组件挂载/卸载生命周期
  // 关键：组件卸载时不 abort 请求，后端任务继续运行
  // 回到页面时自动恢复正在运行的任务
  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      // 注意：不 abort 请求！后端任务继续运行
      // 只清理 abortControllers 引用，避免内存泄漏
      abortControllersRef.current.clear();
    };
  }, []);

  // 同步 runningTasks 到 ref，供 WebSocket 回调读取
  useEffect(() => {
    runningTasksRef.current = runningTasks;
  }, [runningTasks]);

  // 回到页面时恢复所有运行中的任务
  useEffect(() => {
    if (!projectId) return;
    // 检查是否有正在运行的任务需要恢复
    const restoreRunningTasks = async () => {
      try {
        const taskList = await agentApi.listTasks(50, projectId);
        if (!isMountedRef.current) return;
        // 找到所有状态为 running 的任务
        const running = taskList.filter((t) => t.status === "running");
        if (running.length > 0) {
          const runningIds = running.map((t) => t.id);
          setRunningTasks(new Set(runningIds));
          runningTasksRef.current = new Set(runningIds);
          // 选中第一个运行中的任务
          if (!selectedTaskId.current) {
            selectedTaskId.current = running[0].id;
            await refreshTaskDetail(running[0].id);
          }
        }
      } catch {
        // 忽略错误
      }
    };
    // 只有当没有选中任务时才尝试恢复
    if (!selectedTaskId.current) {
      restoreRunningTasks();
    }
  }, [projectId]); // eslint-disable-line react-hooks/exhaustive-deps

  // 获取自动化渗透测试开关状态
  useEffect(() => {
    settingsApi
      .get()
      .then((s) => setAutoPentestEnabled(s.auto_pentest ?? false))
      .catch(() => {});
  }, []);

  // 加载 Provider 列表（用于 Agent 测试页面模型选择器）
  useEffect(() => {
    llmProvidersApi
      .list()
      .then((resp) => {
        setChatProviders(resp.providers);
        const def = resp.providers.find((p) => p.is_default);
        if (def) {
          setDefaultProviderId(def.id);
          setSelectedProviderId(def.id);
        }
      })
      .catch(() => {});
  }, []);

  // 切换自动化渗透测试开关
  const handleToggleAutoPentest = async (enabled: boolean) => {
    setTogglingAutoPentest(true);
    const prev = autoPentestEnabled;
    setAutoPentestEnabled(enabled);
    try {
      const updated = await settingsApi.update({ auto_pentest: enabled });
      setAutoPentestEnabled(updated.auto_pentest);
    } catch {
      setAutoPentestEnabled(prev);
    } finally {
      setTogglingAutoPentest(false);
    }
  };

  // ===== 多 Agent 协作模式：加载角色和 Provider =====
  useEffect(() => {
    if (!collabMode) return;
    Promise.all([
      collabApi.listRoles(),
      llmProvidersApi.list(),
    ]).then(([roles, providersResp]) => {
      setCollabRoles(roles);
      setCollabProviders(providersResp.providers);
      // 默认选中全部内置角色
      const builtinRoles = new Set(roles.filter(r => r.is_builtin).map(r => r.name));
      setSelectedRoles(builtinRoles);
    }).catch(() => {});
  }, [collabMode]);

  // 切换角色选中
  const toggleRole = (name: string) => {
    setSelectedRoles(prev => {
      const n = new Set(prev);
      if (n.has(name)) n.delete(name);
      else n.add(name);
      return n;
    });
  };

  // 设置角色的 Provider
  const setRoleProvider = (roleName: string, providerId: string) => {
    setRoleProviders(prev => ({ ...prev, [roleName]: providerId }));
  };

  // 启动协作任务
  const handleStartCollab = async () => {
    if (!collabTargetUrl.trim() || !collabGoal.trim()) return;
    if (selectedRoles.size === 0) return;
    setCollabStarting(true);
    try {
      await collabApi.createCollabTask({
        project_id: projectId || undefined,
        goal: collabGoal,
        target_url: collabTargetUrl,
        roles: Array.from(selectedRoles),
        role_providers: roleProviders,
      });
      // 刷新任务列表
      await refreshTasks();
      // 退出协作面板
      setCollabMode(false);
      setCollabTargetUrl("");
      setCollabGoal("");
      setSelectedRoles(new Set());
      setRoleProviders({});
    } catch (e) {
      console.error("创建协作任务失败:", e);
    } finally {
      setCollabStarting(false);
    }
  };

  // 项目切换时清空当前选中的任务
  useEffect(() => {
    selectedTaskId.current = null;
    setSelectedTask(null);
    setOptimisticByTask({});
    setRunningTasks(new Set());
    runningTasksRef.current = new Set();
    abortControllersRef.current.clear();
  }, [projectId]);

  // 派生消息列表 = 后端 steps + 本地乐观消息（按当前任务隔离）
  // 注意：后端通过 WebSocket 实时推送事件时会刷新 steps，
  // 此时 optimistic 中该任务的消息可能已持久化到 steps 中，需要去重避免重复显示
  const currentTaskId = selectedTask?.id ?? "";
  const currentOptimistic = optimisticByTask[currentTaskId] ?? [];
  // useMemo：只在 selectedTask.steps 变化时重新解析，避免每次输入文字时重建消息对象
  const baseMessages = useMemo(
    () => (selectedTask ? stepsToMessages(selectedTask.steps) : []),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [selectedTask?.id, selectedTask?.steps]
  );

  // 去重：当后端 steps 已包含相同内容/工具调用时，过滤掉 optimistic 中的重复
  const allMessages = useMemo(() => {
    const _baseContentSet = new Set(
      baseMessages
        .filter((m) => m.content)
        .map((m) => m.content)
    );
    const _baseThinkingPrefixes = new Set(
      baseMessages
        .filter((m) => m.role === "thinking" && m.content)
        .map((m) => m.content.slice(0, 100))
    );
    const _baseToolKeys = new Set(
      baseMessages
        .filter((m) => m.role === "tool_call" && m.tool)
        .map((m) => `${m.tool!.tool}:${JSON.stringify(m.tool!.args)}`)
    );
    const _filteredOptimistic = currentOptimistic.filter((m) => {
      if (m.role === "thinking" && m.content) {
        const prefix = m.content.slice(0, 100);
        return !_baseThinkingPrefixes.has(prefix);
      }
      if (m.role === "tool_call" && m.tool) {
        const key = `${m.tool.tool}:${JSON.stringify(m.tool.args)}`;
        return !_baseToolKeys.has(key);
      }
      if (m.content) {
        return !_baseContentSet.has(m.content);
      }
      return true;
    });
    return [...baseMessages, ..._filteredOptimistic];
  }, [baseMessages, currentOptimistic]);

  // 当前选中任务是否正在等待 AI 回复
  const isCurrentTaskRunning = runningTasks.has(currentTaskId);

  // 自动滚动到底部（使用 auto 避免从顶部滚动到底部的视觉闪烁）
  // 修复：用户向上翻看历史（思考链/工具结果）时不强拉回底部，新消息到达只更新不打断阅读
  useEffect(() => {
    if (userScrolledUpRef.current) return;
    messagesEndRef.current?.scrollIntoView({ behavior: "auto" });
  }, [allMessages.length, isCurrentTaskRunning]);

  // 追踪用户滚动位置：距底部 < 80px 视为"在底部"，否则视为向上翻看
  const handleMessagesScroll = () => {
    const el = messagesContainerRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    userScrolledUpRef.current = !nearBottom;
  };

  // 定时批量刷新思维链 delta：将缓冲区中累积的 delta 一次性写入 state
  // 使用 setTimeout(50ms) 而非 rAF，累积更多文本 per update → 显示更丝滑
  const flushThinkingDeltas = useCallback(() => {
    thinkingRafRef.current = null;
    const buffer = thinkingDeltaBufferRef.current;
    if (buffer.size === 0) return;

    const entries = Array.from(buffer.entries());
    buffer.clear();

    setOptimisticByTask((prev) => {
      let updated = prev;
      for (const [taskId, delta] of entries) {
        const msgs = updated[taskId] ?? [];
        // 查找最后一条正在 streaming 的 thinking 消息
        // 关键：必须检查 m.streaming，避免追加到已完成的旧 thinking 消息
        const lastThinkingIdx = [...msgs].reverse().findIndex(
          (m) => m.role === "thinking" && m.id.startsWith("ws-stream-thinking-") && m.streaming
        );
        if (lastThinkingIdx !== -1) {
          const realIdx = msgs.length - 1 - lastThinkingIdx;
          const newMsgs = [...msgs];
          newMsgs[realIdx] = {
            ...newMsgs[realIdx],
            content: newMsgs[realIdx].content + delta,
          };
          updated = { ...updated, [taskId]: newMsgs };
        } else {
          // 创建新的 streaming thinking 消息
          updated = {
            ...updated,
            [taskId]: [
              ...msgs,
              {
                id: `ws-stream-thinking-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
                role: "thinking" as const,
                content: delta,
                timestamp: new Date().toISOString(),
                streaming: true,
              },
            ],
          };
        }
      }
      return updated;
    });
  }, []);

  // 组件卸载时取消 pending 定时器
  useEffect(() => {
    return () => {
      if (thinkingRafRef.current !== null) {
        clearTimeout(thinkingRafRef.current);
        thinkingRafRef.current = null;
      }
    };
  }, []);

  // WebSocket 实时事件
  const handleWsEvent = useCallback(
    (event: WSEvent) => {
      if (!event.event) return;
      const taskId = event.task_id as string | undefined;

      // Agent 构造数据包发送到 Repeater（静默载入，不强制跳转，仅轻提示）
      if (event.event === "send_to_repeater") {
        const packet = event.packet as {
          method: string;
          url: string;
          headers: string;
          body: string;
        } | undefined;
        if (packet) {
          sendToRepeater({
            method: packet.method,
            url: packet.url,
            headers: packet.headers || "",
            body: packet.body || "",
          });
          // 请求已静默放入 Repeater；展示提示，用户点击"查看"才跳转
          setRepeaterNotice({ method: packet.method, url: packet.url });
          window.setTimeout(() => setRepeaterNotice(null), 8000);
        }
        return;
      }

      // 实时流式更新：thinking_delta / tool_call / tool_result
      // 流式思考链：thinking_delta 增量追加到同一条消息（类似 Codex / Claude Code）
      if (taskId && taskId === selectedTaskId.current) {
        const now = new Date().toISOString();

        // 流式思考增量：通过定时器批量更新，避免每个 delta 都触发重渲染
        if (event.event === "thinking_delta") {
          const delta = (event.delta as string) || "";
          if (delta) {
            // 累积 delta 到缓冲区
            const current = thinkingDeltaBufferRef.current.get(taskId) || "";
            thinkingDeltaBufferRef.current.set(taskId, current + delta);
            // 调度定时器（150ms 间隔，累积更多文本 per update → 按段落显示而非逐字）
            if (thinkingRafRef.current === null) {
              thinkingRafRef.current = window.setTimeout(flushThinkingDeltas, 150);
            }
          }
          return;
        }

        // thinking_done：用完整内容覆盖，标记流式结束
        // 关键：只查找 streaming=true 的消息，避免修改已完成的旧 thinking
        // 如果没有找到 streaming 消息（rAF 未 flush），创建一条完整的 thinking 消息
        if (event.event === "thinking_done") {
          // 取消 pending 定时器并清空缓冲区
          if (thinkingRafRef.current !== null) {
            clearTimeout(thinkingRafRef.current);
            thinkingRafRef.current = null;
          }
          thinkingDeltaBufferRef.current.delete(taskId);

          const fullContent = (event.content as string) || "";
          setOptimisticByTask((prev) => {
            const msgs = prev[taskId] ?? [];
            const updated = [...msgs];
            let found = false;
            for (let i = updated.length - 1; i >= 0; i--) {
              if (updated[i].id.startsWith("ws-stream-thinking-") && updated[i].streaming) {
                updated[i] = {
                  ...updated[i],
                  content: fullContent || updated[i].content,
                  streaming: false,
                };
                found = true;
                break;
              }
            }
            // 如果没有找到 streaming 消息，创建一条（rAF 可能还未 flush）
            if (!found && fullContent) {
              updated.push({
                id: `ws-stream-thinking-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
                role: "thinking" as const,
                content: fullContent,
                timestamp: new Date().toISOString(),
                streaming: false,
              });
            }
            return { ...prev, [taskId]: updated };
          });
          return;
        }

        // 兼容旧版 thinking 事件（非流式，一次性推送完整内容）
        if (event.event === "thinking") {
          const content = (event.content as string) || "";
          if (content) {
            setOptimisticByTask((prev) => ({
              ...prev,
              [taskId]: [
                ...(prev[taskId] ?? []),
                {
                  id: `ws-thinking-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
                  role: "thinking" as const,
                  content,
                  timestamp: now,
                },
              ],
            }));
          }
          return;
        }

        if (event.event === "tool_call") {
          const toolName = (event.tool as string) || "unknown";
          const toolArgs = event.args;
          setOptimisticByTask((prev) => ({
            ...prev,
            [taskId]: [
              ...(prev[taskId] ?? []),
              {
                id: `ws-toolcall-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
                role: "tool_call" as const,
                content: "",
                tool: {
                  tool: toolName,
                  args: toolArgs,
                  result: null, // 结果稍后通过 tool_result 事件或刷新获取
                },
                timestamp: now,
              },
            ],
          }));
          return;
        }

        if (event.event === "tool_result") {
          // 工具结果到达：更新最近的同名 tool_call 乐观消息的 result
          const toolName = (event.tool as string) || "unknown";
          const toolResult = event.result;
          setOptimisticByTask((prev) => {
            const msgs = prev[taskId] ?? [];
            // 从后往前找到最后一个同名且 result 为 null 的 tool_call
            const idx = [...msgs].reverse().findIndex(
              (m) => m.role === "tool_call" && m.tool?.tool === toolName && m.tool.result === null
            );
            if (idx === -1) return prev;
            const realIdx = msgs.length - 1 - idx;
            const updated = [...msgs];
            updated[realIdx] = {
              ...updated[realIdx],
              tool: { ...updated[realIdx].tool!, result: toolResult },
            };
            return { ...prev, [taskId]: updated };
          });
          return;
        }

        // 其他事件（chat_message 等）：刷新详情同步
        refreshTaskDetail(taskId);
      }

      // P1 修复：捕获敏感操作确认请求
      if (event.event === "confirmation_required" && taskId) {
        setPendingConfirmation({
          taskId,
          tool: (event as Record<string, unknown>).tool as string || "未知操作",
          args: ((event as Record<string, unknown>).args as Record<string, unknown>) || {},
          message: (event as Record<string, unknown>).message as string || "Agent 请求执行敏感操作",
        });
      }
      // 确认完成（批准/拒绝/超时）后清除确认状态
      if (
        (event.event === "tool_result" ||
          event.event === "task_complete" ||
          event.event === "task_error" ||
          event.event === "confirmation_timeout") &&
        pendingConfirmation?.taskId === taskId
      ) {
        setPendingConfirmation(null);
      }

      // 任务终止事件 → 立即清除运行状态（修复：任务结束仍显示"工作中"）
      if (
        (event.event === "task_complete" ||
          event.event === "task_error" ||
          event.event === "coordinator_finished") &&
        taskId
      ) {
        setRunningTasks((prev) => {
          const n = new Set(prev);
          n.delete(taskId);
          return n;
        });
        runningTasksRef.current = new Set(
          [...runningTasksRef.current].filter((id) => id !== taskId)
        );
      }

      // 关键事件刷新任务列表
      if (
        event.event === "task_started" ||
        event.event === "task_complete" ||
        event.event === "task_error" ||
        event.event === "confirmation_required" ||
        event.event === "new_finding" ||
        event.event === "coordinator_started" ||
        event.event === "coordinator_complete" ||
        event.event === "coordinator_finished" ||
        event.event === "subagent_started" ||
        event.event === "subagent_complete" ||
        event.event === "chat_message"
      ) {
        refreshTasks();
      }
    },
    [refreshTaskDetail, refreshTasks, sendToRepeater, navigate, flushThinkingDeltas]
  );

  // WS (重)连成功后重同步：断开期间的事件丢失，刷新任务列表 + 当前任务详情兜底
  const handleWsReconnect = useCallback(() => {
    refreshTasks();
    const cur = selectedTaskId.current;
    if (cur) refreshTaskDetail(cur);
  }, [refreshTaskDetail, refreshTasks]);

  useWebSocket(handleWsEvent, handleWsReconnect);

  // 兜底轮询：WS 异常时任务列表/运行状态仍能刷新（每 15s，桌面应用可接受）
  // 只刷任务列表状态，不重拉详情（避免打断用户正在阅读的内容）
  useEffect(() => {
    const timer = window.setInterval(() => {
      refreshTasks();
    }, 15000);
    return () => window.clearInterval(timer);
  }, [refreshTasks]);

  // 切换任务时的加载状态
  const [taskLoading, setTaskLoading] = useState(false);

  // 选中任务
  const handleSelectTask = (taskId: string) => {
    if (selectedTaskId.current === taskId) return;
    selectedTaskId.current = taskId;
    // 切换任务时清空消息队列（排队的消息属于上一个任务）
    pendingQueueRef.current = [];
    setQueuedCount(0);
    // 先清空当前任务 + 显示加载动画，让旧消息卸载，避免新旧消息同时渲染导致卡顿
    setSelectedTask(null);
    setTaskLoading(true);
    refreshTaskDetail(taskId).finally(() => {
      if (isMountedRef.current) setTaskLoading(false);
    });
  };

  // 删除任务
  const handleDeleteTask = useCallback(async (taskId: string) => {
    setDeleteError(null);
    try {
      await agentChatApi.deleteConversation(taskId);
      // 如果删除的是当前选中的任务，清空选中状态
      if (selectedTaskId.current === taskId) {
        selectedTaskId.current = null;
        setSelectedTask(null);
      }
      // 清理被删除任务的运行状态和乐观消息
      setRunningTasks((prev) => {
        const n = new Set(prev);
        n.delete(taskId);
        return n;
      });
      setOptimisticByTask((prev) => {
        const n = { ...prev };
        delete n[taskId];
        return n;
      });
      abortControllersRef.current.delete(taskId);
      await refreshTasks();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setDeleteError(`删除失败: ${msg}`);
      // 3秒后自动清除错误提示
      setTimeout(() => setDeleteError(null), 3000);
    }
  }, [refreshTasks]);

  // 新建任务
  const handleNewTask = async () => {
    setCreating(true);
    try {
      const task = await agentChatApi.createConversation(projectId, "");
      selectedTaskId.current = task.id;
      setInput("");
      await refreshTasks();
      await refreshTaskDetail(task.id);
      inputRef.current?.focus();
    } catch (e) {
      console.error(e);
    } finally {
      setCreating(false);
    }
  };

  // P1 修复：批准敏感操作
  const handleApprove = async () => {
    if (!pendingConfirmation) return;
    try {
      await agentApi.approveTask(pendingConfirmation.taskId);
    } catch (e) {
      console.error("批准失败:", e);
    }
    setPendingConfirmation(null);
  };

  // P1 修复：拒绝敏感操作
  const handleReject = async () => {
    if (!pendingConfirmation) return;
    try {
      await agentApi.rejectTask(pendingConfirmation.taskId);
    } catch (e) {
      console.error("拒绝失败:", e);
    }
    setPendingConfirmation(null);
  };

  // 发送消息（多任务并行：按任务隔离乐观消息和运行状态）
  const handleSend = async (queuedMessage?: string) => {
    // 有排队消息时直接用；否则读输入框 + 数据包预览
    let message = queuedMessage ?? input.trim();
    if (pendingPreview && !queuedMessage) {
      message = message ? `${message}\n\n${pendingPreview}` : pendingPreview;
    }
    if (!message) return;
    if (!selectedTask) return;
    const taskId = selectedTask.id;

    // Agent 运行中收到新消息：不打断当前轮，进队列，当前轮结束后自动发送（对齐 Claude Code）
    // 只对"用户主动发送"排队；队列排空发来的消息直接发送
    if (!queuedMessage && runningTasksRef.current.has(taskId)) {
      pendingQueueRef.current.push({ taskId, message });
      setQueuedCount(pendingQueueRef.current.length);
      setInput("");
      setPendingPreview(null);
      return;
    }

    const now = new Date().toISOString();

    // 清除预览
    setPendingPreview(null);
    setInput("");

    // 乐观插入用户消息（按任务隔离）
    const userMsg: ParsedMessage = {
      id: `opt-user-${Date.now()}`,
      role: "user",
      content: message,
      timestamp: now,
    };
    setOptimisticByTask((prev) => ({
      ...prev,
      [taskId]: [...(prev[taskId] ?? []), userMsg],
    }));
    // 标记当前任务为运行中
    setRunningTasks((prev) => new Set(prev).add(taskId));

    // 创建 AbortController 用于中断请求
    const controller = new AbortController();
    abortControllersRef.current.set(taskId, controller);

    try {
      const resp = await agentChatApi.chat(taskId, message, controller.signal, selectedProviderId || null);

      // 组件已卸载则不更新 UI（后端任务继续运行）
      if (!isMountedRef.current) return;

      // 插入工具调用 + Agent 回复（按任务隔离）
      const newMsgs: ParsedMessage[] = [];
      if (resp.tool_calls && resp.tool_calls.length > 0) {
        resp.tool_calls.forEach((tc: ChatToolCall, i: number) => {
          newMsgs.push({
            id: `opt-tool-${Date.now()}-${i}`,
            role: "tool_call",
            content: "",
            tool: { tool: tc.tool, args: tc.args, result: tc.result },
            timestamp: tc.timestamp || now,
          });
        });
      }
      if (resp.reply) {
        newMsgs.push({
          id: `opt-agent-${Date.now()}`,
          role: "assistant",
          content: resp.reply,
          timestamp: now,
        });
      }
      setOptimisticByTask((prev) => ({
        ...prev,
        [taskId]: [...(prev[taskId] ?? []), ...newMsgs],
      }));

      // 刷新任务详情以同步后端持久化的 steps
      await refreshTaskDetail(taskId);
      if (!isMountedRef.current) return;
      // 清理当前任务的乐观消息（已持久化到后端 steps）
      // 延迟清理：等待可能还在传输中的 WebSocket 事件（thinking 等）到达
      // 去重逻辑会自动过滤掉已存在于 baseMessages 中的消息
      setTimeout(() => {
        if (!isMountedRef.current) return;
        setOptimisticByTask((prev) => {
          const n = { ...prev };
          delete n[taskId];
          return n;
        });
      }, 500);
    } catch (e) {
      if (!isMountedRef.current) return;
      // 用户主动中断时不显示错误
      if (controller.signal.aborted) {
        setOptimisticByTask((prev) => ({
          ...prev,
          [taskId]: [
            ...(prev[taskId] ?? []),
            {
              id: `opt-stop-${Date.now()}`,
              role: "system",
              content: "对话已中断",
              timestamp: now,
            },
          ],
        }));
      } else {
        console.error(e);
        setOptimisticByTask((prev) => ({
          ...prev,
          [taskId]: [
            ...(prev[taskId] ?? []),
            {
              id: `opt-err-${Date.now()}`,
              role: "system",
              content: `发送失败：${
                e instanceof Error ? e.message : String(e)
              }`,
              timestamp: now,
            },
          ],
        }));
      }
    } finally {
      if (isMountedRef.current) {
        // 移除当前任务的运行状态
        setRunningTasks((prev) => {
          const n = new Set(prev);
          n.delete(taskId);
          return n;
        });
      }
      abortControllersRef.current.delete(taskId);
      // 当前轮结束：自动发送队列中积压的消息（对齐 Claude Code 排队机制）
      if (pendingQueueRef.current.length > 0) {
        const next = pendingQueueRef.current.shift()!;
        setQueuedCount(pendingQueueRef.current.length);
        // 延迟一点，等 running 状态 ref 同步完成、UI 稳定后再发下一条
        window.setTimeout(() => handleSend(next.message), 200);
      }
    }
  };

  // 中断当前选中任务的 Agent 对话
  const handleStop = async () => {
    const taskId = selectedTaskId.current;
    if (!taskId) return;
    // 1. 中断前端 HTTP 请求
    const controller = abortControllersRef.current.get(taskId);
    if (controller) {
      controller.abort();
    }
    // 2. 通知后端取消任务
    try {
      await agentApi.cancelTask(taskId);
    } catch {
      // 后端可能已经完成，忽略错误
    }
    // 3. 清理运行状态
    setRunningTasks((prev) => {
      const n = new Set(prev);
      n.delete(taskId);
      return n;
    });
  };

  // 输入框快捷键：Enter 发送，Shift+Enter / Ctrl+Enter 换行
  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleFlowClick = useCallback((flowId: string) => {
    navigate(`/flows/${flowId}`);
  }, [navigate]);

  // ===== 检测从其他页面转发来的 AI 数据包，预存到输入框等待用户决定 =====
  useEffect(() => {
    if (target !== "ai" || !pendingPacket || aiPacketHandled.current) return;
    aiPacketHandled.current = true;

    const pkt = pendingPacket;
    consume();

    // 格式化数据包为预览文本（仅纯数据包内容，指令由用户自行输入）
    const formatPacketPreview = () => {
      let msg = `【请求】\n${pkt.method} ${pkt.url}\n`;
      if (pkt.headers) msg += `${pkt.headers}\n`;
      if (pkt.body) msg += `\n${pkt.body}\n`;
      if (pkt.respStatus !== undefined) {
        msg += `\n【响应】\n状态码: ${pkt.respStatus}\n`;
        if (pkt.respHeaders) msg += `${pkt.respHeaders}\n`;
        if (pkt.respBody) {
          const truncated = pkt.respBody.length > 3000
            ? pkt.respBody.substring(0, 3000) + "\n... (响应体已截断)"
            : pkt.respBody;
          msg += `\n${truncated}\n`;
        }
      }
      return msg;
    };

    (async () => {
      try {
        // 确保有任务存在
        let taskId = selectedTaskId.current;
        if (!taskId) {
          const task = await agentChatApi.createConversation(projectId, "");
          taskId = task.id;
          selectedTaskId.current = task.id;
          await refreshTasks();
          await refreshTaskDetail(task.id);
        }

        // 预存到 pendingPreview，用户可编辑后发送
        const preview = formatPacketPreview();
        setPendingPreview(preview);
        // 焦点到输入框
        setTimeout(() => inputRef.current?.focus(), 300);
      } catch (e) {
        console.error("AI 数据包预存失败:", e);
      } finally {
        // 重置标志，允许下次处理
        setTimeout(() => { aiPacketHandled.current = false; }, 500);
      }
    })();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target, pendingPacket]);

  return (
    <div className="agent-chat-container">
      {/* 顶部头部 */}
      <div className="agent-chat-header">
        <div className="agent-chat-title">
          AI 安全测试
          {autoPentestEnabled && (
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
                marginLeft: 10,
                padding: "2px 8px",
                fontSize: 11,
                fontWeight: 600,
                borderRadius: 10,
                background: "rgba(46, 160, 67, 0.15)",
                color: "var(--accent-green)",
                border: "1px solid var(--accent-green)",
              }}
            >
              <Zap size={11} />
              自动化模式
            </span>
          )}
        </div>
        <div className="agent-chat-actions">
          {/* 模型选择器：根据用户添加的 API Key 选择当前使用的模型 */}
          {chatProviders.length > 0 && (
            <select
              className="agent-chat-provider-select"
              value={selectedProviderId}
              onChange={(e) => setSelectedProviderId(e.target.value)}
              title="选择当前对话使用的模型 / API Key"
              style={{
                padding: "4px 8px",
                background: "var(--bg-tertiary)",
                border: "1px solid var(--border-default)",
                borderRadius: "var(--radius-sm)",
                color: "var(--text-primary)",
                fontSize: 11,
                cursor: "pointer",
                maxHeight: 28,
                maxWidth: 180,
              }}
            >
              {chatProviders.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} ({p.model})
                </option>
              ))}
            </select>
          )}
          {/* 自动化渗透测试开关 */}
          <button
            className="btn btn-sm"
            onClick={() => handleToggleAutoPentest(!autoPentestEnabled)}
            disabled={togglingAutoPentest}
            title={autoPentestEnabled ? "关闭自动化渗透测试" : "开启自动化渗透测试"}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              background: autoPentestEnabled
                ? "rgba(46, 160, 67, 0.12)"
                : "var(--bg-tertiary)",
              border: autoPentestEnabled
                ? "1px solid var(--accent-green)"
                : "1px solid var(--border-default)",
              color: autoPentestEnabled
                ? "var(--accent-green)"
                : "var(--text-secondary)",
            }}
          >
            <Zap size={12} />
            <span style={{ fontSize: 12 }}>
              {togglingAutoPentest ? "..." : autoPentestEnabled ? "自动测试已开" : "自动测试"}
            </span>
          </button>
          {/* 多 Agent 协作模式开关 */}
          <button
            className="btn btn-sm"
            onClick={() => setCollabMode(!collabMode)}
            title="多 Agent 协作模式"
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              background: collabMode
                ? "rgba(137, 87, 229, 0.12)"
                : "var(--bg-tertiary)",
              border: collabMode
                ? "1px solid var(--accent-purple)"
                : "1px solid var(--border-default)",
              color: collabMode
                ? "var(--accent-purple)"
                : "var(--text-secondary)",
            }}
          >
            <Layers size={12} />
            <span style={{ fontSize: 12 }}>
              {collabMode ? "协作模式已开" : "协作模式"}
            </span>
          </button>
          {/* 查看报告：跳转到漏洞管理页面 */}
          <button
            className="btn btn-sm"
            onClick={() => navigate("/findings")}
            title="查看漏洞报告"
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              background: "var(--bg-tertiary)",
              border: "1px solid var(--border-default)",
              color: "var(--text-secondary)",
            }}
          >
            <FileText size={12} />
            <span style={{ fontSize: 12 }}>报告</span>
          </button>
          <button
            className="btn btn-sm"
            onClick={refreshTasks}
            disabled={loading}
          >
            {loading ? <Spinner size={12} /> : <RefreshCw size={12} />}
            刷新
          </button>
          <button
            className="btn btn-primary btn-sm"
            onClick={handleNewTask}
            disabled={creating}
          >
            {creating ? <Spinner size={12} /> : <Plus size={12} />}
            新建任务
          </button>
        </div>
      </div>

      {/* 主体：左侧任务列表 + 右侧聊天区域 */}
      <div className="agent-chat-body">
        {/* 左侧：任务列表 */}
        <div className="agent-chat-sidebar">
          <div className="agent-chat-sidebar-header">
            测试任务 ({tasks.length})
          </div>
          {deleteError && (
            <div
              style={{
                padding: "6px 10px",
                fontSize: 11,
                color: "var(--accent-red)",
                background: "rgba(248,81,73,0.1)",
                borderBottom: "1px solid var(--border-default)",
              }}
            >
              {deleteError}
            </div>
          )}
          <div className="agent-chat-sidebar-list">
            {tasks.length === 0 ? (
              <EmptyState
                icon={<Bot size={32} />}
                message="暂无任务，点击「新建任务」开始安全测试"
              />
            ) : (
              tasks.map((task) => (
                <TaskListItem
                  key={task.id}
                  task={task}
                  active={selectedTaskId.current === task.id}
                  isRunning={runningTasks.has(task.id)}
                  onClick={() => handleSelectTask(task.id)}
                  onDelete={() => handleDeleteTask(task.id)}
                />
              ))
            )}
          </div>
        </div>

        {/* 右侧：聊天区域 / 协作任务创建面板 */}
        <div className="agent-chat-main">
          {collabMode ? (
            /* ===== 多 Agent 协作任务创建面板 ===== */
            <div style={{ padding: 16, overflow: "auto", height: "100%" }}>
              <div className="flex items-center gap-2 mb-4">
                <Users size={16} style={{ color: "var(--accent-purple)" }} />
                <span style={{ fontSize: 14, fontWeight: 500 }}>多 Agent 协作任务</span>
                <span className="text-xs text-muted" style={{ marginLeft: 8 }}>
                  选择角色和 API Key，启动多 Agent 协同测试
                </span>
              </div>

              {/* 目标 URL */}
              <div style={{ marginBottom: 12 }}>
                <label style={{ display: "block", fontSize: 12, color: "var(--text-secondary)", marginBottom: 4 }}>
                  目标 URL
                </label>
                <input
                  style={{
                    width: "100%",
                    padding: "8px 10px",
                    background: "var(--bg-tertiary)",
                    border: "1px solid var(--border-default)",
                    borderRadius: "var(--radius-sm)",
                    color: "var(--text-primary)",
                    fontSize: 13,
                    outline: "none",
                  }}
                  value={collabTargetUrl}
                  onChange={(e) => setCollabTargetUrl(e.target.value)}
                  placeholder="https://example.com"
                />
              </div>

              {/* 任务目标 */}
              <div style={{ marginBottom: 12 }}>
                <label style={{ display: "block", fontSize: 12, color: "var(--text-secondary)", marginBottom: 4 }}>
                  任务目标
                </label>
                <textarea
                  style={{
                    width: "100%",
                    padding: "8px 10px",
                    background: "var(--bg-tertiary)",
                    border: "1px solid var(--border-default)",
                    borderRadius: "var(--radius-sm)",
                    color: "var(--text-primary)",
                    fontSize: 13,
                    outline: "none",
                    minHeight: 60,
                    resize: "vertical",
                  }}
                  value={collabGoal}
                  onChange={(e) => setCollabGoal(e.target.value)}
                  placeholder="对该目标进行全面安全测试"
                />
              </div>

              {/* 角色选择 */}
              <div style={{ marginBottom: 12 }}>
                <label style={{ display: "block", fontSize: 12, color: "var(--text-secondary)", marginBottom: 6 }}>
                  参与角色 ({selectedRoles.size} 个已选)
                </label>
                <div className="flex flex-col gap-2">
                  {collabRoles.map((role) => {
                    const isSelected = selectedRoles.has(role.name);
                    return (
                      <div
                        key={role.name}
                        className="card"
                        style={{
                          padding: "10px 12px",
                          display: "flex",
                          alignItems: "center",
                          gap: 10,
                          cursor: "pointer",
                          borderColor: isSelected ? "var(--accent-purple)" : "var(--border-default)",
                          background: isSelected ? "rgba(137, 87, 229, 0.06)" : "var(--bg-secondary)",
                        }}
                        onClick={() => toggleRole(role.name)}
                      >
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => {}}
                          style={{ cursor: "pointer" }}
                        />
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div className="flex items-center gap-2">
                            <span style={{ fontSize: 13, fontWeight: 500 }}>{role.label}</span>
                            <span className="text-xs text-muted font-mono">{role.name}</span>
                            {role.is_builtin && (
                              <span style={{ fontSize: 10, padding: "1px 5px", borderRadius: 999, background: "var(--bg-tertiary)", color: "var(--text-muted)", border: "1px solid var(--border-default)" }}>
                                内置
                              </span>
                            )}
                          </div>
                          <div className="text-xs text-muted" style={{ marginTop: 2 }}>{role.description}</div>
                        </div>
                        {/* API Key 选择 */}
                        {isSelected && collabProviders.length > 0 && (
                          <select
                            style={{
                              padding: "4px 8px",
                              background: "var(--bg-tertiary)",
                              border: "1px solid var(--border-default)",
                              borderRadius: "var(--radius-sm)",
                              color: "var(--text-primary)",
                              fontSize: 11,
                              cursor: "pointer",
                              flexShrink: 0,
                            }}
                            value={roleProviders[role.name] || ""}
                            onChange={(e) => setRoleProvider(role.name, e.target.value)}
                            onClick={(e) => e.stopPropagation()}
                          >
                            <option value="">默认 API Key</option>
                            {collabProviders.map((p) => (
                              <option key={p.id} value={p.id}>
                                {p.name} ({p.model})
                              </option>
                            ))}
                          </select>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* 提示 */}
              <div
                style={{
                  padding: "8px 12px",
                  background: "var(--bg-tertiary)",
                  borderRadius: "var(--radius-sm)",
                  fontSize: 11,
                  color: "var(--text-muted)",
                  marginBottom: 12,
                }}
              >
                指挥官将依次启动选中的角色 Agent，各角色通过共享上下文协作完成测试。
                为不同角色选择不同 API Key 可实现模型能力差异化。
              </div>

              {/* 启动按钮 */}
              <div className="flex gap-2">
                <button
                  className="btn btn-primary btn-sm"
                  onClick={handleStartCollab}
                  disabled={
                    collabStarting ||
                    !collabTargetUrl.trim() ||
                    !collabGoal.trim() ||
                    selectedRoles.size === 0
                  }
                >
                  {collabStarting ? <Spinner size={13} /> : <Layers size={13} />}
                  {collabStarting ? "启动中..." : "启动协作任务"}
                </button>
                <button
                  className="btn btn-sm"
                  onClick={() => setCollabMode(false)}
                >
                  取消
                </button>
              </div>
            </div>
          ) : taskLoading ? (
            <div className="agent-chat-task-panel">
              <div className="agent-chat-messages" style={{ display: "flex", alignItems: "center", justifyContent: "center" }}>
                <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 12, color: "var(--text-muted)" }}>
                  <Spinner size={28} />
                  <span style={{ fontSize: 13 }}>加载对话...</span>
                </div>
              </div>
            </div>
          ) : selectedTask ? (
            <div key={selectedTask.id} className="agent-chat-task-panel">
              {/* 消息列表 */}
              <div className="agent-chat-messages" ref={messagesContainerRef} onScroll={handleMessagesScroll}>
                {allMessages.length === 0 ? (
                  <EmptyState
                    icon={<Bot size={40} />}
                    message={
                      autoPentestEnabled
                        ? "输入「帮我测试 https://xxx.com」即可自动启动安全测试"
                        : "输入消息或测试指令开始..."
                    }
                  />
                ) : (
                  allMessages.map((msg) => (
                    <MessageRenderer
                      key={msg.id}
                      message={msg}
                      onFlowClick={handleFlowClick}
                    />
                  ))
                )}
                {isCurrentTaskRunning && (
                  <div className="agent-chat-typing">
                    <Bot size={14} />
                    <span>Agent 思考中...</span>
                  </div>
                )}
                <div ref={messagesEndRef} />
              </div>

              {/* P1 修复：敏感操作确认栏 */}
              {pendingConfirmation && pendingConfirmation.taskId === selectedTask.id && (
                <div
                  style={{
                    padding: "10px 14px",
                    background: "var(--bg-secondary)",
                    borderTop: "1px solid var(--border-default)",
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                    animation: "slideUp 0.3s var(--ease-out-quart)",
                  }}
                >
                  <ShieldAlert size={16} style={{ color: "var(--accent-yellow)", flexShrink: 0 }} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>
                      Agent 请求执行敏感操作：{pendingConfirmation.tool}
                    </div>
                    <div style={{ fontSize: 11, color: "var(--text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {pendingConfirmation.message}
                    </div>
                  </div>
                  <button
                    onClick={handleReject}
                    style={{
                      padding: "5px 14px",
                      fontSize: 12,
                      fontWeight: 500,
                      border: "1px solid var(--accent-red)",
                      borderRadius: "var(--radius-sm)",
                      background: "transparent",
                      color: "var(--accent-red)",
                      cursor: "pointer",
                      flexShrink: 0,
                    }}
                  >
                    拒绝
                  </button>
                  <button
                    onClick={handleApprove}
                    style={{
                      padding: "5px 14px",
                      fontSize: 12,
                      fontWeight: 600,
                      border: "none",
                      borderRadius: "var(--radius-sm)",
                      background: "var(--accent-green)",
                      color: "#fff",
                      cursor: "pointer",
                      flexShrink: 0,
                    }}
                  >
                    批准执行
                  </button>
                </div>
              )}

              {/* Agent 放入 Repeater 确认栏（复用安全确认 UI 样式，用户同意才跳转） */}
              {repeaterNotice && (
                <div
                  style={{
                    padding: "10px 14px",
                    background: "var(--bg-secondary)",
                    borderTop: "1px solid var(--border-default)",
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                    animation: "slideUp 0.3s var(--ease-out-quart)",
                  }}
                >
                  <Info size={16} style={{ color: "var(--accent-cyan)", flexShrink: 0 }} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>
                      请求已放入重放器：{repeaterNotice.method} {repeaterNotice.url}
                    </div>
                    <div style={{ fontSize: 11, color: "var(--text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      Agent 已构造请求，是否前往重放器查看？
                    </div>
                  </div>
                  <button
                    onClick={() => setRepeaterNotice(null)}
                    style={{
                      padding: "5px 14px",
                      fontSize: 12,
                      fontWeight: 500,
                      border: "1px solid var(--accent-red)",
                      borderRadius: "var(--radius-sm)",
                      background: "transparent",
                      color: "var(--accent-red)",
                      cursor: "pointer",
                      flexShrink: 0,
                    }}
                  >
                    稍后再说
                  </button>
                  <button
                    onClick={() => {
                      setRepeaterNotice(null);
                      navigate("/repeater");
                    }}
                    style={{
                      padding: "5px 14px",
                      fontSize: 12,
                      fontWeight: 600,
                      border: "none",
                      borderRadius: "var(--radius-sm)",
                      background: "var(--accent-green)",
                      color: "#fff",
                      cursor: "pointer",
                      flexShrink: 0,
                    }}
                  >
                    前往查看
                  </button>
                </div>
              )}

              {/* 输入区域 */}
              <div className="agent-chat-input-area">
                {/* 运行中消息已排队提示（对齐 Claude Code：不打断，当前轮完成后自动发送） */}
                {queuedCount > 0 && (
                  <div style={{ fontSize: 11, color: "var(--accent-yellow)", padding: "2px 2px 6px" }}>
                    {queuedCount} 条消息已排队，当前任务完成后自动发送…
                  </div>
                )}
                {/* 待发送数据包预览（可折叠） */}
                {pendingPreview && (
                  <PendingPacketPreview
                    preview={pendingPreview}
                    onRemove={() => setPendingPreview(null)}
                  />
                )}
                <textarea
                  ref={inputRef}
                  className="agent-chat-input"
                  placeholder={
                    pendingPreview
                      ? "输入指令（可选），Enter 发送数据包给 AI..."
                      : autoPentestEnabled
                      ? "输入「帮我测试 https://xxx.com」即可自动启动安全测试... (Enter 发送，Shift+Enter 换行)"
                      : "输入消息或测试指令... (Enter 发送，Shift+Enter 换行)"
                  }
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  rows={1}
                />
                {isCurrentTaskRunning ? (
                  <button
                    className="btn btn-danger agent-chat-stop"
                    onClick={handleStop}
                    title="中断当前任务对话"
                  >
                    <Square size={14} />
                    停止
                  </button>
                ) : (
                  <button
                    className="btn btn-primary agent-chat-send"
                    onClick={() => handleSend()}
                    disabled={(!input.trim() && !pendingPreview) || isCurrentTaskRunning}
                  >
                    <Send size={14} />
                    发送
                  </button>
                )}
              </div>
            </div>
          ) : (
            <EmptyState
              icon={<Bot size={40} />}
              message={
                creating ? "正在创建..." : "点击「新建任务」开始安全测试"
              }
            />
          )}
        </div>
      </div>
    </div>
  );
}

// ===== 子组件：任务列表项 =====

interface TaskListItemProps {
  task: TaskSummary;
  active: boolean;
  isRunning: boolean;
  onClick: () => void;
  onDelete: () => void;
}

function TaskListItem({
  task,
  active,
  isRunning,
  onClick,
  onDelete,
}: TaskListItemProps) {
  const [confirmDelete, setConfirmDelete] = useState(false);

  return (
    <div
      className={`agent-chat-conv-item ${active ? "active" : ""}`}
      onClick={onClick}
    >
      <div className="agent-chat-conv-goal">{task.goal || "新任务"}</div>
      <div className="agent-chat-conv-meta">
        <span
          className="agent-chat-status-dot"
          style={{
            background: STATUS_COLORS[task.status] || "var(--text-muted)",
            ...(isRunning ? { animation: "pulse-dot 1.5s ease-in-out infinite" } : {}),
          }}
        />
        <span className="agent-chat-conv-status">
          {STATUS_LABELS[task.status] || task.status}
        </span>
        {isRunning && (
          <span
            style={{
              fontSize: 10,
              fontWeight: 600,
              color: "var(--accent-green)",
              display: "inline-flex",
              alignItems: "center",
              gap: 2,
            }}
          >
            <Bot size={9} />
            运行中
          </span>
        )}
        <span className="agent-chat-conv-time">
          {task.started_at
            ? new Date(task.started_at).toLocaleString("zh-CN", {
                month: "2-digit",
                day: "2-digit",
                hour: "2-digit",
                minute: "2-digit",
              })
            : ""}
        </span>
        {/* 删除按钮 */}
        {confirmDelete ? (
          <span
            style={{
              marginLeft: "auto",
              display: "flex",
              gap: 4,
              fontSize: 10,
            }}
          >
            <button
              style={{
                background: "var(--accent-red)",
                color: "#fff",
                border: "none",
                borderRadius: 3,
                padding: "1px 6px",
                cursor: "pointer",
                fontSize: 10,
              }}
              onClick={(e) => {
                e.stopPropagation();
                onDelete();
              }}
            >
              确认
            </button>
            <button
              style={{
                background: "var(--bg-tertiary)",
                color: "var(--text-secondary)",
                border: "1px solid var(--border-default)",
                borderRadius: 3,
                padding: "1px 6px",
                cursor: "pointer",
                fontSize: 10,
              }}
              onClick={(e) => {
                e.stopPropagation();
                setConfirmDelete(false);
              }}
            >
              取消
            </button>
          </span>
        ) : (
          <button
            style={{
              marginLeft: "auto",
              background: "none",
              border: "none",
              cursor: "pointer",
              color: "var(--text-muted)",
              padding: "2px",
              display: "flex",
              alignItems: "center",
              opacity: 0.6,
            }}
            onClick={(e) => {
              e.stopPropagation();
              setConfirmDelete(true);
            }}
            title="删除任务"
          >
            <Trash2 size={12} />
          </button>
        )}
      </div>
    </div>
  );
}

// ===== 子组件：代码块（Typora/Codex 风格 — 语言标签 + 复制按钮）=====

const CodeBlockWrapper = memo(function CodeBlockWrapper({ children }: { children: ReactNode }) {
  const [copied, setCopied] = useState(false);

  // 从子级 code 元素中提取语言和代码文本
  const codeEl = Array.isArray(children) ? children[0] : children;
  const props = ((codeEl as React.ReactElement)?.props ?? {}) as { className?: string; children?: ReactNode };
  const className: string = props.className || "";
  const lang = className.replace(/hljs\s+language-|language-/g, "") || "text";
  const rawChildren = props.children;
  const codeText = typeof rawChildren === "string"
    ? rawChildren
    : Array.isArray(rawChildren)
      ? rawChildren.join("")
      : String(rawChildren ?? "");

  const handleCopy = () => {
    navigator.clipboard.writeText(codeText).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  return (
    <div className="md-codeblock">
      <div className="md-codeblock-header">
        <span className="md-codeblock-lang">{lang}</span>
        <button className="md-codeblock-copy" onClick={handleCopy}>
          {copied ? <Check size={11} /> : <Copy size={11} />}
          {copied ? "已复制" : "复制"}
        </button>
      </div>
      <pre className="md-pre">{children}</pre>
    </div>
  );
});

// ===== 子组件：消息渲染 =====

interface MessageRendererProps {
  message: ParsedMessage;
  onFlowClick: (flowId: string) => void;
}

const MessageRenderer = memo(function MessageRenderer({ message, onFlowClick }: MessageRendererProps) {
  // 工具调用卡片
  if (message.role === "tool_call" && message.tool) {
    return (
      <ToolCallCard
        tool={message.tool}
        timestamp={message.timestamp}
        onFlowClick={onFlowClick}
      />
    );
  }

  // 系统消息（居中淡色提示）
  if (message.role === "system") {
    return (
      <div className="agent-chat-msg agent-chat-msg-system">
        <div className="agent-chat-system-text">
          <Info size={12} />
          <span>{message.content}</span>
        </div>
      </div>
    );
  }

  // 思考过程（默认折叠，点击展开）— Codex 风格
  if (message.role === "thinking") {
    return <ThinkingMessage content={message.content} streaming={message.streaming} id={message.id} />;
  }

  // 用户消息（头像在右，气泡在左，尖角指向右侧头像）— QQ 风格
  if (message.role === "user") {
    return (
      <div className="agent-chat-msg agent-chat-msg-user">
        <div className="agent-chat-msg-avatar agent-chat-avatar-user">
          <UserIcon size={18} />
        </div>
        <div className="agent-chat-msg-bubble agent-chat-bubble-user">
          <div className="agent-chat-msg-text">{message.content}</div>
        </div>
      </div>
    );
  }

  // Agent 消息（左对齐，灰色气泡）- 使用 Markdown 渲染
  return (
    <div className="agent-chat-msg agent-chat-msg-agent">
      <div className="agent-chat-msg-avatar agent-chat-avatar-agent">
        <Bot size={18} />
      </div>
      <div className="agent-chat-msg-bubble agent-chat-bubble-agent">
        <div className="agent-chat-msg-markdown">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            rehypePlugins={[rehypeHighlight]}
            components={{
              pre: ({ children }) => <CodeBlockWrapper>{children}</CodeBlockWrapper>,
              code: ({ className, children, ...props }) => {
                const isInline = !className;
                return isInline ? (
                  <code className="md-code-inline" {...props}>{children}</code>
                ) : (
                  <code className={className} {...props}>{children}</code>
                );
              },
              table: ({ children }) => <table className="md-table">{children}</table>,
              th: ({ children }) => <th className="md-th">{children}</th>,
              td: ({ children }) => <td className="md-td">{children}</td>,
              // 检测包含 ASCII 框线字符的段落，使用等宽字体保持对齐
              p: ({ children, ...props }) => {
                const text = typeof children === "string" ? children : "";
                const hasBoxChars = /[│┌┐└┘├┤┬┴┼─━┃┏┓┗┛┣┫┳┻╋═║╔╗╚╝╠╣╦╩╬↑↓←→]/.test(text);
                return hasBoxChars ? (
                  <p className="md-ascii-art" {...props}>{children}</p>
                ) : (
                  <p {...props}>{children}</p>
                );
              },
            }}
          >
            {message.content}
          </ReactMarkdown>
        </div>
      </div>
    </div>
  );
});

// ===== 子组件：思考过程（流式 + 自动折叠）=====

interface ThinkingMessageProps {
  content: string;
  streaming?: boolean;
  id?: string;
}

const ThinkingMessage = memo(function ThinkingMessage({ content, streaming, id }: ThinkingMessageProps) {
  // 流式时默认展开；流式结束后自动折叠
  const [expanded, setExpanded] = useState(!!streaming);
  const [userToggled, setUserToggled] = useState(false);

  // streaming 状态变化时自动折叠（用户未手动操作过）
  useEffect(() => {
    if (!streaming && !userToggled) {
      setExpanded(false);
    }
    if (streaming && !userToggled) {
      setExpanded(true);
    }
  }, [streaming, userToggled]);

  const handleToggle = () => {
    setUserToggled(true);
    setExpanded((v) => !v);
  };

  // 单一 ChevronRight 图标，由 CSS 旋转控制方向，避免双重变换冲突
  return (
    <div
      className={`agent-chat-thinking ${streaming ? "" : "thinking-done"} ${expanded ? "expanded" : ""}`}
      data-message-id={id}
    >
      <div className="agent-chat-thinking-header" onClick={handleToggle}>
        <Brain size={11} className="agent-chat-thinking-icon" />
        <span className={`agent-chat-thinking-label ${streaming ? "thinking-label-active" : "thinking-label-done"}`}>
          {streaming ? "思考中…" : "思考过程"}
        </span>
        {streaming && <span className="thinking-cursor" />}
        <ChevronRight size={10} className="agent-chat-thinking-chevron" />
      </div>
      {/* CSS Grid 行高动画：content 为 overflow 容器，inner 为实际内容 */}
      <div className="agent-chat-thinking-content">
        <div className="agent-chat-thinking-content-inner">
          {streaming ? (
            /* 流式中用纯文本渲染：避免每 150ms 对累计的整段思考做 markdown 解析(O(n²))，长思考链不卡顿 */
            <div className="thinking-stream-text">{content}</div>
          ) : (
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              rehypePlugins={[rehypeHighlight]}
              components={{
                pre: ({ children }) => <CodeBlockWrapper>{children}</CodeBlockWrapper>,
                code: ({ className, children, ...props }) => {
                  const isInline = !className;
                  return isInline ? (
                    <code className="md-code-inline" {...props}>{children}</code>
                  ) : (
                    <code className={className} {...props}>{children}</code>
                  );
                },
                table: ({ children }) => <table className="md-table">{children}</table>,
                th: ({ children }) => <th className="md-th">{children}</th>,
                td: ({ children }) => <td className="md-td">{children}</td>,
                p: ({ children, ...props }) => {
                  const text = typeof children === "string" ? children : "";
                  const hasBoxChars = /[│┌┐└┘├┤┬┴┼─━┃┏┓┗┛┣┫┳┻╋═║╔╗╚╝╠╣╦╩╬↑↓←→]/.test(text);
                  return hasBoxChars ? (
                    <p className="md-ascii-art" {...props}>{children}</p>
                  ) : (
                    <p {...props}>{children}</p>
                  );
                },
              }}
            >
              {content}
            </ReactMarkdown>
          )}
        </div>
      </div>
    </div>
  );
});

// ===== 子组件：工具调用卡片（可折叠） =====

interface ToolCallCardProps {
  tool: ToolInfo;
  timestamp: string;
  onFlowClick: (flowId: string) => void;
}

const ToolCallCard = memo(function ToolCallCard({ tool, timestamp, onFlowClick }: ToolCallCardProps) {
  const [expanded, setExpanded] = useState(false);

  const argsText =
    tool.args != null ? JSON.stringify(tool.args, null, 2) : "";
  const isRunning = tool.result === null;
  const resultText =
    tool.result != null
      ? typeof tool.result === "string"
        ? tool.result
        : JSON.stringify(tool.result, null, 2)
      : "";

  const preview = isRunning ? "执行中…" : summarizeResult(tool.result);
  const hasDetail = Boolean(argsText || resultText);

  return (
    <div className="agent-chat-toolcard">
      <div
        className="agent-chat-toolcard-header"
        onClick={() => hasDetail && setExpanded((v) => !v)}
        style={{ cursor: hasDetail ? "pointer" : "default" }}
      >
        <Wrench size={12} className="agent-chat-toolcard-icon" />
        <span className="agent-chat-toolcard-name">{tool.tool}</span>
        {!expanded && preview && (
          <span className="agent-chat-toolcard-preview" style={isRunning ? { color: "var(--accent-yellow)" } : undefined}>
            {isRunning && <RefreshCw size={10} className="spin" style={{ display: "inline", marginRight: 4 }} />}
            {preview}
          </span>
        )}
        {hasDetail &&
          (expanded ? (
            <ChevronDown size={12} className="agent-chat-toolcard-chevron" />
          ) : (
            <ChevronRight size={12} className="agent-chat-toolcard-chevron" />
          ))}
      </div>

      {expanded && hasDetail && (
        <div className="agent-chat-toolcard-body">
          {argsText && (
            <div className="agent-chat-toolcard-section">
              <div className="agent-chat-toolcard-label">参数</div>
              <pre className="agent-chat-toolcard-pre">{argsText}</pre>
            </div>
          )}
          {resultText && (
            <div className="agent-chat-toolcard-section">
              <div className="agent-chat-toolcard-label">
                <CheckCircle2 size={11} />
                结果
              </div>
              <pre className="agent-chat-toolcard-pre">
                {renderResultWithLinks(resultText, onFlowClick)}
              </pre>
            </div>
          )}
        </div>
      )}

      <div className="agent-chat-toolcard-time">
        {timestamp ? new Date(timestamp).toLocaleTimeString("zh-CN") : ""}
      </div>
    </div>
  );
});

// ===== 子组件：待发送数据包预览（可折叠小方框） =====

interface PendingPacketPreviewProps {
  preview: string;
  onRemove: () => void;
}

function PendingPacketPreview({ preview, onRemove }: PendingPacketPreviewProps) {
  const [expanded, setExpanded] = useState(false);
  // 提取第一行作为摘要
  const firstLine = preview.split("\n")[0] || "数据包";
  const previewChars = preview.length;

  return (
    <div
      style={{
        marginBottom: 6,
        border: "1px solid var(--accent-purple)",
        borderRadius: "var(--radius-sm)",
        background: "var(--bg-tertiary)",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          padding: "4px 8px",
          cursor: "pointer",
          fontSize: 11,
          color: "var(--text-secondary)",
          userSelect: "none",
        }}
        onClick={() => setExpanded((v) => !v)}
      >
        {expanded ? (
          <ChevronDown size={11} style={{ color: "var(--accent-purple)" }} />
        ) : (
          <ChevronRight size={11} style={{ color: "var(--accent-purple)" }} />
        )}
        <Bot size={11} style={{ color: "var(--accent-purple)" }} />
        <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {firstLine}
        </span>
        <span style={{ color: "var(--text-muted)", fontSize: 10 }}>
          {previewChars} 字符
        </span>
        <button
          style={{
            background: "none",
            border: "none",
            cursor: "pointer",
            color: "var(--text-muted)",
            padding: "0 2px",
            display: "flex",
            alignItems: "center",
          }}
          onClick={(e) => {
            e.stopPropagation();
            onRemove();
          }}
          title="移除数据包"
        >
          <X size={11} />
        </button>
      </div>
      {expanded && (
        <pre
          style={{
            margin: 0,
            padding: "6px 8px",
            fontSize: 10,
            lineHeight: 1.5,
            color: "var(--text-secondary)",
            background: "var(--bg-primary)",
            borderTop: "1px solid var(--border-subtle)",
            maxHeight: 200,
            overflow: "auto",
            whiteSpace: "pre-wrap",
            wordBreak: "break-all",
          }}
        >
          {preview}
        </pre>
      )}
    </div>
  );
}
