import { useState, useEffect, useRef, useCallback } from "react";
import { Zap, ArrowUpToLine, ArrowDownToLine, SkipForward, X, RefreshCw, Power, Wifi, Inbox } from "lucide-react";
import { getProxyStatus, startProxy, stopProxy } from "../api/proxy";
import { getInterceptStatus, setInterceptConfig, getPendingList, forwardIntercept, dropIntercept } from "../api/intercept";
import { useWebSocket } from "../hooks/useWebSocket";
import type { WSEvent, InterceptStatus, InterceptPendingList } from "../types";
import { MethodBadge, StatusCode, EmptyState, Spinner } from "../components/Common";
import { CodeEditor } from "../components/CodeEditor";

/**
 * 拦截器 — Burp 风格「抓包拦截」视图
 *
 * 总开关 + 「拦截请求」「拦截响应」两个子开关；流量经代理时被挂起，
 * 在此编辑原始包后 Forward（放行）/ Drop（丢弃）。
 *
 * 数据一致性策略：WS 的 intercept_* 事件只是「有变化」通知，
 * 收到后全量重拉 GET /status + GET /pending，以 GET 数据为准。
 */
export function Intercept() {
  // ===== 代理状态（view-header 右上角，与流量列表一致） =====
  const [proxyRunning, setProxyRunning] = useState(false);
  const [proxyHost, setProxyHost] = useState("127.0.0.1");
  const [proxyPort, setProxyPort] = useState(8080);
  const [proxyToggling, setProxyToggling] = useState(false);
  const [proxyError, setProxyError] = useState<string | null>(null);

  // ===== 拦截状态 + 挂起队列 =====
  const [status, setStatus] = useState<InterceptStatus | null>(null);
  const [pending, setPending] = useState<InterceptPendingList | null>(null);

  // ===== 编辑区草稿（所见即所得，编辑后的字节即实际发送内容） =====
  const [draftRaw, setDraftRaw] = useState("");
  const activeIdRef = useRef<string | null>(null);
  const [acting, setActing] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [st, pendingList] = await Promise.all([getInterceptStatus(), getPendingList()]);
      setStatus(st);
      setPending(pendingList);
    } catch {
      // 静默处理
    }
  }, []);

  // 查询代理状态
  const fetchProxyStatus = useCallback(async () => {
    try {
      const resp = await getProxyStatus();
      setProxyRunning(resp.running);
      setProxyHost(resp.host);
      setProxyPort(resp.port);
    } catch {
      // 静默处理
    }
  }, []);

  // 启动/停止代理
  const handleToggleProxy = async () => {
    setProxyToggling(true);
    setProxyError(null);
    try {
      const data = proxyRunning ? await stopProxy() : await startProxy();
      setProxyRunning(data.running);
      setProxyHost(data.host);
      setProxyPort(data.port);
      refresh();
    } catch (e) {
      setProxyError(e instanceof Error ? e.message : "操作失败");
      await fetchProxyStatus();
    } finally {
      setProxyToggling(false);
    }
  };

  // ===== 初始加载 + 定时兜底刷新 =====
  useEffect(() => {
    refresh();
    fetchProxyStatus();
    const timer = setInterval(() => {
      fetchProxyStatus();
      refresh();
    }, 3000);
    return () => clearInterval(timer);
  }, [refresh, fetchProxyStatus]);

  // ===== WS 事件：任一 intercept_* 变化 → 全量重拉 =====
  const handleWsEvent = useCallback((e: WSEvent) => {
    if (typeof e.event === "string" && e.event.startsWith("intercept")) {
      refresh();
    }
  }, [refresh]);
  useWebSocket(handleWsEvent, refresh);

  // ===== 活跃项 = 队列第一个（FIFO） =====
  const activeItem = pending?.items[0] ?? null;
  const activeId = activeItem?.id ?? null;

  // 活跃项变化时重置草稿
  useEffect(() => {
    if (activeId && activeId !== activeIdRef.current) {
      activeIdRef.current = activeId;
      setDraftRaw(activeItem?.raw ?? "");
    }
    if (!activeId) {
      activeIdRef.current = null;
    }
  }, [activeId, activeItem?.raw]);

  // ===== 开关 =====
  const toggleEnabled = async () => {
    if (!status) return;
    const next = !status.enabled;
    setStatus((s) => (s ? { ...s, enabled: next } : s));
    try {
      await setInterceptConfig({ enabled: next });
    } catch {
      // 静默处理
    }
    refresh();
  };

  const toggleRequests = async () => {
    if (!status) return;
    const next = !status.intercept_requests;
    setStatus((s) => (s ? { ...s, intercept_requests: next } : s));
    try {
      await setInterceptConfig({ intercept_requests: next });
    } catch {
      // 静默处理
    }
    refresh();
  };

  const toggleResponses = async () => {
    if (!status) return;
    const next = !status.intercept_responses;
    setStatus((s) => (s ? { ...s, intercept_responses: next } : s));
    try {
      await setInterceptConfig({ intercept_responses: next });
    } catch {
      // 静默处理
    }
    refresh();
  };

  // ===== Forward / Drop =====
  const handleForward = async () => {
    if (!activeItem || acting) return;
    setActing(true);
    try {
      await forwardIntercept(activeItem.id, draftRaw);
    } catch {
      // 静默处理
    } finally {
      setActing(false);
      refresh();
    }
  };

  const handleDrop = async () => {
    if (!activeItem || acting) return;
    setActing(true);
    try {
      await dropIntercept(activeItem.id);
    } catch {
      // 静默处理
    } finally {
      setActing(false);
      refresh();
    }
  };

  // 空态提示文案（按当前状态给出可操作指引）
  const emptyMessage = !proxyRunning
    ? "代理未启动，先点击右上角「启动代理」，拦截器随代理一起生效"
    : !status?.enabled
    ? "拦截未开启，打开上方「拦截开启」开关开始拦截流量"
    : !status?.intercept_requests && !status?.intercept_responses
    ? "未勾选任何拦截层，打开「拦截请求」或「拦截响应」即可"
    : "暂无被拦截的流量，代理捕获到新请求后会自动暂停在这里";

  return (
    <div className="flex flex-col h-full">
      {/* ===== view-header（与流量列表同构） ===== */}
      <div className="view-header">
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span className="view-title">拦截器</span>
          <span className="view-info-tag">
            {status?.pending_count ? `队列 ${status.pending_count} 条` : "空闲"}
          </span>
        </div>
        <div className="view-actions" style={{ gap: 6, alignItems: "center" }}>
          <span className={`proxy-status-badge ${proxyRunning ? "running" : "stopped"}`}>
            <Wifi size={11} />
            {proxyRunning ? `${proxyHost}:${proxyPort}` : "代理未启动"}
          </span>
          <button
            className="btn btn-sm"
            onClick={handleToggleProxy}
            disabled={proxyToggling}
            style={{
              backgroundColor: proxyRunning ? "var(--accent-red)" : "var(--accent-green)",
              color: "#fff",
              border: "none",
            }}
          >
            {proxyToggling ? <Spinner size={12} /> : <Power size={12} />}
            {proxyRunning ? "停止" : "启动"}
          </button>
          {proxyError && (
            <span style={{ fontSize: 11, color: "var(--accent-red)", maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={proxyError}>
              {proxyError}
            </span>
          )}
          <div className="btn-group-separator" />
          <button className="btn btn-sm" onClick={refresh} disabled={!status}>
            <RefreshCw size={12} />
            刷新
          </button>
        </div>
      </div>

      {/* ===== 拦截工具栏（开关组 + 队列计数） ===== */}
      <div className="filter-bar">
        <div className="filter-group">
          <button
            className={`toggle-btn ${status?.enabled ? "active" : ""}`}
            onClick={toggleEnabled}
            title="总开关：开启后代理捕获的流量将按子开关规则暂停"
            style={status?.enabled ? { backgroundColor: "var(--accent-green)", borderColor: "transparent" } : undefined}
          >
            <Zap size={12} />
            {status?.enabled ? "拦截开启" : "拦截关闭"}
          </button>
          <div className="filter-divider" />
          <button
            className={`toggle-btn ${status?.intercept_requests ? "active active-blue" : ""}`}
            onClick={toggleRequests}
            title="暂停并编辑发出的请求"
          >
            <ArrowUpToLine size={12} />
            拦截请求
          </button>
          <button
            className={`toggle-btn ${status?.intercept_responses ? "active active-orange" : ""}`}
            onClick={toggleResponses}
            title="暂停并编辑返回的响应"
          >
            <ArrowDownToLine size={12} />
            拦截响应
          </button>
        </div>
        <div style={{ flex: 1 }} />
        <div className="filter-group">
          {status?.enabled && !proxyRunning && (
            <span className="view-info-tag" style={{ color: "var(--accent-orange)", borderColor: "rgba(255,174,0,0.3)" }}>
              代理未运行，拦截不会触发
            </span>
          )}
          {pending && pending.total > 0 && (
            <span className="view-info-tag">
              正在处理 {1} / {pending.total}
            </span>
          )}
        </div>
      </div>

      {/* ===== 主体：编辑区 / 空态 ===== */}
      {activeItem ? (
        <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", background: "var(--bg-primary)" }}>
          {/* 包信息条 */}
          <div style={{
            padding: "0 12px", minHeight: 32,
            borderBottom: "1px solid var(--border-default)", background: "var(--bg-secondary)",
            display: "flex", alignItems: "center", gap: 8, fontSize: 11, color: "var(--text-muted)",
          }}>
            <span style={{
              fontSize: 10, padding: "1px 6px", borderRadius: 3, fontWeight: 600,
              backgroundColor: activeItem.kind === "request" ? "rgba(88,166,255,0.12)" : "rgba(188,140,255,0.12)",
              color: activeItem.kind === "request" ? "var(--accent-blue)" : "var(--accent-purple)",
            }}>
              {activeItem.kind === "request" ? "请求拦截" : "响应拦截"}
            </span>
            <MethodBadge method={activeItem.method} />
            <span style={{ fontFamily: "var(--font-mono)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", minWidth: 0, flex: 1 }} title={activeItem.url}>
              {activeItem.url}
            </span>
            {activeItem.kind === "response" && activeItem.status != null && (
              <StatusCode status={activeItem.status} />
            )}
          </div>

          {/* 编辑区（所见即所得） */}
          <div style={{ flex: 1, overflow: "hidden", display: "flex" }}>
            <CodeEditor
              value={draftRaw}
              onChange={setDraftRaw}
              type={activeItem.kind === "request" ? "request" : "response"}
              editable
            />
          </div>

          {/* 底部操作栏 */}
          <div style={{
            padding: "6px 12px", minHeight: 40,
            borderTop: "1px solid var(--border-default)", background: "var(--bg-secondary)",
            display: "flex", alignItems: "center", gap: 8,
          }}>
            <button className="btn btn-primary" onClick={handleForward} disabled={acting} title="放行：将编辑后的包发送出去（未编辑则原样转发）">
              {acting ? <Spinner size={12} /> : <SkipForward size={12} />}
              放行 (Forward)
            </button>
            <button className="btn-outline-danger" onClick={handleDrop} disabled={acting} title="丢弃：丢弃该包，客户端将收不到响应">
              <X size={12} />
              丢弃 (Drop)
            </button>
            <div style={{ flex: 1 }} />
            <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
              编辑后放行即发送修改后的内容 · 不编辑则原样转发
            </span>
          </div>
        </div>
      ) : (
        <EmptyState icon={<Inbox />} message={emptyMessage} />
      )}
    </div>
  );
}
