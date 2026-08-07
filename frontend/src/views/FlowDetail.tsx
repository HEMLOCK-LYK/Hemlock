import { useState, useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, Repeat, Bot, Repeat2, Swords } from "lucide-react";
import { getFlow, replayFlow } from "../api/flows";
import { analyzeFlow } from "../api/ai";
import { FLOWTAG_COLORS } from "../api/flowtag";
import { usePacket } from "../context/PacketContext";
import type { Flow, AnalyzeResponse } from "../types";
import {
  MethodBadge,
  StatusCode,
  SourceBadge,
  RiskBadge,
  EmptyState,
  Spinner,
} from "../components/Common";
import { FlowDetailPanel } from "../components/FlowDetailPanel";

export function FlowDetail() {
  const { flowId } = useParams<{ flowId: string }>();
  const navigate = useNavigate();
  const { sendToRepeater, sendToIntruder, sendToAI } = usePacket();
  const [flow, setFlow] = useState<Flow | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [replaying, setReplaying] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [analysis, setAnalysis] = useState<AnalyzeResponse | null>(null);
  const [showAnalysis, setShowAnalysis] = useState(false);

  useEffect(() => {
    if (!flowId) return;
    setLoading(true);
    getFlow(flowId)
      .then((data) => {
        setFlow(data);
        setError(null);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [flowId]);

  const handleReplay = async () => {
    if (!flow) return;
    setReplaying(true);
    try {
      const result = await replayFlow(flow.id);
      navigate(`/flows/${result.flow_id}`);
    } catch (e) {
      alert(`重放失败: ${e instanceof Error ? e.message : e}`);
    } finally {
      setReplaying(false);
    }
  };

  const handleAnalyze = async () => {
    if (!flow) return;
    setAnalyzing(true);
    setShowAnalysis(true);
    try {
      const result = await analyzeFlow({ flow_id: flow.id });
      setAnalysis(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "分析失败");
    } finally {
      setAnalyzing(false);
    }
  };

  const handleSendToRepeater = () => {
    if (!flow) return;
    sendToRepeater({
      method: flow.method,
      url: flow.url,
      headers: flow.req_headers || "",
      body: flow.req_body || "",
    });
    navigate("/repeater");
  };

  const handleSendToIntruder = () => {
    if (!flow) return;
    sendToIntruder({
      method: flow.method,
      url: flow.url,
      headers: flow.req_headers || "",
      body: flow.req_body || "",
    });
    navigate("/intruder");
  };

  const handleSendToAI = () => {
    if (!flow) return;
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
  };

  if (loading) {
    return (
      <div className="flex flex-col h-full">
        <div className="view-header">
          <span className="view-title">流量详情</span>
        </div>
        <EmptyState icon={<Spinner size={24} />} message="加载中..." />
      </div>
    );
  }

  if (error || !flow) {
    return (
      <div className="flex flex-col h-full">
        <div className="view-header">
          <button className="btn btn-sm" onClick={() => navigate("/flows")}>
            <ArrowLeft size={12} /> 返回
          </button>
        </div>
        <EmptyState message={error || "流量不存在"} />
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="view-header">
        <div style={{ display: "flex", alignItems: "center", gap: 12, flex: 1, minWidth: 0 }}>
          <button className="btn btn-sm" onClick={() => navigate("/flows")}>
            <ArrowLeft size={12} /> 返回
          </button>
          {flow.flow_number != null && (
            <span
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 12,
                fontWeight: 700,
                color: "var(--accent-blue)",
                background: "rgba(88,166,255,0.12)",
                padding: "2px 8px",
                borderRadius: 4,
              }}
            >
              #{flow.flow_number}
            </span>
          )}
          <MethodBadge method={flow.method} />
          <span
            className="font-mono text-sm"
            style={{ color: "var(--text-link)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
            title={flow.url}
          >
            {flow.url}
          </span>
          <StatusCode status={flow.resp_status} />
          <SourceBadge source={flow.source} />
          {flow.mime_type && (
            <span style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
              {flow.mime_type}
            </span>
          )}
          {flow.resp_length != null && flow.resp_length > 0 && (
            <span style={{ fontSize: 11, color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
              {flow.resp_length < 1024 ? flow.resp_length : `${(flow.resp_length / 1024).toFixed(1)}K`}
            </span>
          )}
          {flow.duration_ms != null && (
            <span
              style={{
                fontSize: 11,
                fontFamily: "var(--font-mono)",
                color: flow.duration_ms >= 5000
                  ? "var(--accent-red)"
                  : flow.duration_ms >= 1000
                  ? "var(--accent-orange)"
                  : "var(--text-muted)",
              }}
              title="请求-响应耗时"
            >
              {flow.duration_ms < 1000
                ? `${Math.round(flow.duration_ms)}ms`
                : `${(flow.duration_ms / 1000).toFixed(2)}s`}
            </span>
          )}
          {/* FlowTag 标签 */}
          {flow.tags && flow.tags.split(",").filter(Boolean).map((tag, idx) => {
            const colorName = flow.tag_colors?.split(",")[idx] || "gray";
            const hex = FLOWTAG_COLORS[colorName] || FLOWTAG_COLORS.gray;
            return (
              <span
                key={idx}
                className="flowtag-tag-pill"
                style={{ backgroundColor: hex }}
                title={tag}
              >
                {tag}
              </span>
            );
          })}
        </div>
        <div className="view-actions">
          <button
            className="btn btn-sm"
            onClick={handleSendToRepeater}
            title="发送到重放器"
          >
            <Repeat2 size={12} />
            重放器
          </button>
          <button
            className="btn btn-sm"
            onClick={handleSendToIntruder}
            title="发送到爆破器"
          >
            <Swords size={12} />
            爆破器
          </button>
          <button
            className={`btn btn-sm ${showAnalysis ? "btn-primary" : ""}`}
            onClick={() => setShowAnalysis((v) => !v)}
          >
            <Bot size={12} />
            {showAnalysis ? "隐藏分析" : "AI 分析"}
          </button>
          <button
            className="btn btn-sm"
            onClick={handleReplay}
            disabled={replaying}
          >
            {replaying ? <Spinner size={12} /> : <Repeat size={12} />}
            重放
          </button>
        </div>
      </div>

      {/* 主体：AI 分析覆盖层 或 复用 FlowDetailPanel（统一显示 + 转发 + 右键菜单） */}
      {showAnalysis ? (
        <div className="scroll-container" style={{ padding: 12 }}>
          {analyzing && (
            <div style={{ display: "flex", alignItems: "center", gap: 12, padding: 24 }}>
              <Spinner size={20} />
              <span className="text-secondary">AI 正在分析此流量...</span>
            </div>
          )}
          {!analyzing && !analysis && (
            <div style={{ padding: 24, textAlign: "center", color: "var(--text-muted)" }}>
              点击"AI 分析"按钮开始分析
            </div>
          )}
          {!analyzing && analysis && (
            <div className="card">
              <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
                <span className="text-xs text-muted">风险等级:</span>
                <RiskBadge level={analysis.risk_level} />
              </div>
              <div style={{ marginBottom: 16 }}>
                <div className="text-xs text-muted" style={{ marginBottom: 8 }}>分析结果</div>
                <div style={{ fontSize: 13, lineHeight: 1.7, whiteSpace: "pre-wrap" }}>
                  {analysis.analysis}
                </div>
              </div>
              {analysis.suggestions.length > 0 && (
                <div>
                  <div className="text-xs text-muted" style={{ marginBottom: 8 }}>建议</div>
                  {analysis.suggestions.map((s, i) => (
                    <div key={i} className="ai-suggestion">{s}</div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      ) : (
        <div style={{ flex: 1, minHeight: 0, display: "flex" }}>
          <FlowDetailPanel flow={flow} storageKeyPrefix="flowdetail" />
        </div>
      )}
    </div>
  );
}
