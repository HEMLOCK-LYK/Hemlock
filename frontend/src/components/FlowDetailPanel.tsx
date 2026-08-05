import { useState, useMemo, useCallback, useRef, useEffect } from "react";
import { Repeat, Bot, Swords, Columns2, Rows2, Copy, Check } from "lucide-react";
import type { Flow } from "../types";
import { MethodBadge, StatusCode, SourceBadge } from "./Common";
import { CodeEditor } from "./CodeEditor";
import { SplitPane } from "./SplitPane";
import { buildRequestContent, buildResponseContent } from "../utils/flowContent";
import { usePacket } from "../context/PacketContext";
import { useNavigate } from "react-router-dom";

interface FlowDetailPanelProps {
  flow: Flow;
  /** 是否显示操作栏（发送到 Repeater/Intruder/AI），默认 true */
  showActions?: boolean;
  /** 布局方向持久化 key 前缀 */
  storageKeyPrefix?: string;
}

/**
 * FlowDetailPanel — 数据包详情面板
 *
 * 可在底部面板或浮动窗口中复用。
 * - 请求/响应左右分栏（可切换上下分栏）
 * - 分隔条可拖拽调节
 * - 操作栏：发送到 Repeater / Intruder / AI
 */
export function FlowDetailPanel({
  flow,
  showActions = true,
  storageKeyPrefix = "flowlist",
}: FlowDetailPanelProps) {
  const navigate = useNavigate();
  const { sendToRepeater, sendToIntruder, sendToAI } = usePacket();

  // 布局方向：horizontal=左右分栏, vertical=上下分栏
  const [layout, setLayout] = useState<"horizontal" | "vertical">(() => {
    try {
      const saved = localStorage.getItem(`${storageKeyPrefix}_layout`);
      if (saved === "vertical" || saved === "horizontal") return saved;
    } catch { /* ignore */ }
    return "horizontal";
  });

  const [copied, setCopied] = useState(false);

  // 右键上下文菜单
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number } | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);

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

  const reqContent = useMemo(() => buildRequestContent(flow), [flow]);
  const respContent = useMemo(() => buildResponseContent(flow), [flow]);

  const toggleLayout = useCallback(() => {
    setLayout((prev) => {
      const next = prev === "horizontal" ? "vertical" : "horizontal";
      try { localStorage.setItem(`${storageKeyPrefix}_layout`, next); } catch { /* ignore */ }
      return next;
    });
  }, [storageKeyPrefix]);

  const handleCopy = useCallback(() => {
    const fullText = `=== Request ===\n${reqContent}\n\n=== Response ===\n${respContent}`;
    navigator.clipboard.writeText(fullText).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }, [reqContent, respContent]);

  const handleSendToRepeater = useCallback(() => {
    sendToRepeater({
      method: flow.method,
      url: flow.url,
      headers: flow.req_headers || "",
      body: flow.req_body || "",
    });
    navigate("/repeater");
  }, [flow, sendToRepeater, navigate]);

  const handleSendToIntruder = useCallback(() => {
    sendToIntruder({
      method: flow.method,
      url: flow.url,
      headers: flow.req_headers || "",
      body: flow.req_body || "",
    });
    navigate("/intruder");
  }, [flow, sendToIntruder, navigate]);

  const handleSendToAI = useCallback(() => {
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
  }, [flow, sendToAI, navigate]);

  return (
    <div
      style={{ display: "flex", flexDirection: "column", flex: 1, width: "100%", height: "100%", overflow: "hidden" }}
      onContextMenu={(e) => {
        e.preventDefault();
        setContextMenu({ x: e.clientX, y: e.clientY });
      }}
    >
      {/* 操作栏 */}
      {showActions && (
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
          {/* URL 信息 */}
          <span
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: 11,
              color: "var(--text-link)",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              maxWidth: 400,
            }}
            title={flow.url}
          >
            {flow.url}
          </span>
          <SourceBadge source={flow.source} />
          {flow.duration_ms != null && (
            <span
              style={{
                fontSize: 10,
                fontFamily: "var(--font-mono)",
                color: flow.duration_ms >= 5000
                  ? "var(--accent-red)"
                  : flow.duration_ms >= 1000
                  ? "var(--accent-orange)"
                  : "var(--text-muted)",
              }}
            >
              {flow.duration_ms < 1000
                ? `${Math.round(flow.duration_ms)}ms`
                : `${(flow.duration_ms / 1000).toFixed(2)}s`}
            </span>
          )}
          <span style={{ flex: 1 }} />
          {/* 布局切换 */}
          <button
            className="btn btn-sm"
            onClick={toggleLayout}
            title={layout === "horizontal" ? "切换为上下分栏" : "切换为左右分栏"}
            style={{ padding: "2px 6px", fontSize: 11 }}
          >
            {layout === "horizontal" ? <Rows2 size={12} /> : <Columns2 size={12} />}
            {layout === "horizontal" ? "上下" : "左右"}
          </button>
          {/* 复制 */}
          <button
            className="btn btn-sm"
            onClick={handleCopy}
            title="复制完整数据包"
            style={{ padding: "2px 6px", fontSize: 11 }}
          >
            {copied ? <Check size={12} /> : <Copy size={12} />}
            {copied ? "已复制" : "复制"}
          </button>
          <div className="btn-group-separator" />
          {/* 发送到 */}
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

      {/* 请求/响应分栏 — 不带头部栏，直接展示内容 */}
      <div style={{ flex: 1, overflow: "hidden", minHeight: 0 }}>
        <SplitPane
          direction={layout}
          initialSplit={0.5}
          minSize={60}
          storageKey={`${storageKeyPrefix}_split`}
          first={
            <div style={{ flex: 1, display: "flex", minHeight: 0, minWidth: 0 }}>
              <CodeEditor value={reqContent} type="request" />
            </div>
          }
          second={
            <div style={{ flex: 1, display: "flex", minHeight: 0, minWidth: 0 }}>
              <CodeEditor value={respContent} type="response" />
            </div>
          }
        />
      </div>

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
            onClick={() => { handleSendToRepeater(); setContextMenu(null); }}
          >
            <Repeat size={13} />
            发送到重放器
          </div>
          <div
            className="context-menu-item"
            onClick={() => { handleSendToIntruder(); setContextMenu(null); }}
          >
            <Swords size={13} />
            发送到爆破器
          </div>
          <div
            className="context-menu-item"
            onClick={() => { handleSendToAI(); setContextMenu(null); }}
          >
            <Bot size={13} />
            发送到 AI 助手
          </div>
        </div>
      )}
    </div>
  );
}

export default FlowDetailPanel;
