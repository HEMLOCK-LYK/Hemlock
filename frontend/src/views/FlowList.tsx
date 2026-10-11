import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { RefreshCw, Search, Inbox, Repeat, Swords, Bot, Power, Wifi, Filter, X, ShieldOff, BarChart3, Globe, Tag, Trash2, Settings2, ExternalLink, ChevronDown, ArrowUp, ArrowDown, ArrowUpDown, KeyRound } from "lucide-react";
import { listFlows, getFlow, getNoiseStats, listNoiseFlows, markFlowNoise, getNoiseAnalysis, clearFlows, migrateFlowAuth } from "../api/flows";
import type { NoiseAnalysisItem } from "../api/flows";
import { FLOWTAG_COLORS } from "../api/flowtag";
import { FlowTagRules } from "./FlowTagRules";
import { useProject } from "../context/ProjectContext";
import { usePacket } from "../context/PacketContext";
import type { FlowListResponse, FlowListItem, Flow } from "../types";
import { MethodBadge, StatusCode, SourceBadge, EmptyState, Spinner } from "../components/Common";
import { SplitPane } from "../components/SplitPane";
import { FlowWindow } from "../components/FlowWindow";
import { FlowDetailPanel } from "../components/FlowDetailPanel";
import apiClient from "../api/client";

// ===== 列定义（Burp Suite 风格） =====

const COLUMN_STORAGE_KEY = "flowlist_column_widths";
const MIN_COL_WIDTH = 40;

interface ColumnDef {
  id: string;
  label: string;
  width: number;
}

const COLUMNS: ColumnDef[] = [
  { id: "number", label: "#", width: 50 },
  { id: "method", label: "方法", width: 70 },
  { id: "host", label: "Host", width: 150 },
  { id: "url", label: "URL", width: 400 },
  { id: "status", label: "状态", width: 60 },
  { id: "mime", label: "MIME", width: 100 },
  { id: "length", label: "长度", width: 80 },
  { id: "duration", label: "耗时", width: 70 },
  { id: "tags", label: "标签", width: 120 },
  { id: "source", label: "来源", width: 70 },
  { id: "time", label: "时间", width: 90 },
];

const DEFAULT_COLUMN_WIDTHS: Record<string, number> = Object.fromEntries(
  COLUMNS.map((c) => [c.id, c.width])
);

function loadColumnWidths(): Record<string, number> {
  try {
    const raw = localStorage.getItem(COLUMN_STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Record<string, unknown>;
      const merged: Record<string, number> = { ...DEFAULT_COLUMN_WIDTHS };
      for (const c of COLUMNS) {
        const v = parsed[c.id];
        if (typeof v === "number" && v >= MIN_COL_WIDTH) {
          merged[c.id] = v;
        }
      }
      return merged;
    }
  } catch {
    // ignore
  }
  return { ...DEFAULT_COLUMN_WIDTHS };
}

// ===== 数据格式化 =====

function formatLength(n: number | null): string {
  if (n == null) return "";
  if (n < 1024) return String(n);
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + "K";
  return (n / (1024 * 1024)).toFixed(1) + "M";
}

function formatMime(mime: string): string {
  if (!mime) return "";
  const idx = mime.indexOf("/");
  return idx >= 0 ? mime.slice(idx + 1).toUpperCase() : mime.toUpperCase();
}

function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return "";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

/** 耗时颜色：慢请求高亮（用于时间盲注识别） */
function durationColor(ms: number | null | undefined): string {
  if (ms == null) return "var(--text-muted)";
  if (ms >= 5000) return "var(--accent-red)";
  if (ms >= 1000) return "var(--accent-orange)";
  return "var(--text-secondary)";
}

interface FlowTagTag {
  name: string;
  color: string;
}

function parseFlowTagTags(tags: string, tagColors: string): FlowTagTag[] {
  const names = tags ? tags.split(",").map((s) => s.trim()).filter(Boolean) : [];
  if (names.length === 0) return [];
  const colors = tagColors ? tagColors.split(",").map((s) => s.trim()) : [];
  return names.map((name, i) => {
    const key = (colors[i] || "").toLowerCase();
    const hex = FLOWTAG_COLORS[key] ?? "";
    return { name, color: hex || "#7f7f7f" };
  });
}

