import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { Shield, Bug, RefreshCw, FileDown, FlaskConical, Repeat, Bot, Swords } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useWebSocket } from "../hooks/useWebSocket";
import {
  listFindings,
  getFinding,
  updateFindingStatus,
  retestFinding,
} from "../api/agent";
import { useProject } from "../context/ProjectContext";
import { API_BASE } from "../api/client";
import type { Finding, FindingDetail, WSEvent, RetestResponse } from "../types";
import { RiskBadge, Spinner, EmptyState } from "../components/Common";
import { CodeEditor } from "../components/CodeEditor";
import { usePacket } from "../context/PacketContext";
import { parseRawRequest } from "../utils/httpParser";

const SEVERITY_ORDER: Record<string, number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
  info: 4,
};

const STATUS_LABELS: Record<string, string> = {
  pending: "待确认",
  confirmed: "已确认",
  false_positive: "误报",
  closed: "已关闭",
};

const STATUS_COLORS: Record<string, string> = {
  pending: "var(--accent-yellow)",
  confirmed: "var(--accent-green)",
  false_positive: "var(--text-muted)",
  closed: "var(--accent-blue)",
};

const RETEST_STATUS_LABELS: Record<string, string> = {
  still_vulnerable: "漏洞仍存在",
  fixed: "已修复",
  uncertain: "无法确定",
  error: "复测失败",
};

const RETEST_STATUS_COLORS: Record<string, string> = {
  still_vulnerable: "var(--accent-red)",
  fixed: "var(--accent-green)",
  uncertain: "var(--accent-yellow)",
  error: "var(--text-muted)",
};

/**
 * 从证据请求原始报文文本解析出 { method, url, headers, body }，
 * 用于转发到重放器/爆破器。
 * 证据格式：
 *   GET /api/users HTTP/1.1
 *   Host: example.com
 *   Content-Type: application/json
 *
 *   {body}
 */
function parseEvidenceRequest(raw: string) {
  const parsed = parseRawRequest(raw, "https");
  return parsed;
}

/** 从证据响应文本解析状态码，如 "HTTP/1.1 200 OK" → 200 */
function parseResponseStatus(raw: string): number | undefined {
  const m = raw.match(/^HTTP\/\d(?:\.\d)?\s+(\d{3})/i);
  return m ? parseInt(m[1], 10) : undefined;
}

/** 从证据响应文本提取响应头（去掉状态行和空行） */
function extractResponseHeaders(raw: string): string {
  const normalized = raw.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const splitIdx = normalized.indexOf("\n\n");
  const headerSection = splitIdx >= 0 ? normalized.substring(0, splitIdx) : normalized;
  const lines = headerSection.split("\n").slice(1); // 去掉状态行
  return lines.join("\n").trim();
}

/** 从证据响应文本提取响应体（空行之后） */
function extractResponseBody(raw: string): string {
  const normalized = raw.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const splitIdx = normalized.indexOf("\n\n");
  return splitIdx >= 0 ? normalized.substring(splitIdx + 2) : "";
}

