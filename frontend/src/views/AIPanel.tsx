import { useState } from "react";
import { Send, Bot, Sparkles, AlertTriangle } from "lucide-react";
import { analyzeFlow } from "../api/ai";
import { generatePayloads } from "../api/ai";
import type { AnalyzeResponse, PayloadResponse } from "../types";
import { RiskBadge, Spinner, EmptyState } from "../components/Common";

interface Message {
  type: "user" | "assistant";
  content: string;
  analysis?: AnalyzeResponse;
  payloads?: string[];
}

export function AIPanel() {
  const [tab, setTab] = useState<"analyze" | "payloads">("analyze");

  // Analyze tab
  const [flowId, setFlowId] = useState("");
  const [question, setQuestion] = useState("");
  const [analyzing, setAnalyzing] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);

  // Payloads tab
  const [scene, setScene] = useState("fuzz");
  const [context, setContext] = useState("");
  const [count, setCount] = useState(30);
  const [genLoading, setGenLoading] = useState(false);
  const [genResult, setGenResult] = useState<PayloadResponse | null>(null);
  const [genError, setGenError] = useState<string | null>(null);

  const handleAnalyze = async () => {
    if (!flowId.trim()) return;
    setAnalyzing(true);
    const userMsg: Message = {
      type: "user",
      content: question.trim()
        ? `分析流量 ${flowId.substring(0, 8)}... 问题: ${question}`
        : `分析流量 ${flowId.substring(0, 8)}...`,
    };
    setMessages((prev) => [...prev, userMsg]);

    try {
      const result = await analyzeFlow({
        flow_id: flowId,
        question: question || null,
      });
      const aiMsg: Message = {
        type: "assistant",
        content: result.analysis,
        analysis: result,
      };
      setMessages((prev) => [...prev, aiMsg]);
    } catch (e) {
      const errMsg: Message = {
        type: "assistant",
        content: `分析失败: ${e instanceof Error ? e.message : e}`,
      };
      setMessages((prev) => [...prev, errMsg]);
    } finally {
      setAnalyzing(false);
      setQuestion("");
    }
  };

  const handleGenerate = async () => {
    setGenLoading(true);
    setGenError(null);
    try {
      const result = await generatePayloads({
        scene,
        context,
        count,
      });
      setGenResult(result);
    } catch (e) {
      setGenError(e instanceof Error ? e.message : "生成失败");
    } finally {
      setGenLoading(false);
    }
  };

  const copyPayloads = () => {
    if (genResult) {
      navigator.clipboard.writeText(genResult.payloads.join("\n"));
    }
  };

  return (
    <div className="flex flex-col h-full">
      <div className="view-header">
        <span className="view-title">AI 助手</span>
      </div>

      <div className="tabs">
        <div
          className={`tab ${tab === "analyze" ? "active" : ""}`}
          onClick={() => setTab("analyze")}
        >
          <Bot size={12} style={{ marginRight: 4, verticalAlign: "middle" }} />
          流量分析
        </div>
        <div
          className={`tab ${tab === "payloads" ? "active" : ""}`}
          onClick={() => setTab("payloads")}
        >
          <Sparkles size={12} style={{ marginRight: 4, verticalAlign: "middle" }} />
          Payload 生成
        </div>
      </div>

      {tab === "analyze" && (
        <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
          {/* 消息列表 */}
          <div className="scroll-container" style={{ padding: 12, flex: 1 }}>
            {messages.length === 0 && (
              <EmptyState
                icon={<Bot />}
                message="输入流量 ID 让 AI 分析安全风险"
              />
            )}
            {messages.map((msg, i) => (
              <div key={i} className={`ai-message ${msg.type}`}>
                {msg.type === "user" && (
                  <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 4 }}>
                    你
                  </div>
                )}
                {msg.type === "assistant" && (
                  <div style={{ fontSize: 11, color: "var(--accent-blue)", marginBottom: 4 }}>
                    AI
                  </div>
                )}
                <div style={{ whiteSpace: "pre-wrap" }}>{msg.content}</div>
                {msg.analysis && (
                  <div className="ai-risk" style={{ marginTop: 8 }}>
                    <div
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                        marginBottom: 8,
                      }}
                    >
                      <span className="text-xs text-muted">风险等级:</span>
                      <RiskBadge level={msg.analysis.risk_level} />
                    </div>
                    {msg.analysis.suggestions.length > 0 && (
                      <div>
                        <div className="text-xs text-muted mb-2">建议:</div>
                        {msg.analysis.suggestions.map((s, j) => (
                          <div key={j} className="ai-suggestion">
                            {s}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
            {analyzing && (
              <div className="ai-message assistant" style={{ display: "flex", gap: 12, alignItems: "center" }}>
                <Spinner size={14} />
                <span className="text-secondary">AI 正在分析...</span>
              </div>
            )}
          </div>

          {/* 输入区 */}
          <div
            style={{
              padding: 12,
              borderTop: "1px solid var(--border-default)",
              background: "var(--bg-secondary)",
            }}
          >
            <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
              <input
                className="input"
                style={{ flex: 1 }}
                placeholder="流量 ID (从流量列表复制)"
                value={flowId}
                onChange={(e) => setFlowId(e.target.value)}
              />
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <input
                className="input"
                style={{ flex: 1 }}
                placeholder="自定义问题 (可选，如: 是否存在 SQL 注入?)"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !analyzing) handleAnalyze();
                }}
              />
              <button
                className="btn btn-primary"
                onClick={handleAnalyze}
                disabled={analyzing || !flowId.trim()}
              >
                {analyzing ? <Spinner size={12} /> : <Send size={12} />}
                分析
              </button>
            </div>
          </div>
        </div>
      )}

      {tab === "payloads" && (
        <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
          {/* 左侧：配置 */}
          <div
            style={{
              width: 320,
              padding: 16,
              borderRight: "1px solid var(--border-default)",
              display: "flex",
              flexDirection: "column",
              gap: 12,
              overflow: "auto",
            }}
          >
            <div>
              <label className="text-xs text-muted" style={{ display: "block", marginBottom: 4 }}>
                场景
              </label>
              <select
                className="select w-full"
                value={scene}
                onChange={(e) => setScene(e.target.value)}
              >
                <option value="fuzz">通用 Fuzz</option>
                <option value="sqli">SQL 注入</option>
                <option value="xss">XSS 跨站脚本</option>
                <option value="weak_password">弱口令字典</option>
                <option value="waf_bypass">WAF 绕过</option>
              </select>
            </div>

            <div>
              <label className="text-xs text-muted" style={{ display: "block", marginBottom: 4 }}>
                上下文信息
              </label>
              <textarea
                className="textarea w-full"
                style={{ minHeight: 80 }}
                placeholder="目标技术栈、参数名、过滤规则等..."
                value={context}
                onChange={(e) => setContext(e.target.value)}
              />
            </div>

            <div>
              <label className="text-xs text-muted" style={{ display: "block", marginBottom: 4 }}>
                生成数量
              </label>
              <input
                className="input w-full"
                type="number"
                min={5}
                max={200}
                value={count}
                onChange={(e) => setCount(parseInt(e.target.value) || 30)}
              />
            </div>

            <button
              className="btn btn-primary w-full"
              onClick={handleGenerate}
              disabled={genLoading}
            >
              {genLoading ? <Spinner size={12} /> : <Sparkles size={12} />}
              生成 Payload
            </button>

            {genError && (
              <div
                style={{
                  display: "flex",
                  gap: 6,
                  padding: 8,
                  borderRadius: 4,
                  background: "rgba(248,81,73,0.1)",
                  color: "var(--accent-red)",
                  fontSize: 11,
                }}
              >
                <AlertTriangle size={14} style={{ flexShrink: 0 }} />
                {genError}
              </div>
            )}
          </div>

          {/* 右侧：结果 */}
          <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "8px 12px",
                borderBottom: "1px solid var(--border-default)",
                background: "var(--bg-secondary)",
              }}
            >
              <span className="text-sm">
                {genResult
                  ? `${genResult.scene} - ${genResult.payloads.length} 个 payload`
                  : "等待生成..."}
              </span>
              {genResult && (
                <button className="btn btn-sm" onClick={copyPayloads}>
                  复制全部
                </button>
              )}
            </div>

            <div className="scroll-container">
              {!genResult && !genLoading && (
                <EmptyState
                  icon={<Sparkles size={40} style={{ opacity: 0.2 }} />}
                  message="配置场景后点击生成"
                />
              )}

              {genLoading && (
                <div style={{ display: "flex", gap: 12, padding: 24, alignItems: "center" }}>
                  <Spinner size={20} />
                  <span className="text-secondary">AI 正在生成 payload...</span>
                </div>
              )}

              {genResult && !genLoading && (
                <div style={{ padding: 8 }}>
                  {genResult.payloads.map((p, i) => (
                    <div
                      key={i}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                        padding: "4px 8px",
                        fontFamily: "var(--font-mono)",
                        fontSize: 12,
                        borderBottom: "1px solid var(--border-subtle)",
                        cursor: "pointer",
                      }}
                      onClick={() => navigator.clipboard.writeText(p)}
                      title="点击复制"
                    >
                      <span
                        style={{
                          color: "var(--text-muted)",
                          fontSize: 10,
                          width: 28,
                          textAlign: "right",
                        }}
                      >
                        {i + 1}
                      </span>
                      <span style={{ color: "var(--text-primary)" }}>{p}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
