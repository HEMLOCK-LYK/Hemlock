import { useState, useEffect, useCallback, useRef } from "react";
import type { ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import {
  RefreshCw,
  Send,
  Satellite,
  Wrench,
  ChevronDown,
  ChevronRight,
  CheckCircle2,
  Info,
  Trash2,
  Search,
  Key,
  FileText,
  Eye,
  Check,
  AlertCircle,
  ExternalLink,
  Save,
  X,
  Globe,
  Download,
  Loader2,
  Crosshair,
} from "lucide-react";
import { useWebSocket } from "../hooks/useWebSocket";
import { useProject } from "../context/ProjectContext";
import {
  listReconConversations,
  getReconConversation,
  createReconConversation,
  reconChat,
  deleteReconConversation,
  getEngines,
  getKeys,
  setKey,
  deleteKey,
  search as reconSearch,
  listReports,
  getReportContent,
  deleteReport,
  type ReconConversation,
  type ReconEngine,
  type ReconReport,
  type SearchResult,
} from "../api/recon";
import type { WSEvent } from "../types";
import { Spinner, EmptyState } from "../components/Common";
import { MarkdownView } from "../components/MarkdownView";

// ===== 常量 =====

const STATUS_LABELS: Record<string, string> = {
  idle: "待开始",
  running: "收集中",
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

type ViewMode = "collect" | "search" | "keys" | "reports";

// ===== 类型定义 =====

interface ToolCallStep {
  tool: string;
  args: unknown;
  result: unknown;
  timestamp: string;
}

interface ParsedTask {
  target: string;
  followups: { text: string; timestamp: string }[];
  toolCalls: ToolCallStep[];
  aiReplies: { text: string; timestamp: string }[];
  systemMessages: string[];
}

// ===== 辅助函数 =====

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

/** 将 steps 分类为目标、工具调用、AI回复等 */
function parseTaskSteps(
  steps: { type: string; data: unknown; timestamp: string }[]
): ParsedTask {
  const result: ParsedTask = {
    target: "",
    followups: [],
    toolCalls: [],
    aiReplies: [],
    systemMessages: [],
  };

  let firstUserMsg = true;
  for (const step of steps) {
    const d = parseStepData(step.data);
    switch (step.type) {
      case "user_message":
      case "user":
        if (firstUserMsg) {
          result.target = d.text || "";
          firstUserMsg = false;
        } else {
          result.followups.push({
            text: d.text || "",
            timestamp: step.timestamp,
          });
        }
        break;
      case "assistant_message":
      case "assistant":
      case "thinking":
      case "complete":
        if (d.text) {
          result.aiReplies.push({ text: d.text, timestamp: step.timestamp });
        }
        break;
      case "tool_call":
        result.toolCalls.push({
          tool: d.tool || "unknown",
          args: d.args,
          result: d.result,
          timestamp: step.timestamp,
        });
        break;
      case "system_message":
      case "system":
      case "error":
      case "limit":
        if (d.text) result.systemMessages.push(d.text);
        break;
    }
  }

  return result;
}

function summarizeResult(result: unknown): string {
  if (result == null) return "";
  let obj: Record<string, unknown> | null = null;
  if (typeof result === "string") {
    try {
      const parsed = JSON.parse(result);
      if (parsed && typeof parsed === "object")
        obj = parsed as Record<string, unknown>;
      else return result.length > 80 ? result.slice(0, 80) + "…" : result;
    } catch {
      return result.length > 80 ? result.slice(0, 80) + "…" : result;
    }
  } else if (typeof result === "object") {
    obj = result as Record<string, unknown>;
  }
  if (!obj) return "";

  if (obj.status !== undefined) {
    let s = `${obj.status}`;
    if (obj.total_engines !== undefined) s += ` | ${obj.total_engines} 引擎`;
    if (obj.total_subdomains !== undefined)
      s += ` | ${obj.total_subdomains} 子域名`;
    if (obj.total_records !== undefined) s += ` | ${obj.total_records} 记录`;
    if (obj.http_status !== undefined) s += ` | HTTP ${obj.http_status}`;
    if (obj.report_file) s += ` | 报告已生成`;
    return s;
  }
  if (obj.error) return `错误: ${String(obj.error).slice(0, 60)}`;
  if (obj.message) return String(obj.message).slice(0, 80);
  return "";
}

/** 从所有工具调用结果中汇总统计信息 */
interface ReconStats {
  subdomains: number;
  ips: number;
  records: number;
  engines: number;
  httpProbes: number;
  hasReport: boolean;
  reportFile: string | null;
}

function extractStats(toolCalls: ToolCallStep[]): ReconStats {
  const stats: ReconStats = {
    subdomains: 0,
    ips: 0,
    records: 0,
    engines: 0,
    httpProbes: 0,
    hasReport: false,
    reportFile: null,
  };

  for (const tc of toolCalls) {
    let obj: Record<string, unknown> | null = null;
    if (typeof tc.result === "string") {
      try {
        const parsed = JSON.parse(tc.result);
        if (parsed && typeof parsed === "object")
          obj = parsed as Record<string, unknown>;
      } catch {
        /* ignore */
      }
    } else if (tc.result && typeof tc.result === "object") {
      obj = tc.result as Record<string, unknown>;
    }
    if (!obj) continue;

    if (obj.total_subdomains !== undefined)
      stats.subdomains += Number(obj.total_subdomains) || 0;
    if (obj.total_records !== undefined)
      stats.records += Number(obj.total_records) || 0;
    if (obj.total_engines !== undefined)
      stats.engines += Number(obj.total_engines) || 0;
    if (obj.http_status !== undefined) stats.httpProbes++;
    if (obj.subdomains && Array.isArray(obj.subdomains))
      stats.subdomains += obj.subdomains.length;
    if (obj.a_records && Array.isArray(obj.a_records))
      stats.ips += obj.a_records.length;
    if (obj.report_file) {
      stats.hasReport = true;
      stats.reportFile = String(obj.report_file);
    }
  }

  return stats;
}

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

export function ReconView() {
  const { currentProject } = useProject();
  const navigate = useNavigate();

  const projectId = currentProject?.id ?? null;

  // ===== 收集任务状态 =====
  const [tasks, setTasks] = useState<ReconConversation[]>([]);
  const [loading, setLoading] = useState(false);
  const [collecting, setCollecting] = useState(false);
  const [targetInput, setTargetInput] = useState("");
  const [expandedTaskId, setExpandedTaskId] = useState<string | null>(null);
  const [taskDetails, setTaskDetails] = useState<Record<string, ReconConversation>>({});
  const [viewMode, setViewMode] = useState<ViewMode>("collect");
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [followupInputs, setFollowupInputs] = useState<Record<string, string>>({});
  const [sendingFollowup, setSendingFollowup] = useState<string | null>(null);

  const expandedTaskIdRef = useRef<string | null>(null);
  expandedTaskIdRef.current = expandedTaskId;

  // ===== 副功能状态 =====
  const [engines, setEngines] = useState<ReconEngine[]>([]);
  const [keys, setKeys] = useState<Record<string, string>>({});
  const [keysLoading, setKeysLoading] = useState(false);
  const [keyInputs, setKeyInputs] = useState<Record<string, string>>({});
  const [savingKey, setSavingKey] = useState<string | null>(null);

  const [query, setQuery] = useState("");
  const [selectedEngines, setSelectedEngines] = useState<string[]>([]);
  const [searchSize, setSearchSize] = useState(100);
  const [searching, setSearching] = useState(false);
  const [searchResult, setSearchResult] = useState<SearchResult | null>(null);

  const [reports, setReports] = useState<ReconReport[]>([]);
  const [viewingReport, setViewingReport] = useState<string | null>(null);
  const [reportContent, setReportContent] = useState("");
  const [loadingReport, setLoadingReport] = useState(false);
  const [deletingReport, setDeletingReport] = useState<string | null>(null);

  const [toast, setToast] = useState<{
    type: "success" | "error" | "warning";
    msg: string;
  } | null>(null);
  const showToast = useCallback(
    (type: "success" | "error" | "warning", msg: string) => {
      setToast({ type, msg });
      window.setTimeout(() => setToast(null), 3000);
    },
    []
  );

  // ===== 任务列表加载 =====
  const refreshTasks = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listReconConversations(projectId, 50);
      setTasks(data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  const refreshTaskDetail = useCallback(async (taskId: string) => {
    try {
      const data = await getReconConversation(taskId);
      setTaskDetails((prev) => ({ ...prev, [taskId]: data }));
    } catch (e) {
      console.error(e);
    }
  }, []);

  useEffect(() => {
    refreshTasks();
  }, [refreshTasks]);

  useEffect(() => {
    setExpandedTaskId(null);
    setTaskDetails({});
  }, [projectId]);

  // WebSocket 实时事件
  const handleWsEvent = useCallback(
    (event: WSEvent) => {
      if (!event.event) return;
      const taskId = event.task_id as string | undefined;
      if (
        taskId &&
        (event.event === "recon_chat_message" ||
          event.event === "recon_tool_call" ||
          event.event === "recon_tool_result" ||
          event.event === "chat_message" ||
          event.event === "tool_call" ||
          event.event === "tool_result" ||
          event.event === "task_complete" ||
          event.event === "task_error")
      ) {
        refreshTaskDetail(taskId);
        refreshTasks();
      }
    },
    [refreshTaskDetail, refreshTasks]
  );

  useWebSocket(handleWsEvent);

  // ===== 开始收集 =====
  const handleStartCollect = async () => {
    const target = targetInput.trim();
    if (!target || collecting) return;

    setCollecting(true);
    setTargetInput("");

    try {
      // 创建新任务
      const conv = await createReconConversation(projectId, target);
      await refreshTasks();

      // 自动展开
      setExpandedTaskId(conv.id);
      await refreshTaskDetail(conv.id);

      // 发送目标，AI 开始收集（长请求）
      await reconChat(conv.id, target, projectId);

      // 最终刷新
      await refreshTaskDetail(conv.id);
      await refreshTasks();
    } catch (e) {
      console.error(e);
      showToast(
        "error",
        `收集启动失败: ${e instanceof Error ? e.message : String(e)}`
      );
    } finally {
      setCollecting(false);
    }
  };

  // ===== 追问 =====
  const handleFollowup = async (taskId: string) => {
    const msg = (followupInputs[taskId] ?? "").trim();
    if (!msg || sendingFollowup) return;

    setSendingFollowup(taskId);
    setFollowupInputs((prev) => ({ ...prev, [taskId]: "" }));

    try {
      await reconChat(taskId, msg, projectId);
      await refreshTaskDetail(taskId);
      await refreshTasks();
    } catch (e) {
      console.error(e);
      showToast(
        "error",
        `发送失败: ${e instanceof Error ? e.message : String(e)}`
      );
    } finally {
      setSendingFollowup(null);
    }
  };

  // ===== 删除任务 =====
  const handleDeleteTask = useCallback(
    async (taskId: string) => {
      setDeleteError(null);
      try {
        await deleteReconConversation(taskId);
        if (expandedTaskIdRef.current === taskId) {
          setExpandedTaskId(null);
        }
        setTaskDetails((prev) => {
          const next = { ...prev };
          delete next[taskId];
          return next;
        });
        await refreshTasks();
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        setDeleteError(`删除失败: ${msg}`);
        setTimeout(() => setDeleteError(null), 3000);
      }
    },
    [refreshTasks]
  );

  // ===== 展开/折叠 =====
  const handleToggleExpand = (taskId: string) => {
    setExpandedTaskId((prev) => {
      const next = prev === taskId ? null : taskId;
      if (next && !taskDetails[next]) {
        refreshTaskDetail(next);
      }
      return next;
    });
  };

  const handleFlowClick = (flowId: string) => {
    navigate(`/flows/${flowId}`);
  };

  // ===== 副功能数据加载 =====
  const loadEnginesAndKeys = useCallback(async () => {
    setKeysLoading(true);
    try {
      const [engineList, keyMap] = await Promise.all([getEngines(), getKeys()]);
      setEngines(engineList);
      setKeys(keyMap);
      if (selectedEngines.length === 0) {
        const freeEngines = engineList
          .filter((e) => !e.key_required)
          .map((e) => e.id);
        setSelectedEngines(freeEngines);
      }
    } catch (err) {
      showToast("error", err instanceof Error ? err.message : "加载数据失败");
    } finally {
      setKeysLoading(false);
    }
  }, [showToast, selectedEngines.length]);

  const loadReports = useCallback(async () => {
    try {
      const list = await listReports();
      setReports(list);
    } catch (err) {
      showToast("error", err instanceof Error ? err.message : "加载报告失败");
    }
  }, [showToast]);

  useEffect(() => {
    if (
      (viewMode === "search" || viewMode === "keys") &&
      engines.length === 0
    ) {
      loadEnginesAndKeys();
    }
    if (viewMode === "reports") {
      loadReports();
    }
  }, [viewMode, engines.length, loadEnginesAndKeys, loadReports]);

  // ===== 手动搜索 =====
  const handleSearch = useCallback(async () => {
    if (!query.trim()) {
      showToast("error", "请输入搜索内容");
      return;
    }
    if (selectedEngines.length === 0) {
      showToast("error", "请至少选择一个引擎");
      return;
    }
    setSearching(true);
    setSearchResult(null);
    try {
      const result = await reconSearch(
        query.trim(),
        selectedEngines,
        searchSize
      );
      setSearchResult(result);
      if (result.errors && result.errors.length > 0) {
        showToast("warning", `搜索完成，${result.errors.length} 个引擎失败`);
      } else {
        showToast(
          "success",
          `搜索完成，${result.total_engines} 个引擎返回结果`
        );
      }
    } catch (err) {
      showToast("error", err instanceof Error ? err.message : "搜索失败");
    } finally {
      setSearching(false);
    }
  }, [query, selectedEngines, searchSize, showToast]);

  // ===== Key 管理 =====
  const handleSaveKey = useCallback(
    async (engineId: string) => {
      const keyVal = keyInputs[engineId] ?? "";
      if (!keyVal.trim()) {
        showToast("error", "请输入 API Key");
        return;
      }
      setSavingKey(engineId);
      try {
        await setKey(engineId, keyVal.trim());
        const [engineList, keyMap] = await Promise.all([
          getEngines(),
          getKeys(),
        ]);
        setEngines(engineList);
        setKeys(keyMap);
        setKeyInputs((prev) => ({ ...prev, [engineId]: "" }));
        showToast("success", "API Key 已保存");
      } catch (err) {
        showToast("error", err instanceof Error ? err.message : "保存失败");
      } finally {
        setSavingKey(null);
      }
    },
    [keyInputs, showToast]
  );

  const handleDeleteKey = useCallback(
    async (engineId: string) => {
      const engineName =
        engines.find((e) => e.id === engineId)?.name ?? engineId;
      if (!window.confirm(`确认删除 ${engineName} 的 API Key？`)) return;
      try {
        await deleteKey(engineId);
        const [engineList, keyMap] = await Promise.all([
          getEngines(),
          getKeys(),
        ]);
        setEngines(engineList);
        setKeys(keyMap);
        showToast("success", `已删除 ${engineName} 的 API Key`);
      } catch (err) {
        showToast("error", err instanceof Error ? err.message : "删除失败");
      }
    },
    [engines, showToast]
  );

  // ===== 报告管理 =====
  const handleViewReport = useCallback(
    async (filename: string) => {
      setLoadingReport(true);
      setViewingReport(filename);
      setReportContent("");
      try {
        const content = await getReportContent(filename);
        setReportContent(content);
      } catch (err) {
        showToast("error", err instanceof Error ? err.message : "加载报告失败");
      } finally {
        setLoadingReport(false);
      }
    },
    [showToast]
  );

  const handleDeleteReport = useCallback(
    async (filename: string) => {
      if (!window.confirm(`确认删除报告 ${filename}？`)) return;
      setDeletingReport(filename);
      try {
        await deleteReport(filename);
        setReports((prev) => prev.filter((r) => r.filename !== filename));
        if (viewingReport === filename) {
          setViewingReport(null);
          setReportContent("");
        }
        showToast("success", "报告已删除");
      } catch (err) {
        showToast("error", err instanceof Error ? err.message : "删除失败");
      } finally {
        setDeletingReport(null);
      }
    },
    [viewingReport, showToast]
  );

  const handleDownloadReport = useCallback(
    (filename: string, content: string) => {
      const blob = new Blob([content], { type: "text/markdown" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      showToast("success", "报告已下载");
    },
    [showToast]
  );

  const toggleEngine = useCallback((engineId: string) => {
    setSelectedEngines((prev) =>
      prev.includes(engineId)
        ? prev.filter((e) => e !== engineId)
        : [...prev, engineId]
    );
  }, []);

  // ===== 渲染 =====

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* 顶部标签栏 */}
      <div className="view-header">
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Satellite size={15} style={{ color: "var(--accent-cyan)" }} />
          <span className="view-title">AI 资产收集</span>
        </div>
        <div style={{ display: "flex", gap: 4 }}>
          <button
            className={`btn btn-sm ${viewMode === "search" ? "btn-primary" : ""}`}
            onClick={() => setViewMode("search")}
          >
            <Search size={12} />
            手动搜索
          </button>
          <button
            className={`btn btn-sm ${viewMode === "keys" ? "btn-primary" : ""}`}
            onClick={() => setViewMode("keys")}
          >
            <Key size={12} />
            Key
          </button>
          <button
            className={`btn btn-sm ${viewMode === "reports" ? "btn-primary" : ""}`}
            onClick={() => setViewMode("reports")}
          >
            <FileText size={12} />
            报告
          </button>
          <button
            className="btn btn-sm"
            onClick={refreshTasks}
            disabled={loading}
          >
            {loading ? <Spinner size={12} /> : <RefreshCw size={12} />}
            刷新
          </button>
        </div>
      </div>

      {/* ===== AI 收集模式 ===== */}
      {viewMode === "collect" && (
        <div
          style={{
            flex: 1,
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
          }}
        >
          {/* 目标输入区 */}
          <div
            style={{
              padding: "16px",
              borderBottom: "1px solid var(--border-default)",
              background: "var(--bg-secondary)",
            }}
          >
            <div
              style={{
                display: "flex",
                gap: 8,
                alignItems: "stretch",
              }}
            >
              <textarea
                className="input"
                style={{
                  flex: 1,
                  minHeight: 90,
                  maxHeight: 160,
                  resize: "vertical",
                  fontSize: 13,
                  lineHeight: 1.5,
                }}
                placeholder="输入收集目标，如公司名称、域名、IP 等"
                value={targetInput}
                onChange={(e) => setTargetInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                    e.preventDefault();
                    handleStartCollect();
                  }
                }}
                rows={3}
                disabled={collecting}
              />
              <button
                className="btn btn-primary"
                onClick={handleStartCollect}
                disabled={!targetInput.trim() || collecting}
                style={{
                  minWidth: 100,
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                }}
              >
                {collecting ? (
                  <Loader2 size={14} className="animate-spin" />
                ) : (
                  <Crosshair size={14} />
                )}
                {collecting ? "收集中..." : "开始收集"}
              </button>
            </div>
            {deleteError && (
              <div
                style={{
                  marginTop: 8,
                  padding: "6px 10px",
                  fontSize: 11,
                  color: "var(--accent-red)",
                  background: "rgba(248,81,73,0.1)",
                  borderRadius: 4,
                }}
              >
                {deleteError}
              </div>
            )}
          </div>

          {/* 任务列表 */}
          <div style={{ flex: 1, overflow: "auto", padding: "12px 16px" }}>
            {tasks.length === 0 ? (
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  justifyContent: "center",
                  height: "100%",
                  gap: 12,
                  color: "var(--text-muted)",
                }}
              >
                <Crosshair size={48} style={{ opacity: 0.4 }} />
                <span style={{ fontSize: 14 }}>
                  在上方输入目标，AI 将自动规划并执行全面信息收集
                </span>
                <span style={{ fontSize: 11, opacity: 0.7 }}>
                  支持域名、IP、公司名、SRC 名称等多种目标
                </span>
              </div>
            ) : (
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 8,
                }}
              >
                {tasks.map((task) => (
                  <TaskCard
                    key={task.id}
                    task={task}
                    detail={taskDetails[task.id]}
                    expanded={expandedTaskId === task.id}
                    onToggle={() => handleToggleExpand(task.id)}
                    onDelete={() => handleDeleteTask(task.id)}
                    onFlowClick={handleFlowClick}
                    followupInput={followupInputs[task.id] ?? ""}
                    onFollowupChange={(v) =>
                      setFollowupInputs((prev) => ({
                        ...prev,
                        [task.id]: v,
                      }))
                    }
                    onFollowupSend={() => handleFollowup(task.id)}
                    sendingFollowup={sendingFollowup === task.id}
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* ===== 手动搜索模式 ===== */}
      {viewMode === "search" && (
        <SearchPanel
          engines={engines}
          keys={keys}
          loading={keysLoading}
          query={query}
          setQuery={setQuery}
          selectedEngines={selectedEngines}
          toggleEngine={toggleEngine}
          searchSize={searchSize}
          setSearchSize={setSearchSize}
          searching={searching}
          searchResult={searchResult}
          onSearch={handleSearch}
          onSelectAll={() => setSelectedEngines(engines.map((e) => e.id))}
          onSelectFree={() =>
            setSelectedEngines(
              engines.filter((e) => !e.key_required).map((e) => e.id)
            )
          }
        />
      )}

      {/* ===== Key 管理模式 ===== */}
      {viewMode === "keys" && (
        <KeysPanel
          engines={engines}
          keys={keys}
          keyInputs={keyInputs}
          setKeyInputs={setKeyInputs}
          savingKey={savingKey}
          onSave={handleSaveKey}
          onDelete={handleDeleteKey}
          loading={keysLoading}
        />
      )}

      {/* ===== 报告模式 ===== */}
      {viewMode === "reports" && (
        <ReportsPanel
          reports={reports}
          viewingReport={viewingReport}
          reportContent={reportContent}
          loadingReport={loadingReport}
          deletingReport={deletingReport}
          onView={handleViewReport}
          onDelete={handleDeleteReport}
          onDownload={handleDownloadReport}
          onCloseView={() => setViewingReport(null)}
          onRefresh={loadReports}
        />
      )}

      {/* ===== Toast ===== */}
      {toast && (
        <div
          style={{
            position: "fixed",
            top: 16,
            right: 16,
            zIndex: 3000,
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "10px 16px",
            borderRadius: 6,
            fontSize: 13,
            fontWeight: 500,
            boxShadow: "0 4px 12px rgba(0,0,0,0.3)",
            background:
              toast.type === "success"
                ? "var(--accent-green)"
                : toast.type === "warning"
                ? "var(--accent-yellow)"
                : "var(--accent-red)",
            color: toast.type === "warning" ? "#000" : "#fff",
          }}
        >
          {toast.type === "success" ? (
            <Check size={16} />
          ) : (
            <AlertCircle size={16} />
          )}
          {toast.msg}
        </div>
      )}
    </div>
  );
}

// ===== 子组件：任务卡片 =====

interface TaskCardProps {
  task: ReconConversation;
  detail?: ReconConversation;
  expanded: boolean;
  onToggle: () => void;
  onDelete: () => void;
  onFlowClick: (flowId: string) => void;
  followupInput: string;
  onFollowupChange: (v: string) => void;
  onFollowupSend: () => void;
  sendingFollowup: boolean;
}

function TaskCard({
  task,
  detail,
  expanded,
  onToggle,
  onDelete,
  onFlowClick,
  followupInput,
  onFollowupChange,
  onFollowupSend,
  sendingFollowup,
}: TaskCardProps) {
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [showFollowup, setShowFollowup] = useState(false);

  const source = detail ?? task;
  const parsed = parseTaskSteps(source.steps || []);
  const isRunning = source.status === "running";
  const stats = extractStats(parsed.toolCalls);
  const hasStats =
    stats.subdomains > 0 ||
    stats.records > 0 ||
    stats.engines > 0 ||
    stats.httpProbes > 0;

  return (
    <div
      style={{
        border: "1px solid var(--border-default)",
        borderRadius: 8,
        background: "var(--bg-secondary)",
        overflow: "hidden",
      }}
    >
      {/* 卡片头部 */}
      <div
        onClick={onToggle}
        style={{
          padding: "12px 16px",
          display: "flex",
          alignItems: "center",
          gap: 10,
          cursor: "pointer",
          borderBottom: expanded ? "1px solid var(--border-default)" : "none",
        }}
      >
        {/* 状态图标 — 青色系区别于 Agent 的绿色 */}
        {isRunning ? (
          <Loader2
            size={16}
            className="animate-spin"
            style={{ color: "var(--accent-cyan)", flexShrink: 0 }}
          />
        ) : (
          <CheckCircle2
            size={16}
            style={{
              color:
                source.status === "failed"
                  ? "var(--accent-red)"
                  : source.status === "done"
                  ? "var(--accent-green)"
                  : "var(--accent-cyan)",
              flexShrink: 0,
            }}
          />
        )}

        {/* 目标 */}
        <span
          style={{
            fontSize: 13,
            fontWeight: 600,
            color: "var(--text-primary)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            flex: 1,
          }}
        >
          {parsed.target || task.goal?.replace(/^\[RECON\]/, "") || "新任务"}
        </span>

        {/* 折叠时显示迷你统计 */}
        {!expanded && hasStats && (
          <span
            style={{
              fontSize: 10,
              color: "var(--accent-cyan)",
              flexShrink: 0,
              display: "flex",
              gap: 6,
            }}
          >
            {stats.subdomains > 0 && <span>{stats.subdomains} 域名</span>}
            {stats.records > 0 && <span>{stats.records} 记录</span>}
            {stats.httpProbes > 0 && <span>{stats.httpProbes} 探测</span>}
          </span>
        )}

        {/* 状态徽章 */}
        <span
          style={{
            fontSize: 10,
            padding: "2px 8px",
            borderRadius: 10,
            flexShrink: 0,
            background: `${STATUS_COLORS[source.status] || "var(--text-muted)"}20`,
            color: STATUS_COLORS[source.status] || "var(--text-muted)",
            border: `1px solid ${STATUS_COLORS[source.status] || "var(--text-muted)"}40`,
          }}
        >
          {STATUS_LABELS[source.status] || source.status}
        </span>

        {/* 步骤数 */}
        {parsed.toolCalls.length > 0 && (
          <span
            style={{
              fontSize: 10,
              color: "var(--text-muted)",
              flexShrink: 0,
            }}
          >
            {parsed.toolCalls.length} 步
          </span>
        )}

        {/* 时间 */}
        <span
          style={{
            fontSize: 10,
            color: "var(--text-muted)",
            flexShrink: 0,
          }}
        >
          {source.started_at
            ? new Date(source.started_at).toLocaleString("zh-CN", {
                month: "2-digit",
                day: "2-digit",
                hour: "2-digit",
                minute: "2-digit",
              })
            : ""}
        </span>

        {/* 展开/折叠 */}
        {expanded ? (
          <ChevronDown size={14} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
        ) : (
          <ChevronRight size={14} style={{ color: "var(--text-muted)", flexShrink: 0 }} />
        )}

        {/* 删除 */}
        {confirmDelete ? (
          <span
            style={{ display: "flex", gap: 4, flexShrink: 0 }}
            onClick={(e) => e.stopPropagation()}
          >
            <button
              style={{
                background: "var(--accent-red)",
                color: "#fff",
                border: "none",
                borderRadius: 3,
                padding: "2px 8px",
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
                padding: "2px 8px",
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
              background: "none",
              border: "none",
              cursor: "pointer",
              color: "var(--text-muted)",
              padding: 2,
              display: "flex",
              alignItems: "center",
              opacity: 0.6,
              flexShrink: 0,
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

      {/* ===== 展开内容 — 仪表板风格 ===== */}
      {expanded && (
        <div style={{ padding: "14px 16px" }}>
          {/* 统计仪表板 */}
          {hasStats && (
            <div
              style={{
                display: "flex",
                gap: 8,
                marginBottom: 14,
                flexWrap: "wrap",
              }}
            >
              {stats.subdomains > 0 && (
                <StatBadge label="子域名" value={stats.subdomains} color="var(--accent-cyan)" />
              )}
              {stats.records > 0 && (
                <StatBadge label="DNS记录" value={stats.records} color="var(--accent-blue)" />
              )}
              {stats.engines > 0 && (
                <StatBadge label="引擎调用" value={stats.engines} color="var(--accent-purple)" />
              )}
              {stats.httpProbes > 0 && (
                <StatBadge label="HTTP探测" value={stats.httpProbes} color="var(--accent-green)" />
              )}
              {stats.hasReport && (
                <StatBadge label="报告" value="已生成" color="var(--accent-green)" isText />
              )}
            </div>
          )}

          {/* 系统消息 */}
          {parsed.systemMessages.length > 0 && (
            <div
              style={{
                padding: "6px 10px",
                marginBottom: 12,
                fontSize: 11,
                color: "var(--text-muted)",
                background: "var(--bg-primary)",
                borderRadius: 4,
                border: "1px solid var(--border-subtle)",
                display: "flex",
                alignItems: "center",
                gap: 6,
              }}
            >
              <Info size={12} />
              {parsed.systemMessages[parsed.systemMessages.length - 1]}
            </div>
          )}

          {/* 收集步骤 — 紧凑列表式（非时间线） */}
          {parsed.toolCalls.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <div
                style={{
                  fontSize: 11,
                  fontWeight: 600,
                  color: "var(--text-muted)",
                  marginBottom: 6,
                }}
              >
                收集步骤 ({parsed.toolCalls.length})
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                {parsed.toolCalls.map((tc, i) => (
                  <CompactToolRow
                    key={i}
                    tc={tc}
                    isRunning={isRunning && i === parsed.toolCalls.length - 1}
                    onFlowClick={onFlowClick}
                  />
                ))}
              </div>
            </div>
          )}

          {/* 收集中提示 */}
          {isRunning && parsed.toolCalls.length === 0 && (
            <div
              style={{
                padding: "16px",
                textAlign: "center",
                color: "var(--text-muted)",
                fontSize: 12,
              }}
            >
              <Loader2
                size={20}
                className="animate-spin"
                style={{ marginBottom: 8, opacity: 0.6, color: "var(--accent-cyan)" }}
              />
              <div>AI 正在分析目标并规划收集策略...</div>
            </div>
          )}

          {/* 收集摘要 — 报告式展示（非聊天气泡） */}
          {parsed.aiReplies.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <div
                style={{
                  fontSize: 11,
                  fontWeight: 600,
                  color: "var(--text-muted)",
                  marginBottom: 6,
                }}
              >
                收集摘要
              </div>
              <div
                style={{
                  padding: "12px 14px",
                  background: "var(--bg-primary)",
                  borderRadius: 6,
                  border: "1px solid var(--border-default)",
                  borderLeft: "3px solid var(--accent-cyan)",
                }}
              >
                {parsed.aiReplies.map((reply, i) => (
                  <div
                    key={i}
                    style={{
                      marginBottom: i < parsed.aiReplies.length - 1 ? 10 : 0,
                      paddingBottom: i < parsed.aiReplies.length - 1 ? 10 : 0,
                      borderBottom:
                        i < parsed.aiReplies.length - 1
                          ? "1px solid var(--border-subtle)"
                          : "none",
                    }}
                  >
                    <MarkdownView content={reply.text} />
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 操作按钮区 — 非对话式 */}
          <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
            {!showFollowup ? (
              <button
                className="btn btn-sm"
                onClick={() => setShowFollowup(true)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 4,
                  fontSize: 11,
                }}
              >
                <Crosshair size={11} />
                补充收集
              </button>
            ) : (
              <div
                style={{
                  flex: 1,
                  display: "flex",
                  gap: 6,
                  alignItems: "center",
                }}
              >
                <input
                  className="input"
                  style={{ flex: 1, fontSize: 12 }}
                  placeholder="输入补充目标或指令..."
                  value={followupInput}
                  onChange={(e) => onFollowupChange(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !sendingFollowup) {
                      onFollowupSend();
                    }
                  }}
                  disabled={sendingFollowup}
                  autoFocus
                />
                <button
                  className="btn btn-sm btn-primary"
                  onClick={onFollowupSend}
                  disabled={!followupInput.trim() || sendingFollowup}
                >
                  {sendingFollowup ? (
                    <Loader2 size={12} className="animate-spin" />
                  ) : (
                    <Send size={12} />
                  )}
                  发送
                </button>
                <button
                  className="btn btn-sm"
                  onClick={() => {
                    setShowFollowup(false);
                    onFollowupChange("");
                  }}
                  disabled={sendingFollowup}
                >
                  <X size={12} />
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ===== 子组件：统计徽章 =====

interface StatBadgeProps {
  label: string;
  value: number | string;
  color: string;
  isText?: boolean;
}

function StatBadge({ label, value, color, isText }: StatBadgeProps) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 4,
        padding: "4px 10px",
        borderRadius: 6,
        background: `${color}15`,
        border: `1px solid ${color}40`,
      }}
    >
      <span style={{ fontSize: 10, color: "var(--text-muted)" }}>{label}</span>
      <span
        style={{
          fontSize: 13,
          fontWeight: 700,
          color,
          fontFamily: isText ? "inherit" : "var(--font-mono)",
        }}
      >
        {value}
      </span>
    </div>
  );
}

// ===== 子组件：紧凑工具行（替代时间线式 ToolStep） =====

interface CompactToolRowProps {
  tc: ToolCallStep;
  isRunning: boolean;
  onFlowClick: (flowId: string) => void;
}

function CompactToolRow({ tc, isRunning, onFlowClick }: CompactToolRowProps) {
  const [expanded, setExpanded] = useState(false);

  const argsText = tc.args != null ? JSON.stringify(tc.args, null, 2) : "";
  const resultText =
    tc.result != null
      ? typeof tc.result === "string"
        ? tc.result
        : JSON.stringify(tc.result, null, 2)
      : "";

  const summary = summarizeResult(tc.result);
  const hasDetail = Boolean(argsText || resultText);

  return (
    <div
      style={{
        borderRadius: 4,
        background: "var(--bg-primary)",
        border: "1px solid var(--border-subtle)",
        overflow: "hidden",
      }}
    >
      <div
        onClick={() => hasDetail && setExpanded((v) => !v)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          padding: "5px 10px",
          cursor: hasDetail ? "pointer" : "default",
        }}
      >
        {isRunning ? (
          <Loader2 size={11} className="animate-spin" style={{ color: "var(--accent-cyan)", flexShrink: 0 }} />
        ) : (
          <CheckCircle2 size={11} style={{ color: "var(--accent-green)", flexShrink: 0 }} />
        )}
        <Wrench size={10} style={{ color: "var(--accent-cyan)", flexShrink: 0 }} />
        <span style={{ fontSize: 11, fontWeight: 600, color: "var(--text-primary)" }}>
          {tc.tool}
        </span>
        {summary && !expanded && (
          <span style={{ fontSize: 10, color: "var(--text-muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            — {summary}
          </span>
        )}
        {hasDetail && (
          expanded ? (
            <ChevronDown size={10} style={{ color: "var(--text-muted)", marginLeft: "auto", flexShrink: 0 }} />
          ) : (
            <ChevronRight size={10} style={{ color: "var(--text-muted)", marginLeft: "auto", flexShrink: 0 }} />
          )
        )}
      </div>

      {expanded && hasDetail && (
        <div style={{ padding: "0 10px 8px", display: "flex", flexDirection: "column", gap: 4 }}>
          {argsText && (
            <div>
              <div style={{ fontSize: 9, color: "var(--text-muted)", marginBottom: 2 }}>参数</div>
              <pre
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 10,
                  lineHeight: 1.4,
                  color: "var(--text-secondary)",
                  background: "var(--bg-secondary)",
                  padding: "4px 6px",
                  borderRadius: 3,
                  border: "1px solid var(--border-subtle)",
                  overflow: "auto",
                  maxHeight: 150,
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                  margin: 0,
                }}
              >
                {argsText}
              </pre>
            </div>
          )}
          {resultText && (
            <div>
              <div style={{ fontSize: 9, color: "var(--text-muted)", marginBottom: 2, display: "flex", alignItems: "center", gap: 3 }}>
                <CheckCircle2 size={9} />
                结果
              </div>
              <pre
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 10,
                  lineHeight: 1.4,
                  color: "var(--text-secondary)",
                  background: "var(--bg-secondary)",
                  padding: "4px 6px",
                  borderRadius: 3,
                  border: "1px solid var(--border-subtle)",
                  overflow: "auto",
                  maxHeight: 250,
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                  margin: 0,
                }}
              >
                {renderResultWithLinks(resultText, onFlowClick)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ===== 子组件：手动搜索面板 =====

interface SearchPanelProps {
  engines: ReconEngine[];
  keys: Record<string, string>;
  loading: boolean;
  query: string;
  setQuery: (v: string) => void;
  selectedEngines: string[];
  toggleEngine: (id: string) => void;
  searchSize: number;
  setSearchSize: (v: number) => void;
  searching: boolean;
  searchResult: SearchResult | null;
  onSearch: () => void;
  onSelectAll: () => void;
  onSelectFree: () => void;
}

function SearchPanel({
  engines,
  keys,
  loading,
  query,
  setQuery,
  selectedEngines,
  toggleEngine,
  searchSize,
  setSearchSize,
  searching,
  searchResult,
  onSearch,
  onSelectAll,
  onSelectFree,
}: SearchPanelProps) {
  if (loading) {
    return (
      <div className="empty-state" style={{ flex: 1 }}>
        <Spinner size={28} />
        <span>正在加载引擎列表...</span>
      </div>
    );
  }

  return (
    <div
      style={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
      }}
    >
      <div
        style={{
          padding: "12px 16px",
          borderBottom: "1px solid var(--border-default)",
          background: "var(--bg-secondary)",
          display: "flex",
          flexDirection: "column",
          gap: 10,
        }}
      >
        <div style={{ display: "flex", gap: 8 }}>
          <input
            className="input"
            style={{ flex: 1 }}
            placeholder='输入搜索内容，如域名或 domain="xxx"'
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !searching) onSearch();
            }}
          />
          <input
            className="input"
            style={{ width: 80, textAlign: "center" }}
            type="number"
            min={1}
            max={500}
            value={searchSize}
            onChange={(e) => setSearchSize(Number(e.target.value) || 100)}
            title="每个引擎返回的最大结果数"
          />
          <button
            className="btn btn-primary"
            onClick={onSearch}
            disabled={searching}
          >
            {searching ? <Spinner size={12} /> : <Search size={12} />}
            {searching ? "搜索中..." : "搜索"}
          </button>
        </div>

        <div
          style={{
            display: "flex",
            alignItems: "center",
            flexWrap: "wrap",
            gap: 6,
          }}
        >
          <span
            className="text-xs text-muted"
            style={{ marginRight: 4 }}
          >
            引擎:
          </span>
          {engines.map((engine) => {
            const isSelected = selectedEngines.includes(engine.id);
            const hasKey = !!keys[engine.id];
            const needsKey = engine.key_required && !hasKey;
            return (
              <button
                key={engine.id}
                onClick={() => toggleEngine(engine.id)}
                title={
                  needsKey
                    ? `${engine.name} - 未配置 API Key`
                    : engine.description
                }
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 4,
                  padding: "3px 10px",
                  borderRadius: 4,
                  fontSize: 11,
                  cursor: "pointer",
                  border: isSelected
                    ? "1px solid var(--accent-blue)"
                    : "1px solid var(--border-default)",
                  background: isSelected
                    ? "rgba(78,161,230,0.15)"
                    : "transparent",
                  color: isSelected
                    ? "var(--text-primary)"
                    : needsKey
                    ? "var(--text-muted)"
                    : "var(--text-secondary)",
                  opacity: needsKey && !isSelected ? 0.6 : 1,
                }}
              >
                {engine.name}
                {engine.key_required && (
                  <span
                    style={{
                      fontSize: 9,
                      color: hasKey
                        ? "var(--accent-green)"
                        : "var(--accent-yellow)",
                    }}
                  >
                    {hasKey ? "✓" : "⚠"}
                  </span>
                )}
              </button>
            );
          })}
          <button
            className="btn btn-sm"
            style={{ marginLeft: "auto", fontSize: 11 }}
            onClick={onSelectAll}
          >
            全选
          </button>
          <button
            className="btn btn-sm"
            style={{ fontSize: 11 }}
            onClick={onSelectFree}
          >
            仅免费
          </button>
        </div>
      </div>

      <div style={{ flex: 1, overflow: "auto", padding: "12px 16px" }}>
        {searching ? (
          <div className="empty-state">
            <Spinner size={28} />
            <span>正在搜索，多引擎并行查询中...</span>
          </div>
        ) : searchResult ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <div
              style={{
                display: "flex",
                gap: 12,
                padding: "8px 12px",
                borderRadius: 6,
                background: "var(--bg-secondary)",
                border: "1px solid var(--border-default)",
                fontSize: 12,
              }}
            >
              <span>
                <Check size={12} style={{ color: "var(--accent-green)" }} />
                成功引擎: {searchResult.total_engines}
              </span>
              {searchResult.errors.length > 0 && (
                <span>
                  <AlertCircle
                    size={12}
                    style={{ color: "var(--accent-yellow)" }}
                  />
                  失败: {searchResult.errors.length}
                </span>
              )}
              {searchResult.report_file && (
                <span
                  style={{
                    marginLeft: "auto",
                    color: "var(--text-muted)",
                  }}
                >
                  报告: {searchResult.report_file}
                </span>
              )}
            </div>
            {searchResult.errors.length > 0 && (
              <div
                style={{
                  padding: "8px 12px",
                  borderRadius: 6,
                  background: "rgba(240,99,108,0.08)",
                  border: "1px solid var(--accent-red)",
                  fontSize: 12,
                  color: "var(--accent-red)",
                }}
              >
                {searchResult.errors.map((err, i) => (
                  <div key={i}>• {err}</div>
                ))}
              </div>
            )}
            {searchResult.report_preview && (
              <div
                style={{
                  padding: "12px 16px",
                  borderRadius: 6,
                  background: "var(--bg-primary)",
                  border: "1px solid var(--border-default)",
                  overflow: "auto",
                  maxHeight: "60vh",
                }}
              >
                <MarkdownView content={searchResult.report_preview} />
              </div>
            )}
          </div>
        ) : (
          <div className="empty-state">
            <Globe size={36} />
            <span>输入目标域名或搜索语句，选择引擎后开始信息收集</span>
          </div>
        )}
      </div>
    </div>
  );
}

// ===== 子组件：Key 管理面板 =====

interface KeysPanelProps {
  engines: ReconEngine[];
  keys: Record<string, string>;
  keyInputs: Record<string, string>;
  setKeyInputs: React.Dispatch<React.SetStateAction<Record<string, string>>>;
  savingKey: string | null;
  onSave: (engineId: string) => void;
  onDelete: (engineId: string) => void;
  loading: boolean;
}

function KeysPanel({
  engines,
  keys,
  keyInputs,
  setKeyInputs,
  savingKey,
  onSave,
  onDelete,
  loading,
}: KeysPanelProps) {
  if (loading) {
    return (
      <div className="empty-state" style={{ flex: 1 }}>
        <Spinner size={28} />
        <span>正在加载...</span>
      </div>
    );
  }

  return (
    <div style={{ flex: 1, overflow: "auto", padding: "12px 16px" }}>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fill, minmax(340px, 1fr))",
          gap: 12,
        }}
      >
        {engines.map((engine) => {
          const hasKey = !!keys[engine.id];
          const isSaving = savingKey === engine.id;
          return (
            <div
              key={engine.id}
              style={{
                padding: 14,
                borderRadius: 8,
                background: "var(--bg-secondary)",
                border: "1px solid var(--border-default)",
                display: "flex",
                flexDirection: "column",
                gap: 8,
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span
                  style={{
                    fontSize: 13,
                    fontWeight: 600,
                    color: "var(--text-primary)",
                  }}
                >
                  {engine.name}
                </span>
                {engine.key_required ? (
                  <span
                    style={{
                      fontSize: 10,
                      padding: "1px 6px",
                      borderRadius: 3,
                      background: hasKey
                        ? "rgba(106,171,115,0.15)"
                        : "rgba(210,153,34,0.15)",
                      color: hasKey
                        ? "var(--accent-green)"
                        : "var(--accent-yellow)",
                    }}
                  >
                    {hasKey ? "已配置" : "需要 Key"}
                  </span>
                ) : (
                  <span
                    style={{
                      fontSize: 10,
                      padding: "1px 6px",
                      borderRadius: 3,
                      background: "rgba(23,162,184,0.15)",
                      color: "var(--accent-cyan)",
                    }}
                  >
                    免费
                  </span>
                )}
                {engine.doc_url && (
                  <a
                    href={engine.doc_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{
                      marginLeft: "auto",
                      color: "var(--text-muted)",
                      display: "flex",
                      alignItems: "center",
                    }}
                    title="查看文档"
                  >
                    <ExternalLink size={12} />
                  </a>
                )}
              </div>
              <div
                style={{
                  fontSize: 11,
                  color: "var(--text-muted)",
                  lineHeight: 1.4,
                }}
              >
                {engine.description}
              </div>
              {hasKey && (
                <div
                  style={{
                    fontSize: 11,
                    color: "var(--text-secondary)",
                    fontFamily: "var(--font-mono)",
                    padding: "4px 8px",
                    borderRadius: 4,
                    background: "var(--bg-primary)",
                    border: "1px solid var(--border-subtle)",
                  }}
                >
                  当前: {keys[engine.id]}
                </div>
              )}
              {engine.key_required || engine.id === "hackertarget" ? (
                <div style={{ display: "flex", gap: 6 }}>
                  <input
                    className="input"
                    style={{ flex: 1, fontSize: 12 }}
                    placeholder={
                      engine.key_hint || `输入 ${engine.key_label}`
                    }
                    value={keyInputs[engine.id] ?? ""}
                    onChange={(e) =>
                      setKeyInputs((prev) => ({
                        ...prev,
                        [engine.id]: e.target.value,
                      }))
                    }
                    type="password"
                  />
                  <button
                    className="btn btn-sm btn-primary"
                    onClick={() => onSave(engine.id)}
                    disabled={isSaving}
                    title="保存 Key"
                  >
                    {isSaving ? <Spinner size={12} /> : <Save size={12} />}
                  </button>
                  {hasKey && (
                    <button
                      className="btn btn-sm"
                      onClick={() => onDelete(engine.id)}
                      title="删除 Key"
                      style={{ color: "var(--accent-red)" }}
                    >
                      <Trash2 size={12} />
                    </button>
                  )}
                </div>
              ) : (
                <div
                  style={{
                    fontSize: 11,
                    color: "var(--text-muted)",
                    fontStyle: "italic",
                  }}
                >
                  无需 API Key，可直接使用
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ===== 子组件：报告面板 =====

interface ReportsPanelProps {
  reports: ReconReport[];
  viewingReport: string | null;
  reportContent: string;
  loadingReport: boolean;
  deletingReport: string | null;
  onView: (filename: string) => void;
  onDelete: (filename: string) => void;
  onDownload: (filename: string, content: string) => void;
  onCloseView: () => void;
  onRefresh: () => void;
}

function ReportsPanel({
  reports,
  viewingReport,
  reportContent,
  loadingReport,
  deletingReport,
  onView,
  onDelete,
  onDownload,
  onCloseView,
  onRefresh,
}: ReportsPanelProps) {
  return (
    <>
      <div
        style={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            padding: "8px 16px",
            borderBottom: "1px solid var(--border-default)",
            background: "var(--bg-secondary)",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <span className="text-xs text-muted">
            共 {reports.length} 个报告
          </span>
          <button className="btn btn-sm" onClick={onRefresh}>
            <RefreshCw size={12} />
            刷新
          </button>
        </div>
        <div style={{ flex: 1, overflow: "auto", padding: "8px 16px" }}>
          {reports.length === 0 ? (
            <div className="empty-state">
              <FileText size={36} />
              <span>暂无报告文件，执行搜索后将自动生成</span>
            </div>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>文件名</th>
                  <th style={{ width: 80, textAlign: "center" }}>大小</th>
                  <th style={{ width: 160 }}>修改时间</th>
                  <th style={{ width: 120, textAlign: "center" }}>操作</th>
                </tr>
              </thead>
              <tbody>
                {reports.map((report) => (
                  <tr key={report.filename}>
                    <td
                      style={{
                        fontSize: 12,
                        fontFamily: "var(--font-mono)",
                        color: "var(--text-primary)",
                      }}
                      title={report.filename}
                    >
                      {report.filename}
                    </td>
                    <td
                      style={{
                        textAlign: "center",
                        fontSize: 11,
                        color: "var(--text-muted)",
                      }}
                    >
                      {report.size_human}
                    </td>
                    <td
                      style={{
                        fontSize: 11,
                        color: "var(--text-muted)",
                      }}
                    >
                      {new Date(report.modified).toLocaleString("zh-CN")}
                    </td>
                    <td style={{ textAlign: "center" }}>
                      <div style={{ display: "inline-flex", gap: 4 }}>
                        <button
                          className="btn btn-sm"
                          onClick={() => onView(report.filename)}
                          title="查看报告"
                        >
                          <Eye size={12} />
                        </button>
                        <button
                          className="btn btn-sm"
                          onClick={() => onDelete(report.filename)}
                          title="删除报告"
                          style={{ color: "var(--accent-red)" }}
                          disabled={deletingReport === report.filename}
                        >
                          {deletingReport === report.filename ? (
                            <Spinner size={12} />
                          ) : (
                            <Trash2 size={12} />
                          )}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {viewingReport && (
        <div
          className="modal-overlay"
          style={{ zIndex: 2100 }}
          onClick={onCloseView}
        >
          <div
            className="modal-container"
            style={{ width: "85%", maxWidth: 900, maxHeight: "85vh" }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-header">
              <div className="modal-header-title">
                <FileText size={15} />
                {viewingReport}
              </div>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                }}
              >
                <button
                  className="btn btn-sm"
                  onClick={() => onDownload(viewingReport, reportContent)}
                  title="下载"
                >
                  <Download size={12} />
                </button>
                <button className="modal-close-btn" onClick={onCloseView}>
                  <X size={14} />
                </button>
              </div>
            </div>
            <div style={{ flex: 1, overflow: "auto", padding: 16 }}>
              {loadingReport ? (
                <div className="empty-state">
                  <Spinner size={24} />
                  <span>加载中...</span>
                </div>
              ) : (
                <MarkdownView content={reportContent} />
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