export function FindingsView() {
  const { currentProjectId } = useProject();
  const navigate = useNavigate();
  const { sendToRepeater, sendToIntruder, sendToAI } = usePacket();
  const [findings, setFindings] = useState<Finding[]>([]);
  const [selectedFinding, setSelectedFinding] = useState<FindingDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [filterStatus, setFilterStatus] = useState<string>("");
  const [detailTab, setDetailTab] = useState<"request" | "response" | "indicator">("request");
  const [retesting, setRetesting] = useState(false);
  const [retestResult, setRetestResult] = useState<RetestResponse | null>(null);

  // 右键上下文菜单
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number } | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  // 解析证据请求 → 用于转发
  const evidenceParsed = useMemo(() => {
    const raw = selectedFinding?.evidence?.request;
    if (!raw) return null;
    const parsed = parseEvidenceRequest(raw);
    if (!parsed || !parsed.url) return null;
    return parsed;
  }, [selectedFinding]);

  // 点击外部 / 滚动时关闭右键菜单
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

  const handleSendToRepeater = () => {
    setContextMenu(null);
    if (!evidenceParsed) return;
    sendToRepeater({
      method: evidenceParsed.method,
      url: evidenceParsed.url,
      headers: evidenceParsed.headers,
      body: evidenceParsed.body,
    });
    navigate("/repeater");
  };

  const handleSendToIntruder = () => {
    setContextMenu(null);
    if (!evidenceParsed) return;
    sendToIntruder({
      method: evidenceParsed.method,
      url: evidenceParsed.url,
      headers: evidenceParsed.headers,
      body: evidenceParsed.body,
    });
    navigate("/intruder");
  };

  const handleSendToAI = () => {
    setContextMenu(null);
    if (!evidenceParsed) return;
    sendToAI({
      method: evidenceParsed.method,
      url: evidenceParsed.url,
      headers: evidenceParsed.headers,
      body: evidenceParsed.body,
      respStatus: selectedFinding?.evidence?.response ? parseResponseStatus(selectedFinding.evidence.response) : undefined,
      respHeaders: extractResponseHeaders(selectedFinding?.evidence?.response || ""),
      respBody: extractResponseBody(selectedFinding?.evidence?.response || ""),
    });
    navigate("/agent");
  };

  const refreshFindings = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listFindings(currentProjectId || undefined, filterStatus || undefined);
      data.sort((a, b) => (SEVERITY_ORDER[a.severity] ?? 99) - (SEVERITY_ORDER[b.severity] ?? 99));
      setFindings(data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, [filterStatus, currentProjectId]);

  useEffect(() => {
    refreshFindings();
  }, [refreshFindings]);

  const handleWsEvent = useCallback(
    (event: WSEvent) => {
      if (event.event === "new_finding" || event.event === "retest_complete") {
        refreshFindings();
      }
    },
    [refreshFindings]
  );

  useWebSocket(handleWsEvent);

  const handleSelectFinding = async (findingId: string) => {
    try {
      const data = await getFinding(findingId);
      setSelectedFinding(data);
      setDetailTab("request");
      setRetestResult(null);
    } catch (e) {
      console.error(e);
    }
  };

  const handleStatusChange = async (findingId: string, status: string) => {
    try {
      await updateFindingStatus(findingId, status);
      await refreshFindings();
      if (selectedFinding?.id === findingId) {
        await handleSelectFinding(findingId);
      }
    } catch (e) {
      console.error(e);
    }
  };

  const handleRetest = async () => {
    if (!selectedFinding) return;
    setRetesting(true);
    setRetestResult(null);
    try {
      const result = await retestFinding(selectedFinding.id);
      setRetestResult(result);
      await refreshFindings();
    } catch (e) {
      console.error(e);
      setRetestResult({
        finding_id: selectedFinding.id,
        retest_status: "error",
        original_status: null,
        new_status: null,
        similarity: 0,
        analysis: "复测请求失败",
        new_flow_id: null,
        error: String(e),
      });
    } finally {
      setRetesting(false);
    }
  };

  const handleExportReport = async (fmt: string) => {
    // window.open 在 Tauri WebView 会被静默拦截，改用保存对话框（Tauri）/ Blob 下载（浏览器）触发导出
    try {
      const url = generateReportUrlSync({ fmt });
      const resp = await fetch(url);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const content = await resp.text();
      const extMap: Record<string, string> = { html: "html", markdown: "md", json: "json" };
      const mimeMap: Record<string, string> = {
        html: "text/html",
        markdown: "text/markdown",
        json: "application/json",
      };
      const ext = extMap[fmt] ?? "txt";

      // Tauri 环境：弹系统保存对话框，用户自选保存位置
      if (typeof window !== "undefined" && ("__TAURI_INTERNALS__" in window || "__TAURI__" in window)) {
        const { save } = await import("@tauri-apps/plugin-dialog");
        const { writeTextFile } = await import("@tauri-apps/plugin-fs");
        const path = await save({
          defaultPath: `hemlock-report.${ext}`,
          filters: [{ name: "Hemlock 报告", extensions: [ext] }],
        });
        if (!path) return; // 用户取消保存
        await writeTextFile(path, content);
        return;
      }

      // 浏览器 / dev 模式：Blob 下载兜底
      const blob = new Blob([content], { type: mimeMap[fmt] ?? "text/plain" });
      const blobUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = blobUrl;
      a.download = `hemlock-report.${ext}`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(blobUrl), 1000);
    } catch (err) {
      console.error("导出报告失败:", err);
    }
  };

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="view-header">
        <div className="view-title">漏洞管理</div>
        <div className="view-actions">
          <select
            className="select"
            value={filterStatus}
            onChange={(e) => setFilterStatus(e.target.value)}
          >
            <option value="">全部状态</option>
            <option value="pending">待确认</option>
            <option value="confirmed">已确认</option>
            <option value="false_positive">误报</option>
            <option value="closed">已关闭</option>
          </select>
          <button
            className="btn btn-sm"
            onClick={() => handleExportReport("html")}
            title="导出 HTML 报告"
          >
            <FileDown size={12} />
            导出报告
          </button>
          <button className="btn btn-sm" onClick={refreshFindings} disabled={loading}>
            {loading ? <Spinner size={12} /> : <RefreshCw size={12} />}
            刷新
          </button>
        </div>
      </div>

      {/* 主体 */}
      <div className="split-pane">
        {/* 左：漏洞列表 */}
        <div className="split-pane-left" style={{ flex: "0 0 50%" }}>
          <div className="scroll-container">
            {findings.length === 0 ? (
              <EmptyState
                icon={<Bug size={36} />}
                message="暂无漏洞记录"
              />
            ) : (
              <table className="data-table">
                <thead>
                  <tr>
                    <th>漏洞类型</th>
                    <th>严重程度</th>
                    <th>标题</th>
                    <th>状态</th>
                    <th>发现时间</th>
                  </tr>
                </thead>
                <tbody>
                  {findings.map((f) => (
                    <tr
                      key={f.id}
                      onClick={() => handleSelectFinding(f.id)}
                      className={selectedFinding?.id === f.id ? "selected" : ""}
                    >
                      <td className="font-mono text-xs">{f.vuln_type}</td>
                      <td><RiskBadge level={f.severity} /></td>
                      <td className="url-cell">{f.title}</td>
                      <td>
                        <span
                          className="badge"
                          style={{
                            color: STATUS_COLORS[f.status] || "var(--text-muted)",
                            background: "var(--bg-active)",
                          }}
                        >
                          {STATUS_LABELS[f.status] || f.status}
                        </span>
                      </td>
                      <td className="time-cell">
                        {f.created_at
                          ? new Date(f.created_at).toLocaleString("zh-CN", {
                              month: "2-digit",
                              day: "2-digit",
                              hour: "2-digit",
                              minute: "2-digit",
                            })
                          : "-"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>

        {/* 右：漏洞详情 + 证据链 */}
        <div className="split-pane-right">
          {selectedFinding ? (
            <div className="flex flex-col h-full">
              {/* 详情头部 */}
              <div className="finding-detail-header">
                <div className="flex items-center gap-2 mb-2">
                  <RiskBadge level={selectedFinding.severity} />
                  <span className="font-mono text-xs text-muted">
                    {selectedFinding.vuln_type}
                  </span>
                  {selectedFinding.evidence?.replayable && (
                    <button
                      className="btn btn-sm"
                      onClick={handleRetest}
                      disabled={retesting}
                      title="重放证据请求，验证漏洞是否已修复"
                    >
                      {retesting ? <Spinner size={12} /> : <FlaskConical size={12} />}
                      {retesting ? "复测中..." : "复测"}
                    </button>
                  )}
                </div>
                <div className="text-sm font-semibold">{selectedFinding.title}</div>
                {selectedFinding.detail && (
                  <div className="text-xs text-secondary mt-2">
                    {selectedFinding.detail}
                  </div>
                )}

                {/* 复测结果 */}
                {retestResult && (
                  <div className="card mt-3" style={{
                    borderLeft: `3px solid ${RETEST_STATUS_COLORS[retestResult.retest_status] || "var(--text-muted)"}`,
                  }}>
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-xs font-semibold">复测结果</span>
                      <span
                        className="badge"
                        style={{
                          color: RETEST_STATUS_COLORS[retestResult.retest_status] || "var(--text-muted)",
                          background: "var(--bg-active)",
                        }}
                      >
                        {RETEST_STATUS_LABELS[retestResult.retest_status] || retestResult.retest_status}
                      </span>
                    </div>
                    <div className="text-xs text-secondary">
                      {retestResult.analysis}
                    </div>
                    {retestResult.error && (
                      <div className="text-xs text-error mt-1">{retestResult.error}</div>
                    )}
                    <div className="text-xs text-muted mt-1">
                      原始状态码: {retestResult.original_status ?? "N/A"} → 新状态码: {retestResult.new_status ?? "N/A"}
                      {" | 相似度: "}{retestResult.similarity.toFixed(1)}%
                    </div>
                  </div>
                )}

                <div className="flex items-center gap-2 mt-2">
                  <span className="text-xs text-muted">状态：</span>
                  <select
                    className="select"
                    style={{ width: "auto" }}
                    value={selectedFinding.status}
                    onChange={(e) =>
                      handleStatusChange(selectedFinding.id, e.target.value)
                    }
                  >
                    <option value="pending">待确认</option>
                    <option value="confirmed">已确认</option>
                    <option value="false_positive">误报</option>
                    <option value="closed">已关闭</option>
                  </select>
                </div>
              </div>

              {/* 证据链 */}
              {selectedFinding.evidence ? (
                <div
                  className="flex flex-col flex-1 overflow-hidden"
                  onContextMenu={(e) => {
                    e.preventDefault();
                    setContextMenu({ x: e.clientX, y: e.clientY });
                  }}
                >
                  {/* 操作栏：发送到重放器 / 爆破器 / AI（与 FlowDetailPanel 保持一致） */}
                  {evidenceParsed && (
                    <div
                      style={{
                        flexShrink: 0,
                        display: "flex",
                        alignItems: "center",
                        gap: 6,
                        padding: "4px 12px",
                        borderBottom: "1px solid var(--border-default)",
                        background: "var(--bg-secondary)",
                        fontSize: 11,
                      }}
                    >
                      <span
                        className="font-mono text-xs"
                        style={{
                          color: "var(--text-link)",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                          maxWidth: 300,
                        }}
                        title={evidenceParsed.url}
                      >
                        {evidenceParsed.url}
                      </span>
                      <span style={{ flex: 1 }} />
                      <button
                        className="btn btn-sm"
                        onClick={handleSendToRepeater}
                        title="发送到重放器"
                        style={{ padding: "2px 6px", fontSize: 11 }}
                      >
                        <Repeat size={12} /> 重放器
                      </button>
                      <button
                        className="btn btn-sm"
                        onClick={handleSendToIntruder}
                        title="发送到爆破器"
                        style={{ padding: "2px 6px", fontSize: 11 }}
                      >
                        <Swords size={12} /> 爆破器
                      </button>
                      <button
                        className="btn btn-sm"
                        onClick={handleSendToAI}
                        title="发送到 AI 助手"
                        style={{ padding: "2px 6px", fontSize: 11 }}
                      >
                        <Bot size={12} /> AI
                      </button>
                    </div>
                  )}

                  <div className="tabs">
                    <div
                      className={`tab ${detailTab === "request" ? "active" : ""}`}
                      onClick={() => setDetailTab("request")}
                    >
                      原始请求
                    </div>
                    <div
                      className={`tab ${detailTab === "response" ? "active" : ""}`}
                      onClick={() => setDetailTab("response")}
                    >
                      响应数据
                    </div>
                    <div
                      className={`tab ${detailTab === "indicator" ? "active" : ""}`}
                      onClick={() => setDetailTab("indicator")}
                    >
                      判定指标
                    </div>
                  </div>

                  {detailTab === "request" && (
                    <div style={{ flex: 1, overflow: "hidden", minHeight: 0, display: "flex" }}>
                      <CodeEditor
                        value={selectedFinding.evidence.request || ""}
                        type="request"
                      />
                    </div>
                  )}
                  {detailTab === "response" && (
                    <div style={{ flex: 1, overflow: "hidden", minHeight: 0, display: "flex" }}>
                      <CodeEditor
                        value={selectedFinding.evidence.response || ""}
                        type="response"
                      />
                    </div>
                  )}
                  {detailTab === "indicator" && (
                    <div className="scroll-container flex-1">
                      <div className="p-4">
                        <div className="text-xs text-muted mb-2">漏洞判定指标</div>
                        <div className="card text-sm">
                          {selectedFinding.evidence.indicator || "(未提供)"}
                        </div>
                        {selectedFinding.evidence.ai_reasoning && (
                          <>
                            <div className="text-xs text-muted mt-4 mb-2">
                              AI 推理过程
                            </div>
                            <div className="card text-sm">
                              {selectedFinding.evidence.ai_reasoning}
                            </div>
                          </>
                        )}
                        <div className="text-xs text-muted mt-4">
                          可复现: {selectedFinding.evidence.replayable ? "是" : "否"}
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              ) : (
                <EmptyState
                  icon={<Shield size={36} />}
                  message="该漏洞未关联证据链"
                />
              )}
            </div>
          ) : (
            <EmptyState
              icon={<Shield size={36} />}
              message="选择漏洞查看证据链详情"
            />
          )}
        </div>
      </div>

      {/* 右键上下文菜单 */}
      {contextMenu && evidenceParsed && (
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

// Helper: generate report URL synchronously
function generateReportUrlSync(opts: { fmt: string; projectId?: string; taskId?: string }): string {
  const params = new URLSearchParams();
  if (opts.projectId) params.set("project_id", opts.projectId);
  if (opts.taskId) params.set("task_id", opts.taskId);
  params.set("fmt", opts.fmt);
  // API_BASE 已处理 Tauri 生产模式 vs 开发模式
  return `${API_BASE}/reports?${params.toString()}`;
}