export function FlowList() {
  const navigate = useNavigate();
  const { currentProjectId } = useProject();
  const { sendToRepeater, sendToIntruder, sendToAI } = usePacket();
  const [data, setData] = useState<FlowListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [filters, setFilters] = useState({
    method: "",
    url: "",
    host: "",
    source: "",
    status: "",
  });

  // 仅看标签开关
  const [tagsOnly, setTagsOnly] = useState(false);

  // 杂包过滤开关
  const [hideNoise, setHideNoise] = useState(false);

  // FlowTag 规则弹窗
  const [flowtagModalOpen, setFlowtagModalOpen] = useState(false);

  // 清空流量确认中
  const [clearing, setClearing] = useState(false);

  // ===== 可调列宽（Excel 风格） =====
  const [columnWidths, setColumnWidths] = useState<Record<string, number>>(loadColumnWidths);
  const [resizingId, setResizingId] = useState<string | null>(null);
  const resizeRef = useRef<{ columnId: string; startX: number; startWidth: number } | null>(null);

  // 持久化列宽
  useEffect(() => {
    try {
      localStorage.setItem(COLUMN_STORAGE_KEY, JSON.stringify(columnWidths));
    } catch {
      // ignore
    }
  }, [columnWidths]);

  // 拖拽过程中在 document 上监听 mousemove / mouseup
  useEffect(() => {
    if (!resizingId) return;
    const onMove = (e: MouseEvent) => {
      const r = resizeRef.current;
      if (!r) return;
      const delta = e.clientX - r.startX;
      const next = Math.max(MIN_COL_WIDTH, r.startWidth + delta);
      setColumnWidths((prev) => ({ ...prev, [r.columnId]: next }));
    };
    const onUp = () => {
      resizeRef.current = null;
      setResizingId(null);
    };
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    return () => {
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
  }, [resizingId]);

  const startResize = (e: React.MouseEvent, columnId: string) => {
    e.preventDefault();
    e.stopPropagation();
    const startWidth = columnWidths[columnId] ?? DEFAULT_COLUMN_WIDTHS[columnId] ?? 100;
    resizeRef.current = { columnId, startX: e.clientX, startWidth };
    setResizingId(columnId);
  };

  const totalWidth = COLUMNS.reduce((sum, c) => sum + (columnWidths[c.id] ?? c.width), 0);

  // ===== 表头排序（类 Burp Suite） =====
  type SortField = "number" | "method" | "host" | "url" | "status" | "mime" | "length" | "duration" | "source" | "time";
  const [sortField, setSortField] = useState<SortField | null>(null);
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");

  const handleSort = useCallback((field: SortField) => {
    if (sortField === field) {
      setSortDir((prev) => (prev === "asc" ? "desc" : "asc"));
    } else {
      setSortField(field);
      setSortDir("asc");
    }
  }, [sortField]);

  const sortedItems = useMemo(() => {
    if (!data?.items) return [];
    if (!sortField) return data.items;
    const arr = [...data.items];
    arr.sort((a, b) => {
      let cmp = 0;
      switch (sortField) {
        case "number":
          cmp = (a.flow_number ?? 0) - (b.flow_number ?? 0);
          break;
        case "method":
          cmp = a.method.localeCompare(b.method);
          break;
        case "host":
          cmp = a.host.localeCompare(b.host);
          break;
        case "url":
          cmp = (a.path || a.url).localeCompare(b.path || b.url);
          break;
        case "status":
          cmp = (a.resp_status ?? 0) - (b.resp_status ?? 0);
          break;
        case "mime":
          cmp = (a.mime_type ?? "").localeCompare(b.mime_type ?? "");
          break;
        case "length":
          cmp = (a.resp_length ?? 0) - (b.resp_length ?? 0);
          break;
        case "duration":
          cmp = (a.duration_ms ?? 0) - (b.duration_ms ?? 0);
          break;
        case "source":
          cmp = (a.source ?? "").localeCompare(b.source ?? "");
          break;
        case "time":
          cmp = ((a.timestamp ? new Date(a.timestamp).getTime() : 0) - (b.timestamp ? new Date(b.timestamp).getTime() : 0));
          break;
      }
      return sortDir === "asc" ? cmp : -cmp;
    });
    return arr;
  }, [data, sortField, sortDir]);

  /** 渲染可排序表头标签 */
  const renderHeaderLabel = (col: ColumnDef) => {
    const sortableFields: SortField[] = ["number", "method", "host", "url", "status", "mime", "length", "duration", "source", "time"];
    if (!sortableFields.includes(col.id as SortField)) {
      return <span>{col.label}</span>;
    }
    const isActive = sortField === col.id;
    return (
      <span
        style={{ cursor: "pointer", display: "flex", alignItems: "center", gap: 2, color: isActive ? "var(--accent-blue)" : undefined }}
        onClick={() => handleSort(col.id as SortField)}
      >
        {col.label}
        {isActive ? (
          sortDir === "asc" ? <ArrowUp size={10} /> : <ArrowDown size={10} />
        ) : (
          <ArrowUpDown size={10} style={{ opacity: 0.3 }} />
        )}
      </span>
    );
  };

  // 杂包管理弹窗
  const [noiseModalOpen, setNoiseModalOpen] = useState(false);
  const [noiseModalTab, setNoiseModalTab] = useState<"list" | "analysis">("list");
  const [noiseData, setNoiseData] = useState<FlowListResponse | null>(null);
  const [noiseStats, setNoiseStats] = useState<{ total: number; summary: { reason: string; count: number }[] } | null>(null);
  const [noiseLoading, setNoiseLoading] = useState(false);
  const [analysisData, setAnalysisData] = useState<NoiseAnalysisItem[]>([]);

  // 右键菜单状态
  const [contextMenu, setContextMenu] = useState<{
    x: number;
    y: number;
    flowId: string;
    isNoise: boolean;
  } | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  // 代理状态
  const [proxyRunning, setProxyRunning] = useState(false);
  const [proxyHost, setProxyHost] = useState("127.0.0.1");
  const [proxyPort, setProxyPort] = useState(8080);
  const [proxyToggling, setProxyToggling] = useState(false);
  const [proxyError, setProxyError] = useState<string | null>(null);

  // ===== 全局搜索 =====
  const [searchKeyword, setSearchKeyword] = useState("");

  // ===== 底部面板（单击选中） =====
  const [selectedFlowId, setSelectedFlowId] = useState<string | null>(null);
  const [selectedFlow, setSelectedFlow] = useState<Flow | null>(null);
  const [selectedFlowLoading, setSelectedFlowLoading] = useState(false);
  const [bottomPanelVisible, setBottomPanelVisible] = useState(false);

  // ===== 浮动窗口（双击打开） =====
  const [windowFlows, setWindowFlows] = useState<{ id: string; flow: Flow | null; loading: boolean }[]>([]);

  // 查询代理状态
  const fetchProxyStatus = useCallback(async () => {
    try {
      const resp = await apiClient.get("/proxy/status");
      setProxyRunning(resp.data.running);
      setProxyHost(resp.data.host);
      setProxyPort(resp.data.port);
    } catch {
      // 静默处理
    }
  }, []);

  useEffect(() => {
    fetchProxyStatus();
    const timer = setInterval(fetchProxyStatus, 5000);
    return () => clearInterval(timer);
  }, [fetchProxyStatus]);

  // 启动/停止代理
  const handleToggleProxy = async () => {
    setProxyToggling(true);
    setProxyError(null);
    try {
      const resp = proxyRunning
        ? await apiClient.post("/proxy/stop")
        : await apiClient.post("/proxy/start");

      // 从响应中读取状态和错误
      const { running, host, port, error } = resp.data;
      setProxyRunning(running);
      setProxyHost(host);
      setProxyPort(port);
      if (error) {
        setProxyError(error);
      }
    } catch (e) {
      setProxyError(e instanceof Error ? e.message : "操作失败");
      await fetchProxyStatus();
    } finally {
      setProxyToggling(false);
    }
  };

  const fetchData = useCallback(async () => {
    if (!currentProjectId) {
      setData(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const result = await listFlows({
        page,
        page_size: 50,
        method: filters.method || undefined,
        url: filters.url || undefined,
        host: filters.host || undefined,
        source: filters.source || undefined,
        status: filters.status ? parseInt(filters.status) : undefined,
        tags: tagsOnly ? "true" : undefined,
        project_id: currentProjectId,
        hide_noise: hideNoise,
        search: searchKeyword.trim() || undefined,
      });
      setData(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [page, filters, currentProjectId, hideNoise, tagsOnly, searchKeyword]);

  // 搜索防抖
  const handleSearchChange = useCallback((value: string) => {
    setSearchKeyword(value);
    setPage(1);
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // 自动刷新（5秒）
  useEffect(() => {
    if (!currentProjectId) return;
    const timer = setInterval(fetchData, 5000);
    return () => clearInterval(timer);
  }, [fetchData, currentProjectId]);

  // 单击：在底部面板展示数据包
  const handleRowClick = useCallback(async (item: FlowListItem) => {
    if (selectedFlowId === item.id) return; // 已选中则不重复加载
    setSelectedFlowId(item.id);
    setSelectedFlow(null);
    setSelectedFlowLoading(true);
    setBottomPanelVisible(true);
    try {
      const flow = await getFlow(item.id);
      setSelectedFlow(flow);
    } catch {
      // 静默处理
    } finally {
      setSelectedFlowLoading(false);
    }
  }, [selectedFlowId]);

  // 双击：在浮动窗口中打开数据包详情
  const handleRowDoubleClick = useCallback(async (item: FlowListItem) => {
    // 如果已在窗口中打开，不重复
    if (windowFlows.some((w) => w.id === item.id)) return;
    const windowId = item.id;
    setWindowFlows((prev) => [...prev, { id: windowId, flow: null, loading: true }]);
    try {
      const flow = await getFlow(item.id);
      setWindowFlows((prev) =>
        prev.map((w) => (w.id === windowId ? { ...w, flow, loading: false } : w))
      );
    } catch {
      setWindowFlows((prev) => prev.filter((w) => w.id !== windowId));
    }
  }, [windowFlows]);

  // 关闭浮动窗口
  const handleCloseWindow = useCallback((windowId: string) => {
    setWindowFlows((prev) => prev.filter((w) => w.id !== windowId));
  }, []);

  // 关闭底部面板
  const handleCloseBottomPanel = useCallback(() => {
    setBottomPanelVisible(false);
    setSelectedFlowId(null);
    setSelectedFlow(null);
  }, []);

  const handleFilterChange = (key: string, value: string) => {
    setFilters((prev) => ({ ...prev, [key]: value }));
    setPage(1);
  };

  // 右键菜单
  const handleContextMenu = (e: React.MouseEvent, item: FlowListItem) => {
    e.preventDefault();
    e.stopPropagation();
    setContextMenu({ x: e.clientX, y: e.clientY, flowId: item.id, isNoise: item.is_noise ?? false });
  };

  // 点击外部关闭菜单
  useEffect(() => {
    if (!contextMenu) return;
    const handleClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setContextMenu(null);
      }
    };
    const handleScroll = () => setContextMenu(null);
    document.addEventListener("mousedown", handleClick);
    document.addEventListener("scroll", handleScroll, true);
    return () => {
      document.removeEventListener("mousedown", handleClick);
      document.removeEventListener("scroll", handleScroll, true);
    };
  }, [contextMenu]);

  // 发送到 Repeater
  const handleSendToRepeater = async (flowId: string) => {
    setContextMenu(null);
    try {
      const flow = await getFlow(flowId);
      sendToRepeater({
        method: flow.method,
        url: flow.url,
        headers: flow.req_headers || "",
        body: flow.req_body || "",
      });
      navigate("/repeater");
    } catch {
      // 静默处理
    }
  };

  // 发送到 Intruder
  const handleSendToIntruder = async (flowId: string) => {
    setContextMenu(null);
    try {
      const flow = await getFlow(flowId);
      sendToIntruder({
        method: flow.method,
        url: flow.url,
        headers: flow.req_headers || "",
        body: flow.req_body || "",
      });
      navigate("/intruder");
    } catch {
      // 静默处理
    }
  };

  // 发送到 AI 助手
  const handleSendToAI = async (flowId: string) => {
    setContextMenu(null);
    try {
      const flow = await getFlow(flowId);
      sendToAI({
        method: flow.method,
        url: flow.url,
        headers: flow.req_headers || "",
        body: flow.req_body || "",
        respStatus: flow.resp_status ?? undefined,
        respHeaders: flow.resp_headers || "",
        respBody: flow.resp_body || "",
      });
      navigate("/agent");
    } catch {
      // 静默处理
    }
  };

  // 标记/取消标记杂包
  const handleToggleNoise = async (flowId: string, isNoise: boolean) => {
    setContextMenu(null);
    try {
      await markFlowNoise(flowId, !isNoise, isNoise ? "" : "手动标记");
      fetchData();
    } catch {
      // 静默处理
    }
  };

  // 登录态一键迁移：把该流量的认证头注入 AI 会话（凭据只进内存）
  const handleMigrateAuth = async (flowId: string) => {
    setContextMenu(null);
    try {
      const r = await migrateFlowAuth(flowId);
      if (!r.migrated) {
        alert(`迁移失败: ${r.error ?? "未知原因"}`);
        return;
      }
      const v = r.validation;
      const vText = v
        ? v.valid === true
          ? `\n\n登录态验证: 有效（${v.reason}）`
          : v.valid === false
            ? `\n\n登录态验证: 已失效（${v.reason}）\n请在浏览器重新登录后再迁移一次`
            : `\n\n登录态验证: 未确定（${v.reason}）`
        : "";
      alert(
        `已迁移到 AI 会话: ${r.injected?.join("、") ?? ""}${vText}`
      );
    } catch (e) {
      alert(`迁移失败: ${e instanceof Error ? e.message : e}`);
    }
  };

  // 加载杂包列表
  const fetchNoiseData = useCallback(async () => {
    if (!currentProjectId) return;
    setNoiseLoading(true);
    try {
      const [flows, stats, analysis] = await Promise.all([
        listNoiseFlows({ project_id: currentProjectId, page_size: 200 }),
        getNoiseStats(currentProjectId),
        getNoiseAnalysis(30),
      ]);
      setNoiseData(flows);
      setNoiseStats(stats);
      setAnalysisData(analysis.items);
    } catch {
      // 静默处理
    } finally {
      setNoiseLoading(false);
    }
  }, [currentProjectId]);

  const handleOpenNoiseModal = () => {
    setNoiseModalOpen(true);
    setNoiseModalTab("list");
    fetchNoiseData();
  };

  // 在杂包弹窗中取消标记
  const handleUnmarkNoise = async (flowId: string) => {
    try {
      await markFlowNoise(flowId, false, "");
      fetchNoiseData();
      fetchData();
    } catch {
      // 静默处理
    }
  };

  // 清空当前项目所有流量（不包括 intruder 源）
  const handleClearFlows = async () => {
    if (!currentProjectId) return;
    if (!window.confirm("确定清空当前项目的所有流量？此操作不影响 Repeater/Intruder 等数据。")) return;
    setClearing(true);
    try {
      await clearFlows(currentProjectId);
      fetchData();
    } catch {
      // 静默处理
    } finally {
      setClearing(false);
    }
  };

  const formatTime = (ts: string | null) => {
    if (!ts) return "";
    try {
      const d = new Date(ts);
      return d.toLocaleTimeString("zh-CN", { hour12: false });
    } catch {
      return ts;
    }
  };

  // 渲染 FlowTag 标签单元格（固定单行高度，溢出隐藏，保证行高一致）
  const renderTagsCell = (item: FlowListItem) => {
    const tags = parseFlowTagTags(item.tags, item.tag_colors);
    if (tags.length === 0) return null;
    return (
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 2,
          overflow: "hidden",
          height: 16,
          maxHeight: 16,
        }}
        title={tags.map((t) => t.name).join(", ")}
      >
        {tags.map((t, i) => (
          <span
            key={`${t.name}-${i}`}
            style={{
              display: "inline-block",
              backgroundColor: t.color,
              color: "#fff",
              fontSize: 10,
              padding: "0 5px",
              borderRadius: 3,
              lineHeight: "16px",
              whiteSpace: "nowrap",
              maxWidth: 100,
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {t.name}
          </span>
        ))}
      </div>
    );
  };

  const ellipsisStyle: React.CSSProperties = {
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };

  return (
    <div className="flex flex-col h-full">
      {/* 列宽拖拽手柄的 hover 高亮样式 */}
      <style>{`
        .col-resize-handle:hover .col-resize-line,
        .col-resize-handle.active .col-resize-line {
          background: var(--accent-blue) !important;
          box-shadow: 0 0 4px var(--accent-blue);
        }
      `}</style>

      <div className="view-header">
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span className="view-title">流量列表</span>
          {data && (
            <span className="view-info-tag">
              共 {data.total} 条
              {data.noise_count != null && data.noise_count > 0 && (
                <span style={{ color: "var(--accent-orange)" }}>
                  · 杂包 {data.noise_count}
                </span>
              )}
            </span>
          )}
        </div>
        <div className="view-actions" style={{ gap: 6, alignItems: "center" }}>
          {/* 代理状态组 */}
          <span className={`proxy-status-badge ${proxyRunning ? "running" : "stopped"}`}>
            <Wifi size={11} />
            {proxyRunning ? `${proxyHost}:${proxyPort}` : "代理未启动"}
          </span>
          <button
            className="btn btn-sm"
            onClick={handleToggleProxy}
            disabled={proxyToggling}
            style={{
              backgroundColor: proxyRunning
                ? "var(--accent-red)"
                : "var(--accent-green)",
              color: "#fff",
              border: "none",
            }}
          >
            {proxyToggling ? (
              <Spinner size={12} />
            ) : (
              <Power size={12} />
            )}
            {proxyRunning ? "停止" : "启动"}
          </button>
          {proxyError && (
            <span style={{ fontSize: 11, color: "var(--accent-red)", maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={proxyError}>
              {proxyError}
            </span>
          )}
          <div className="btn-group-separator" />
          <button className="btn btn-sm" onClick={fetchData} disabled={loading}>
            {loading ? <Spinner size={12} /> : <RefreshCw size={12} />}
            刷新
          </button>
          {data && data.total > 0 && (
            <button
              className="btn-outline-danger"
              onClick={handleClearFlows}
              disabled={clearing}
              title="清空当前项目的所有流量（不影响 Repeater/Intruder）"
            >
              {clearing ? <Spinner size={12} /> : <Trash2 size={12} />}
              清空
            </button>
          )}
        </div>
      </div>

      {/* 过滤栏 */}
      <div className="filter-bar">
        <div className="filter-group">
          {/* 全局搜索（匹配所有字段） */}
          <div style={{ position: "relative", display: "flex", alignItems: "center" }}>
            <Search size={14} style={{ position: "absolute", left: 8, color: "var(--text-muted)", pointerEvents: "none" }} />
            <input
              className="input"
              style={{ paddingLeft: 28, width: 220, borderColor: searchKeyword ? "var(--accent-blue)" : undefined }}
              placeholder="搜索所有字段（请求头/体/响应...）"
              value={searchKeyword}
              onChange={(e) => handleSearchChange(e.target.value)}
              title="在所有数据包字段中搜索关键字"
            />
            {searchKeyword && (
              <button
                onClick={() => handleSearchChange("")}
                style={{
                  position: "absolute",
                  right: 4,
                  border: "none",
                  background: "transparent",
                  cursor: "pointer",
                  color: "var(--text-muted)",
                  padding: 2,
                  display: "flex",
                }}
                title="清除搜索"
              >
                <X size={12} />
              </button>
            )}
          </div>
          <div className="filter-divider" style={{ margin: "0 4px" }} />
          <Globe size={14} className="text-muted" />
          <input
            className="input"
            style={{ width: 140 }}
            placeholder="Host..."
            value={filters.host}
            onChange={(e) => handleFilterChange("host", e.target.value)}
          />
          <select
            className="select"
            value={filters.method}
            onChange={(e) => handleFilterChange("method", e.target.value)}
          >
            <option value="">所有方法</option>
            <option value="GET">GET</option>
            <option value="POST">POST</option>
            <option value="PUT">PUT</option>
            <option value="DELETE">DELETE</option>
            <option value="PATCH">PATCH</option>
          </select>
          <select
            className="select"
            value={filters.source}
            onChange={(e) => handleFilterChange("source", e.target.value)}
          >
            <option value="">所有来源</option>
            <option value="proxy">proxy</option>
            <option value="repeater">repeater</option>
            <option value="intruder">intruder</option>
          </select>
          <input
            className="input"
            style={{ width: 80 }}
            placeholder="状态码"
            value={filters.status}
            onChange={(e) => handleFilterChange("status", e.target.value)}
          />
        </div>
        <div style={{ flex: 1 }} />
        <div className="filter-divider" />
        <div className="filter-group">
          <button
            className={`toggle-btn ${tagsOnly ? "active active-blue" : ""}`}
            onClick={() => {
              setTagsOnly((v) => !v);
              setPage(1);
            }}
            title="仅显示带有 FlowTag 标签的流量"
          >
            <Tag size={12} />
            {tagsOnly ? "仅看标签中" : "仅看标签"}
          </button>
          <button
            className="btn btn-sm"
            onClick={() => setFlowtagModalOpen(true)}
            title="管理 FlowTag 正则匹配规则"
          >
            <Settings2 size={12} />
            FlowTag 规则
          </button>
          <button
            className={`toggle-btn ${hideNoise ? "active active-orange" : ""}`}
            onClick={() => setHideNoise((v) => !v)}
            title="过滤心跳包、静态资源等杂包"
          >
            <Filter size={12} />
            {hideNoise ? "过滤中" : "过滤杂包"}
          </button>
          <button
            className="btn btn-sm"
            onClick={handleOpenNoiseModal}
            title="查看和管理杂包列表"
          >
            <ShieldOff size={12} />
            杂包列表
          </button>
        </div>
      </div>

      {/* 数据表格 + 底部详情面板 */}
      {bottomPanelVisible && selectedFlowId ? (
        <div style={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column" }}>
          <SplitPane
            direction="vertical"
            initialSplit={0.55}
            minSize={80}
            storageKey="flowlist_main_split"
            firstHeader={
              <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 12px", fontSize: 11, color: "var(--text-muted)" }}>
                <span style={{ fontWeight: 600, color: "var(--text-secondary)" }}>流量列表</span>
                {data && <span>共 {data.total} 条</span>}
                <span style={{ fontSize: 10 }}>(单击选中 · 双击新窗口打开 · 右键菜单)</span>
              </div>
            }
            secondHeader={
              <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 12px", fontSize: 11 }}>
                <span style={{ fontWeight: 600, color: "var(--text-secondary)" }}>数据包详情</span>
                {selectedFlow && (
                  <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--text-muted)" }}>
                    #{selectedFlow.flow_number ?? "-"}
                  </span>
                )}
                <span style={{ flex: 1 }} />
                <button
                  className="btn btn-sm"
                  onClick={() => selectedFlow && handleRowDoubleClick({ id: selectedFlow.id } as FlowListItem)}
                  title="在新窗口打开"
                  style={{ padding: "2px 6px", fontSize: 11 }}
                >
                  <ExternalLink size={12} /> 新窗口
                </button>
                <button
                  className="btn btn-sm"
                  onClick={handleCloseBottomPanel}
                  title="关闭详情面板"
                  style={{ padding: "2px 6px", fontSize: 11 }}
                >
                  <ChevronDown size={12} /> 收起
                </button>
              </div>
            }
            first={
              <div className="scroll-container" style={{ height: "100%", width: "100%" }}>
                {data && data.items.length > 0 && (
                  <table
                    className="data-table"
                    style={{ tableLayout: "fixed", width: "100%", minWidth: totalWidth }}
                  >
                    <thead>
                      <tr>
                        {COLUMNS.map((col) => {
                          const w = columnWidths[col.id] ?? col.width;
                          return (
                            <th key={col.id} style={{ width: w, position: "relative" }}>
                              {renderHeaderLabel(col)}
                              <div
                                className={`col-resize-handle${resizingId === col.id ? " active" : ""}`}
                                onMouseDown={(e) => startResize(e, col.id)}
                                style={{
                                  position: "absolute", right: 0, top: 0, bottom: 0, width: 6,
                                  cursor: "col-resize", zIndex: 2,
                                  display: "flex", alignItems: "center", justifyContent: "center",
                                }}
                              >
                                <span className="col-resize-line" style={{ display: "block", width: 1, height: "60%", background: "var(--border-default)" }} />
                              </div>
                            </th>
                          );
                        })}
                      </tr>
                    </thead>
                    <tbody>
                      {sortedItems.map((item) => {
                        const isSelected = selectedFlowId === item.id;
                        return (
                          <tr
                            key={item.id}
                            onClick={() => handleRowClick(item)}
                            onDoubleClick={() => handleRowDoubleClick(item)}
                            onContextMenu={(e) => handleContextMenu(e, item)}
                            style={{
                              cursor: "pointer",
                              backgroundColor: isSelected ? "rgba(88,166,255,0.08)" : undefined,
                            }}
                          >
                            <td style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-muted)", ...ellipsisStyle }}>
                              {item.flow_number ?? "-"}
                            </td>
                            <td><MethodBadge method={item.method} /></td>
                            <td style={{ ...ellipsisStyle, fontFamily: "var(--font-mono)", fontSize: 11 }} title={item.host}>{item.host}</td>
                            <td className="url-cell" style={ellipsisStyle} title={item.title ? `${item.title}\n${item.url}` : item.url}>
                              {item.path || item.url}
                            </td>
                            <td><StatusCode status={item.resp_status} /></td>
                            <td style={{ ...ellipsisStyle, fontSize: 11 }} title={item.mime_type}>{formatMime(item.mime_type)}</td>
                            <td style={{ fontFamily: "var(--font-mono)", fontSize: 11, ...ellipsisStyle }}>{formatLength(item.resp_length)}</td>
                            <td style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: durationColor(item.duration_ms), ...ellipsisStyle }} title={item.duration_ms != null ? `${item.duration_ms} ms` : ""}>
                              {formatDuration(item.duration_ms)}
                            </td>
                            <td>{renderTagsCell(item)}</td>
                            <td><SourceBadge source={item.source} /></td>
                            <td className="time-cell">{formatTime(item.timestamp)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                )}
              </div>
            }
            second={
              selectedFlowLoading ? (
                <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%" }}>
                  <Spinner size={20} />
                </div>
              ) : selectedFlow ? (
                <FlowDetailPanel flow={selectedFlow} storageKeyPrefix="flowlist_bottom" />
              ) : (
                <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "var(--text-muted)", fontSize: 12 }}>
                  加载失败
                </div>
              )
            }
          />
        </div>
      ) : (
      <div className="scroll-container">
        {!currentProjectId && (
          <EmptyState
            icon={<Inbox />}
            message="请先选择或创建项目，流量数据按项目隔离"
          />
        )}

        {currentProjectId && error && (
          <div style={{ padding: 16, color: "var(--accent-red)" }}>
            加载失败: {error}
          </div>
        )}

        {currentProjectId && !error && !loading && data && data.items.length === 0 && (
          <EmptyState
            icon={<Inbox />}
            message={
              hideNoise
                ? "当前无流量（已过滤杂包），关闭过滤可查看全部"
                : proxyRunning
                ? `代理运行中 (${proxyHost}:${proxyPort})，将浏览器代理设置为此地址即可抓包`
                : "点击上方「启动代理」按钮，然后将浏览器代理设置为指定地址即可开始抓包"
            }
          />
        )}

        {data && data.items.length > 0 && (
          <table
            className="data-table"
            style={{
              tableLayout: "fixed",
              width: "100%",
              minWidth: totalWidth,
            }}
          >
            <thead>
              <tr>
                {COLUMNS.map((col) => {
                  const w = columnWidths[col.id] ?? col.width;
                  return (
                    <th key={col.id} style={{ width: w, position: "relative" }}>
                      {renderHeaderLabel(col)}
                      {/* 列宽拖拽手柄 */}
                      <div
                        className={`col-resize-handle${resizingId === col.id ? " active" : ""}`}
                        onMouseDown={(e) => startResize(e, col.id)}
                        style={{
                          position: "absolute",
                          right: 0,
                          top: 0,
                          bottom: 0,
                          width: 6,
                          cursor: "col-resize",
                          zIndex: 2,
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                        }}
                      >
                        <span
                          className="col-resize-line"
                          style={{
                            display: "block",
                            width: 1,
                            height: "60%",
                            background: "var(--border-default)",
                          }}
                        />
                      </div>
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {sortedItems.map((item) => {
                const isSelected = selectedFlowId === item.id;
                return (
                <tr
                  key={item.id}
                  onClick={() => handleRowClick(item)}
                  onDoubleClick={() => handleRowDoubleClick(item)}
                  onContextMenu={(e) => handleContextMenu(e, item)}
                  style={{
                    cursor: "pointer",
                    backgroundColor: isSelected ? "rgba(88,166,255,0.08)" : undefined,
                  }}
                >
                  <td
                    style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: 11,
                      color: "var(--text-muted)",
                      ...ellipsisStyle,
                    }}
                  >
                    {item.flow_number ?? "-"}
                  </td>
                  <td>
                    <MethodBadge method={item.method} />
                  </td>
                  <td style={{ ...ellipsisStyle, fontFamily: "var(--font-mono)", fontSize: 11 }} title={item.host}>
                    {item.host}
                  </td>
                  <td
                    className="url-cell"
                    style={ellipsisStyle}
                    title={item.title ? `${item.title}\n${item.url}` : item.url}
                  >
                    {item.path || item.url}
                  </td>
                  <td>
                    <StatusCode status={item.resp_status} />
                  </td>
                  <td style={{ ...ellipsisStyle, fontSize: 11 }} title={item.mime_type}>
                    {formatMime(item.mime_type)}
                  </td>
                  <td
                    style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: 11,
                      ...ellipsisStyle,
                    }}
                  >
                    {formatLength(item.resp_length)}
                  </td>
                  <td
                    style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: 11,
                      color: durationColor(item.duration_ms),
                      ...ellipsisStyle,
                    }}
                    title={item.duration_ms != null ? `${item.duration_ms} ms` : ""}
                  >
                    {formatDuration(item.duration_ms)}
                  </td>
                  <td>{renderTagsCell(item)}</td>
                  <td>
                    <SourceBadge source={item.source} />
                  </td>
                  <td className="time-cell">{formatTime(item.timestamp)}</td>
                </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
      )}

      {/* 分页 */}
      {data && data.total > data.page_size && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: 12,
            padding: "8px 16px",
            borderTop: "1px solid var(--border-default)",
            background: "var(--bg-secondary)",
          }}
        >
          <button
            className="btn btn-sm"
            disabled={page <= 1}
            onClick={() => setPage((p) => p - 1)}
          >
            上一页
          </button>
          <span className="text-xs text-muted">
            {page} / {Math.ceil(data.total / data.page_size)}
          </span>
          <button
            className="btn btn-sm"
            disabled={page * data.page_size >= data.total}
            onClick={() => setPage((p) => p + 1)}
          >
            下一页
          </button>
        </div>
      )}

      {/* 右键上下文菜单 */}
      {contextMenu && (
        <div
          ref={menuRef}
          className="context-menu"
          style={{
            position: "fixed",
            top: contextMenu.y,
            left: contextMenu.x,
            zIndex: 1000,
          }}
        >
          <div
            className="context-menu-item"
            onClick={() => handleSendToRepeater(contextMenu.flowId)}
          >
            <Repeat size={13} />
            发送到重放器
          </div>
          <div
            className="context-menu-item"
            onClick={() => handleSendToIntruder(contextMenu.flowId)}
          >
            <Swords size={13} />
            发送到爆破器
          </div>
          <div
            className="context-menu-item"
            onClick={() => handleSendToAI(contextMenu.flowId)}
          >
            <Bot size={13} />
            发送到 AI 助手
          </div>
          <div
            className="context-menu-item"
            onClick={() => handleMigrateAuth(contextMenu.flowId)}
          >
            <KeyRound size={13} />
            迁移认证头到 AI 会话
          </div>
          <div style={{ borderTop: "1px solid var(--border-default)", margin: "4px 0" }} />
          <div
            className="context-menu-item"
            onClick={() => handleToggleNoise(contextMenu.flowId, contextMenu.isNoise)}
          >
            <ShieldOff size={13} />
            {contextMenu.isNoise ? "取消杂包标记" : "标记为杂包"}
          </div>
        </div>
      )}

      {/* 杂包管理弹窗 */}
      {noiseModalOpen && (
        <div
          className="modal-overlay"
          onClick={() => setNoiseModalOpen(false)}
        >
          <div
            className="modal-container"
            style={{ width: "80%", maxWidth: 900, maxHeight: "70vh" }}
            onClick={(e) => e.stopPropagation()}
          >
            {/* 弹窗头部 */}
            <div className="modal-header">
              <div className="modal-header-title">
                <ShieldOff size={16} style={{ color: "var(--accent-orange)" }} />
                杂包管理
                {noiseStats && noiseModalTab === "list" && (
                  <span className="modal-header-hint">
                    共 {noiseStats.total} 条
                  </span>
                )}
              </div>
              <button
                className="modal-close-btn"
                onClick={() => setNoiseModalOpen(false)}
              >
                <X size={14} />
              </button>
            </div>

            {/* Tab 切换 */}
            <div style={{ display: "flex", borderBottom: "1px solid var(--border-default)" }}>
              <button
                onClick={() => setNoiseModalTab("list")}
                style={{
                  padding: "8px 16px",
                  fontSize: 12,
                  fontWeight: 500,
                  color: noiseModalTab === "list" ? "var(--accent-blue)" : "var(--text-muted)",
                  borderBottom: noiseModalTab === "list" ? "2px solid var(--accent-blue)" : "2px solid transparent",
                  background: "none",
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  gap: 4,
                }}
              >
                <ShieldOff size={12} />
                杂包列表
              </button>
              <button
                onClick={() => setNoiseModalTab("analysis")}
                style={{
                  padding: "8px 16px",
                  fontSize: 12,
                  fontWeight: 500,
                  color: noiseModalTab === "analysis" ? "var(--accent-blue)" : "var(--text-muted)",
                  borderBottom: noiseModalTab === "analysis" ? "2px solid var(--accent-blue)" : "2px solid transparent",
                  background: "none",
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  gap: 4,
                }}
              >
                <BarChart3 size={12} />
                抽样排序
              </button>
            </div>

            {/* 统计摘要（仅列表模式显示） */}
            {noiseModalTab === "list" && noiseStats && noiseStats.summary.length > 0 && (
              <div
                style={{
                  display: "flex",
                  gap: 8,
                  padding: "8px 16px",
                  borderBottom: "1px solid var(--border-default)",
                  flexWrap: "wrap",
                }}
              >
                {noiseStats.summary.map((s) => (
                  <span
                    key={s.reason}
                    style={{
                      fontSize: 11,
                      padding: "2px 8px",
                      borderRadius: 3,
                      backgroundColor: "var(--bg-tertiary)",
                      color: "var(--text-muted)",
                    }}
                  >
                    {s.reason}: {s.count}
                  </span>
                ))}
              </div>
            )}

            {/* 内容区 */}
            <div style={{ flex: 1, overflow: "auto", padding: "4px 0" }}>
              {noiseLoading && (
                <div style={{ padding: 24, textAlign: "center", color: "var(--text-muted)" }}>
                  <Spinner size={16} />
                </div>
              )}

              {/* === 杂包列表 Tab === */}
              {!noiseLoading && noiseModalTab === "list" && (
                <>
                  {noiseData && noiseData.items.length === 0 && (
                    <div style={{ padding: 24, textAlign: "center", color: "var(--text-muted)", fontSize: 13 }}>
                      暂无杂包
                    </div>
                  )}
                  {noiseData && noiseData.items.length > 0 && (
                    <table className="data-table" style={{ fontSize: 12 }}>
                      <thead>
                        <tr>
                          <th style={{ width: 60 }}>方法</th>
                          <th>URL</th>
                          <th style={{ width: 100 }}>原因</th>
                          <th style={{ width: 80 }}>操作</th>
                        </tr>
                      </thead>
                      <tbody>
                        {noiseData.items.map((item) => (
                          <tr key={item.id}>
                            <td>
                              <MethodBadge method={item.method} />
                            </td>
                            <td className="url-cell" title={item.url} style={{ maxWidth: 400 }}>
                              {item.url}
                            </td>
                            <td>
                              <span
                                style={{
                                  fontSize: 10,
                                  padding: "1px 6px",
                                  borderRadius: 3,
                                  backgroundColor: "var(--accent-orange)",
                                  color: "#fff",
                                }}
                              >
                                {item.noise_reason || "杂包"}
                              </span>
                            </td>
                            <td>
                              <button
                                className="btn btn-sm"
                                onClick={() => handleUnmarkNoise(item.id)}
                                style={{ fontSize: 11, padding: "1px 8px" }}
                              >
                                取消标记
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </>
              )}

              {/* === 抽样排序 Tab === */}
              {!noiseLoading && noiseModalTab === "analysis" && (
                <>
                  {analysisData.length === 0 && (
                    <div style={{ padding: 24, textAlign: "center", color: "var(--text-muted)", fontSize: 13 }}>
                      暂无分析数据，请确保代理正在运行并产生流量
                    </div>
                  )}
                  {analysisData.length > 0 && (
                    <>
                      <div
                        style={{
                          padding: "8px 16px",
                          fontSize: 11,
                          color: "var(--text-muted)",
                          borderBottom: "1px solid var(--border-default)",
                        }}
                      >
                        按当前窗口（60秒）内请求数降序排列，帮助识别高频杂包来源
                      </div>
                      <table className="data-table" style={{ fontSize: 12 }}>
                        <thead>
                          <tr>
                            <th style={{ width: 50 }}>排名</th>
                            <th style={{ width: 60 }}>类型</th>
                            <th>目标</th>
                            <th style={{ width: 80 }}>请求数</th>
                            <th style={{ width: 80 }}>URL种类</th>
                            <th style={{ width: 70 }}>状态</th>
                          </tr>
                        </thead>
                        <tbody>
                          {analysisData.map((item, idx) => (
                            <tr key={`${item.type}-${item.key}`}>
                              <td style={{ fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>
                                {idx + 1}
                              </td>
                              <td>
                                <span
                                  style={{
                                    fontSize: 10,
                                    padding: "1px 6px",
                                    borderRadius: 3,
                                    backgroundColor: item.type === "host"
                                      ? "rgba(188,140,255,0.12)"
                                      : "rgba(88,166,255,0.12)",
                                    color: item.type === "host"
                                      ? "var(--accent-purple)"
                                      : "var(--accent-blue)",
                                    fontWeight: 600,
                                  }}
                                >
                                  {item.type === "host" ? "主机" : "URL"}
                                </span>
                              </td>
                              <td className="url-cell" title={item.key} style={{ maxWidth: 350 }}>
                                {item.key}
                              </td>
                              <td>
                                <span style={{ fontFamily: "var(--font-mono)", fontWeight: 700, color: "var(--accent-orange)" }}>
                                  {item.count}
                                </span>
                              </td>
                              <td style={{ fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>
                                {item.type === "host" ? (item.url_variety ?? "-") : "-"}
                              </td>
                              <td>
                                {item.is_flagged ? (
                                  <span
                                    style={{
                                      fontSize: 10,
                                      padding: "1px 6px",
                                      borderRadius: 3,
                                      backgroundColor: "var(--accent-orange)",
                                      color: "#fff",
                                    }}
                                  >
                                    已标记
                                  </span>
                                ) : (
                                  <span style={{ fontSize: 10, color: "var(--text-muted)" }}>-</span>
                                )}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </>
                  )}
                </>
              )}
            </div>

            {/* 弹窗底部 */}
            <div className="modal-footer">
              <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                自动检测心跳包、静态资源、追踪服务、高频重复请求
              </span>
              <button
                className="btn btn-sm"
                onClick={() => {
                  fetchNoiseData();
                  fetchData();
                }}
                disabled={noiseLoading}
              >
                {noiseLoading ? <Spinner size={12} /> : <RefreshCw size={12} />}
                刷新
              </button>
            </div>
          </div>
        </div>
      )}

      {/* FlowTag 规则管理弹窗 */}
      {flowtagModalOpen && (
        <div
          className="modal-overlay"
          onClick={() => setFlowtagModalOpen(false)}
        >
          <div
            className="modal-container"
            style={{ width: "85%", maxWidth: 1000, maxHeight: "80vh" }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="modal-header">
              <div className="modal-header-title">
                <Settings2 size={16} style={{ color: "var(--accent-blue)" }} />
                FlowTag 正则匹配规则
                <span className="modal-header-hint">
                  命中规则的流量会自动标注标签
                </span>
              </div>
              <button
                className="modal-close-btn"
                onClick={() => setFlowtagModalOpen(false)}
              >
                <X size={14} />
              </button>
            </div>
            <div className="modal-body">
              <FlowTagRules embedded />
            </div>
          </div>
        </div>
      )}

      {/* ===== 浮动数据包详情窗口（双击打开） ===== */}
      {windowFlows.map((w, idx) => (
        <FlowWindow
          key={w.id}
          title={
            <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
              {w.flow ? (
                <>
                  <MethodBadge method={w.flow.method} />
                  <span style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>
                    #{w.flow.flow_number ?? "-"} {w.flow.host}{w.flow.path}
                  </span>
                </>
              ) : (
                <span>加载中...</span>
              )}
            </span>
          }
          onClose={() => handleCloseWindow(w.id)}
          initialX={120 + idx * 40}
          initialY={80 + idx * 30}
          initialWidth={960}
          initialHeight={640}
        >
          {w.loading ? (
            <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%" }}>
              <Spinner size={24} />
            </div>
          ) : w.flow ? (
            <FlowDetailPanel flow={w.flow} storageKeyPrefix={`flowlist_win_${w.id}`} />
          ) : (
            <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "var(--text-muted)" }}>
              加载失败
            </div>
          )}
        </FlowWindow>
      ))}
    </div>
  );
}
