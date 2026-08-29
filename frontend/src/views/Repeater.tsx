import { useState, useEffect, useRef, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { Send, Bot, Swords, Repeat, AlignLeft, SplitSquareHorizontal, X, Plus, Inbox } from "lucide-react";
import { sendRequest } from "../api/request";
import { useProject } from "../context/ProjectContext";
import { usePacket } from "../context/PacketContext";
import type { SendRequestResponse, RepeaterTab } from "../types";
import { MethodBadge, StatusCode, Spinner, EmptyState } from "../components/Common";
import { CodeEditor } from "../components/CodeEditor";
import { buildRawRequest, parseRawRequest, extractScheme } from "../utils/httpParser";
import { formatBody, extractContentType } from "../utils/bodyFormatter";

// 使用时间戳作为 ID 基数，避免跨会话冲突
let tabIdCounter = Date.now();

function createRepeaterTab(rawRequest = ""): RepeaterTab {
  return {
    id: `rep-${tabIdCounter++}`,
    rawRequest,
    scheme: "https",
    response: null,
    error: null,
    respMode: "raw",
  };
}

/** 构建响应文本（自动格式化 body） */
function buildResponseContent(response: SendRequestResponse): string {
  const statusLine = `HTTP/1.1 ${response.status}`;
  const headers = (response.headers || "").replace(/\n+$/, "");
  const rawBody = response.body || "";
  if (rawBody) {
    const ct = extractContentType(headers);
    const body = formatBody(rawBody, ct);
    return `${statusLine}\n${headers}\n\n${body}`;
  }
  return `${statusLine}\n${headers}`;
}

export function Repeater() {
  const navigate = useNavigate();
  const { currentProjectId } = useProject();
  const { pendingPacket, target, consume, sendToAI, sendToIntruder, sendToRepeater } = usePacket();

  const [tabs, setTabs] = useState<RepeaterTab[]>([]);
  const [activeTabId, setActiveTabId] = useState<string>("");
  const [sending, setSending] = useState(false);

  const [contextMenu, setContextMenu] = useState<{ x: number; y: number } | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  // 防止重复消费同一个 packet（React Strict Mode / 快速重渲染）
  const consumingRef = useRef(false);
  const loadedProjectRef = useRef<string | null>(null);

  const activeTab = tabs.find((t) => t.id === activeTabId) || null;

  const updateActiveTab = useCallback((patch: Partial<RepeaterTab>) => {
    setTabs((prev) => prev.map((t) => (t.id === activeTabId ? { ...t, ...patch } : t)));
  }, [activeTabId]);

  // ===== 项目切换时加载持久化标签 =====
  useEffect(() => {
    if (currentProjectId === loadedProjectRef.current) return;
    loadedProjectRef.current = currentProjectId;

    if (!currentProjectId) {
      setTabs([]);
      setActiveTabId("");
      return;
    }
    try {
      const key = `hemlock_repeater_${currentProjectId}`;
      const saved = localStorage.getItem(key);
      if (saved) {
        const parsed = JSON.parse(saved) as RepeaterTab[];
        if (Array.isArray(parsed) && parsed.length > 0) {
          setTabs(parsed);
          setActiveTabId(parsed[0].id);
          return;
        }
      }
    } catch {
      // localStorage 异常，忽略
    }
    setTabs([]);
    setActiveTabId("");
  }, [currentProjectId]);

  // ===== 标签变更时持久化（渐进式：完整→去响应体→仅请求） =====
  useEffect(() => {
    if (!currentProjectId) return;
    const key = `hemlock_repeater_${currentProjectId}`;
    const tabsToSave = tabs.filter((t) => t.rawRequest.trim());
    if (tabsToSave.length === 0) {
      try { localStorage.removeItem(key); } catch {}
      return;
    }
    // 第一轮：尝试保存完整数据
    try {
      localStorage.setItem(key, JSON.stringify(tabsToSave));
      return;
    } catch {
      // 继续 fallback
    }
    // 第二轮：去掉响应体（响应体可能很大）
    try {
      const stripped = tabsToSave.map((t) => ({ ...t, response: null, error: null }));
      localStorage.setItem(key, JSON.stringify(stripped));
      return;
    } catch {
      // 继续 fallback
    }
    // 第三轮：只保留请求文本
    try {
      const minimal = tabsToSave.map((t) => ({
        id: t.id,
        rawRequest: t.rawRequest,
        scheme: t.scheme,
        respMode: t.respMode,
        response: null,
        error: null,
      }));
      localStorage.setItem(key, JSON.stringify(minimal));
    } catch {
      // localStorage 确实满了，无法保存
    }
  }, [tabs, currentProjectId]);

  const handleAddTab = useCallback(() => {
    const newTab = createRepeaterTab();
    setTabs((prev) => [...prev, newTab]);
    setActiveTabId(newTab.id);
  }, []);

  const handleCloseTab = useCallback((e: React.MouseEvent, tabId: string) => {
    e.stopPropagation();
    setTabs((prev) => {
      if (prev.length <= 1) {
        return [];
      }
      const filtered = prev.filter((t) => t.id !== tabId);
      if (activeTabId === tabId) {
        setActiveTabId(filtered[filtered.length - 1].id);
      }
      return filtered;
    });
  }, [activeTabId]);

  // ===== 接收转发的数据包（带防重复守卫） =====
  useEffect(() => {
    if (!pendingPacket || target !== "repeater" || consumingRef.current) return;
    consumingRef.current = true;

    const newTab: RepeaterTab = {
      id: `rep-${tabIdCounter++}`,
      rawRequest: buildRawRequest(
        pendingPacket.method || "GET",
        pendingPacket.url || "",
        pendingPacket.headers || "",
        pendingPacket.body || ""
      ),
      scheme: pendingPacket.url ? extractScheme(pendingPacket.url) : "https",
      response: null,
      error: null,
      respMode: "raw",
    };
    setTabs((prev) => [...prev, newTab]);
    setActiveTabId(newTab.id);
    consume();

    // 下一帧重置守卫
    requestAnimationFrame(() => {
      consumingRef.current = false;
    });
  }, [pendingPacket, target, consume]);

  // ===== 右键菜单外部点击关闭 =====
  useEffect(() => {
    if (!contextMenu) return;
    const handleClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setContextMenu(null);
    };
    const handleScroll = () => setContextMenu(null);
    document.addEventListener("mousedown", handleClick);
    document.addEventListener("scroll", handleScroll, true);
    return () => {
      document.removeEventListener("mousedown", handleClick);
      document.removeEventListener("scroll", handleScroll, true);
    };
  }, [contextMenu]);

  const handleSend = async () => {
    if (!activeTab) return;
    const parsed = parseRawRequest(activeTab.rawRequest, activeTab.scheme);
    if (!parsed || !parsed.url) {
      updateActiveTab({ error: "无法解析请求" });
      return;
    }
    setSending(true);
    updateActiveTab({ error: null });
    try {
      const result = await sendRequest({
        method: parsed.method,
        url: parsed.url,
        headers: parsed.headers,
        body: parsed.body,
        project_id: currentProjectId,
        follow_redirects: activeTab.followRedirects !== false,
      });
      updateActiveTab({ response: result });
    } catch (e) {
      updateActiveTab({ error: e instanceof Error ? e.message : "发送失败" });
    } finally {
      setSending(false);
    }
  };

  const currentMethod = activeTab
    ? parseRawRequest(activeTab.rawRequest, activeTab.scheme)?.method || "GET"
    : "GET";

  const handleSendToAI = useCallback(() => {
    setContextMenu(null);
    if (!activeTab) return;
    const parsed = parseRawRequest(activeTab.rawRequest, activeTab.scheme);
    if (!parsed) return;
    sendToAI({
      method: parsed.method,
      url: parsed.url,
      headers: parsed.headers,
      body: parsed.body,
      respStatus: activeTab.response?.status ?? undefined,
      respHeaders: activeTab.response?.headers || "",
      respBody: activeTab.response?.body || "",
    });
    navigate("/agent");
  }, [activeTab, sendToAI, navigate]);

  const handleSendToIntruder = useCallback(() => {
    setContextMenu(null);
    if (!activeTab) return;
    const parsed = parseRawRequest(activeTab.rawRequest, activeTab.scheme);
    if (!parsed) return;
    sendToIntruder({
      method: parsed.method,
      url: parsed.url,
      headers: parsed.headers,
      body: parsed.body,
    });
    navigate("/intruder");
  }, [activeTab, sendToIntruder, navigate]);

  const handleSendToRepeater = useCallback(() => {
    setContextMenu(null);
    if (!activeTab) return;
    const parsed = parseRawRequest(activeTab.rawRequest, activeTab.scheme);
    if (!parsed) return;
    // 发送到重放器（自身）— 通过 PacketContext 统一通道，会创建新标签页
    sendToRepeater({
      method: parsed.method,
      url: parsed.url,
      headers: parsed.headers,
      body: parsed.body,
      respStatus: activeTab.response?.status ?? undefined,
      respHeaders: activeTab.response?.headers || "",
      respBody: activeTab.response?.body || "",
    });
  }, [activeTab, sendToRepeater]);

  const getTabTitle = (tab: RepeaterTab, idx: number) => {
    const parsed = parseRawRequest(tab.rawRequest, tab.scheme);
    if (parsed) {
      const path = parsed.url ? new URL(parsed.url).pathname : "/";
      return `#${idx + 1} ${parsed.method} ${path.length > 20 ? path.substring(0, 20) + "..." : path}`;
    }
    return `#${idx + 1}`;
  };

  return (
    <div className="flex flex-col h-full">
      <div className="view-header">
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span className="view-title">重放器</span>
          {activeTab && (
            <div style={{ display: "flex", gap: 0, borderRadius: 4, overflow: "hidden", border: "1px solid var(--border-default)" }}>
              <button
                style={{
                  padding: "2px 10px", fontSize: 11,
                  background: activeTab.scheme === "http" ? "var(--accent-blue)" : "var(--bg-tertiary)",
                  color: activeTab.scheme === "http" ? "#fff" : "var(--text-secondary)",
                  border: "none", cursor: "pointer",
                }}
                onClick={() => updateActiveTab({ scheme: "http" })}
              >HTTP</button>
              <button
                style={{
                  padding: "2px 10px", fontSize: 11,
                  background: activeTab.scheme === "https" ? "var(--accent-blue)" : "var(--bg-tertiary)",
                  color: activeTab.scheme === "https" ? "#fff" : "var(--text-secondary)",
                  border: "none", cursor: "pointer",
                }}
                onClick={() => updateActiveTab({ scheme: "https" })}
              >HTTPS</button>
            </div>
          )}
          {activeTab && (
            <button
              className="btn btn-sm"
              style={{
                fontSize: 11,
                padding: "2px 10px",
                background: activeTab.followRedirects !== false ? "var(--accent-blue)" : "var(--bg-tertiary)",
                color: activeTab.followRedirects !== false ? "#fff" : "var(--text-secondary)",
              }}
              title="开启时自动跟随 3xx 重定向并显示最终响应；关闭时保留原始 30x 响应（跨域重定向会丢失 Authorization/Cookie，导致目标返回 401，关闭后可看到真实跳转过程）"
              onClick={() =>
                updateActiveTab({ followRedirects: activeTab.followRedirects === false })
              }
            >
              跟随重定向
            </button>
          )}
        </div>
      </div>

      {/* 多标签栏 */}
      <div className="multi-tab-bar">
        {tabs.map((tab, idx) => (
          <div key={tab.id} className={`multi-tab ${tab.id === activeTabId ? "active" : ""}`} onClick={() => setActiveTabId(tab.id)}>
            <span className="multi-tab-title">{getTabTitle(tab, idx)}</span>
            {tabs.length > 1 && (
              <button className="multi-tab-close" onClick={(e) => handleCloseTab(e, tab.id)}>
                <X size={11} />
              </button>
            )}
          </div>
        ))}
        <button className="multi-tab-add" onClick={handleAddTab} title="新建标签页"><Plus size={13} /></button>
      </div>

      {/* 主体：左右等宽镜像面板 */}
      {activeTab ? (
        <div style={{ flex: 1, display: "flex", overflow: "hidden", gap: 1, background: "var(--border-default)" }}>
          {/* ===== 左侧：请求编辑器 ===== */}
          <div
            style={{
              flex: "1 1 50%",
              minWidth: 0,
              display: "flex",
              flexDirection: "column",
              overflow: "hidden",
              background: "var(--bg-primary)",
            }}
            onContextMenu={(e) => { e.preventDefault(); setContextMenu({ x: e.clientX, y: e.clientY }); }}
          >
            {/* 左侧工具栏 */}
            <div style={{
              padding: "0 12px", minHeight: 32,
              borderBottom: "1px solid var(--border-default)", background: "var(--bg-secondary)",
              display: "flex", alignItems: "center", gap: 8, fontSize: 11, color: "var(--text-muted)",
            }}>
              <MethodBadge method={currentMethod} />
              <span>请求</span>
              <div style={{ flex: 1 }} />
              <button className="btn btn-sm" onClick={handleSendToRepeater} title="发送到重放器（新标签页）">
                <Repeat size={11} /> 重放器
              </button>
              <button className="btn btn-sm" onClick={handleSendToIntruder} title="发送到爆破器">
                <Swords size={11} /> 爆破器
              </button>
              <button className="btn btn-sm" onClick={handleSendToAI} title="发送到 AI 助手">
                <Bot size={11} /> AI
              </button>
              <button className="btn btn-sm btn-primary" onClick={handleSend} disabled={sending || !activeTab.rawRequest.trim()}>
                {sending ? <Spinner size={11} /> : <Send size={11} />}
                发送
              </button>
            </div>
            {/* 请求编辑区 */}
            <div style={{ flex: 1, overflow: "hidden", display: "flex" }}>
              <CodeEditor
                value={activeTab.rawRequest}
                onChange={(val) => updateActiveTab({ rawRequest: val })}
                type="request"
                editable
              />
            </div>
          </div>

          {/* ===== 右侧：响应显示 ===== */}
          <div
            style={{
              flex: "1 1 50%",
              minWidth: 0,
              display: "flex",
              flexDirection: "column",
              overflow: "hidden",
              background: "var(--bg-primary)",
            }}
          >
            {activeTab.error && (
              <div style={{ margin: "8px 8px 0", padding: 12, color: "var(--accent-red)", borderRadius: 4, border: "1px solid var(--accent-red)", background: "rgba(248,81,73,0.08)" }}>
                {activeTab.error}
              </div>
            )}

            {/* 右侧工具栏 */}
            <div style={{
              padding: "0 12px", minHeight: 32,
              borderBottom: "1px solid var(--border-default)", background: "var(--bg-secondary)",
              display: "flex", alignItems: "center", gap: 12,
            }}>
              {activeTab.response ? (
                <>
                  <StatusCode status={activeTab.response.status} />
                  {activeTab.response.duration_ms != null && (
                    <span
                      style={{
                        fontSize: 11,
                        fontFamily: "var(--font-mono)",
                        color: activeTab.response.duration_ms >= 5000
                          ? "var(--accent-red)"
                          : activeTab.response.duration_ms >= 1000
                          ? "var(--accent-orange)"
                          : "var(--text-muted)",
                      }}
                      title="请求-响应耗时"
                    >
                      {activeTab.response.duration_ms < 1000
                        ? `${Math.round(activeTab.response.duration_ms)}ms`
                        : `${(activeTab.response.duration_ms / 1000).toFixed(2)}s`}
                    </span>
                  )}
                </>
              ) : (
                <span style={{ fontSize: 11, color: "var(--text-muted)" }}>响应</span>
              )}
              <div style={{ flex: 1 }} />
              {activeTab.response && (
                <div className="packet-mode-toggle">
                  <button className={`packet-mode-btn ${activeTab.respMode === "raw" ? "active" : ""}`} onClick={() => updateActiveTab({ respMode: "raw" })}>
                    <AlignLeft size={12} /> 原始
                  </button>
                  <button className={`packet-mode-btn ${activeTab.respMode === "split" ? "active" : ""}`} onClick={() => updateActiveTab({ respMode: "split" })}>
                    <SplitSquareHorizontal size={12} /> 分离
                  </button>
                </div>
              )}
            </div>

            {/* 响应显示区 */}
            <div style={{ flex: 1, overflow: "hidden", display: "flex", flexDirection: "column" }}>
              {!activeTab.response && !activeTab.error && (
                <div style={{ flex: 1, border: "1px solid var(--border-default)", borderRadius: 4 }} />
              )}

              {activeTab.response && activeTab.respMode === "raw" && (
                <CodeEditor
                  value={buildResponseContent(activeTab.response)}
                  type="response"
                />
              )}

              {activeTab.response && activeTab.respMode === "split" && (
                <>
                  <div style={{ flex: "1 1 50%", minHeight: 0, display: "flex" }}>
                    <CodeEditor
                      value={`HTTP/1.1 ${activeTab.response.status}\n${(activeTab.response.headers || "").replace(/\n+$/, "")}`}
                      type="response"
                    />
                  </div>
                  <div style={{ flex: "1 1 50%", minHeight: 0, display: "flex" }}>
                    <CodeEditor
                      value={formatBody(activeTab.response.body || "", extractContentType(activeTab.response.headers || ""))}
                      type="response"
                    />
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      ) : (
        <EmptyState
          icon={<Inbox />}
          message="点击 + 新建标签页，或从流量列表右键发送数据包到重放器"
        />
      )}

      {/* 右键上下文菜单 */}
      {contextMenu && (
        <div ref={menuRef} className="context-menu" style={{ position: "fixed", top: contextMenu.y, left: contextMenu.x, zIndex: 1000 }}>
          <div className="context-menu-item" onClick={handleSendToRepeater}>
            <Repeat size={13} /> 发送到重放器
          </div>
          <div className="context-menu-item" onClick={handleSendToIntruder}>
            <Swords size={13} /> 发送到爆破器
          </div>
          <div className="context-menu-item" onClick={handleSendToAI}>
            <Bot size={13} /> 发送到 AI 助手
          </div>
        </div>
      )}
    </div>
  );
}
