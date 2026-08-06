import { useState, useEffect, useCallback, useRef } from "react";
import {
  Plus,
  Trash2,
  Pencil,
  Plug,
  PlugZap,
  Server,
  Zap,
  Check,
  AlertCircle,
  X,
  ChevronDown,
  ChevronRight,
  RefreshCw,
  Wrench,
  Upload,
  Eye,
  Code,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  skillsApi,
  mcpApi,
  type Skill,
  type SkillDetail,
  type McpServerInfo,
  type McpTool,
} from "../api/skills";

type Tab = "skills" | "mcp";

type ToastType = "success" | "error";
interface ToastState {
  type: ToastType;
  msg: string;
}

// ===== Skill 编辑表单状态（文件方案：直接编辑 Markdown） =====
interface SkillFormState {
  name: string;
  content: string; // 完整 Markdown 内容
}

const emptySkillForm: SkillFormState = {
  name: "",
  content: "",
};

// ===== MCP 添加表单状态 =====
interface McpFormState {
  name: string;
  transport: "stdio" | "http";
  command: string;
  args: string; // 逗号分隔
  url: string;
  enabled: boolean;
}

const emptyMcpForm: McpFormState = {
  name: "",
  transport: "stdio",
  command: "npx",
  args: "",
  url: "",
  enabled: true,
};

export function AgentConfig() {
  const [tab, setTab] = useState<Tab>("skills");
  const [toast, setToast] = useState<ToastState | null>(null);

  const showToast = useCallback((type: ToastType, msg: string) => {
    setToast({ type, msg });
    window.setTimeout(() => setToast(null), 2500);
  }, []);

  return (
    <div className="flex flex-col h-full">
      <div className="view-header">
        <span className="view-title">Agent 能力扩展</span>
      </div>

      {/* Tab 切换 */}
      <div className="agentconfig-tabs">
        <button
          className={`agentconfig-tab ${tab === "skills" ? "active" : ""}`}
          onClick={() => setTab("skills")}
        >
          <Zap size={14} />
          Skills 技能
        </button>
        <button
          className={`agentconfig-tab ${tab === "mcp" ? "active" : ""}`}
          onClick={() => setTab("mcp")}
        >
          <Plug size={14} />
          MCP 服务器
        </button>
      </div>

      <div className="scroll-container" style={{ padding: 16, maxWidth: 860 }}>
        {tab === "skills" ? (
          <SkillsPanel showToast={showToast} />
        ) : (
          <McpPanel showToast={showToast} />
        )}
      </div>

      {/* Toast */}
      {toast && (
        <div
          style={{
            position: "fixed",
            top: 16,
            right: 16,
            zIndex: 200,
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
                : "var(--accent-red)",
            color: "#fff",
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

// ===== Skills 面板（文件方案） =====
function SkillsPanel({
  showToast,
}: {
  showToast: (type: ToastType, msg: string) => void;
}) {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<SkillFormState>(emptySkillForm);
  const [saving, setSaving] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [fullContent, setFullContent] = useState<string>("");
  const [viewMode, setViewMode] = useState<"rendered" | "source">("rendered");

  const loadSkills = useCallback(async () => {
    setLoading(true);
    try {
      const data = await skillsApi.list();
      setSkills(data.skills);
    } catch (e) {
      showToast("error", e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    loadSkills();
  }, [loadSkills]);

  const handleAdd = () => {
    setForm({
      ...emptySkillForm,
      content: "# 技能名称\n\n一句话描述技能用途。\n\n## 使用方法\n\n在此编写完整操作指导...\n",
    });
    setEditingId(null);
    setShowForm(true);
  };

  const handleEdit = async (skill: Skill) => {
    try {
      const detail = await skillsApi.get(skill.name);
      setForm({
        name: detail.name,
        content: detail.instructions,
      });
      setEditingId(skill.name);
      setShowForm(true);
    } catch (e) {
      showToast("error", e instanceof Error ? e.message : "加载详情失败");
    }
  };

  const handleExpand = async (skill: Skill) => {
    if (expandedId === skill.id) {
      setExpandedId(null);
      return;
    }
    try {
      const detail = await skillsApi.get(skill.name);
      setFullContent(detail.instructions);
      setExpandedId(skill.id);
    } catch (e) {
      showToast("error", e instanceof Error ? e.message : "加载详情失败");
    }
  };

  const handleDelete = async (skill: Skill) => {
    if (!confirm(`确认删除技能「${skill.label}」？将删除对应的 .md 文件，此操作不可撤销。`)) return;
    try {
      await skillsApi.delete(skill.name);
      setSkills((prev) => prev.filter((s) => s.id !== skill.id));
      showToast("success", "技能已删除");
    } catch (e) {
      showToast("error", e instanceof Error ? e.message : "删除失败");
    }
  };

  const handleSave = async () => {
    if (!form.name.trim()) {
      showToast("error", "请填写技能名称（将作为文件名）");
      return;
    }
    if (!form.content.trim()) {
      showToast("error", "请编写技能内容");
      return;
    }
    setSaving(true);
    try {
      if (editingId) {
        await skillsApi.update(editingId, { content: form.content });
        showToast("success", "技能已更新");
      } else {
        await skillsApi.create({ name: form.name, content: form.content });
        showToast("success", "技能已创建");
      }
      setShowForm(false);
      loadSkills();
    } catch (e) {
      showToast("error", e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="empty-state">
        <div className="spinner" style={{ width: 28, height: 28 }} />
        <span>加载技能列表...</span>
      </div>
    );
  }

  return (
    <>
      {/* 说明卡片 */}
      <div className="card mb-4" style={{ borderColor: "var(--border-subtle)" }}>
        <div
          style={{
            display: "flex",
            alignItems: "flex-start",
            gap: 10,
            fontSize: 12,
            color: "var(--text-secondary)",
            lineHeight: 1.7,
          }}
        >
          <Zap
            size={16}
            style={{ color: "var(--accent-yellow)", flexShrink: 0, marginTop: 1 }}
          />
          <div>
            技能是 <code style={{ fontSize: 11 }}>~/.hemlock/skills/</code> 目录下的 Markdown 文件，
            文件存在即启用。Agent 会自动将技能描述注入系统提示词，需要时通过{" "}
            <code style={{ fontSize: 11 }}>invoke_skill</code> 加载完整指令。
            也可直接用任意编辑器创建/编辑 .md 文件，保存即生效。
          </div>
        </div>
      </div>

      {/* 技能列表 */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 12,
        }}
      >
        <span style={{ fontWeight: 600, fontSize: 14 }}>
          技能文件 ({skills.length})
        </span>
        <button className="btn btn-primary btn-sm" onClick={handleAdd}>
          <Plus size={12} />
          添加技能
        </button>
      </div>

      {skills.length === 0 ? (
        <div className="card" style={{ textAlign: "center", padding: 40 }}>
          <Zap size={32} style={{ opacity: 0.2, marginBottom: 8 }} />
          <div style={{ color: "var(--text-muted)", fontSize: 13 }}>
            暂无技能文件，点击「添加技能」或直接在 ~/.hemlock/skills/ 目录创建 .md 文件
          </div>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {skills.map((skill) => (
            <div key={skill.id} className="agentconfig-item-card">
              <div
                className="agentconfig-item-header"
                onClick={() => handleExpand(skill)}
              >
                {expandedId === skill.id ? (
                  <ChevronDown size={14} style={{ flexShrink: 0 }} />
                ) : (
                  <ChevronRight size={14} style={{ flexShrink: 0 }} />
                )}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 8,
                    }}
                  >
                    <span style={{ fontWeight: 600, fontSize: 13 }}>
                      {skill.label}
                    </span>
                    <span
                      className="agentconfig-badge"
                      style={{
                        background: "var(--bg-tertiary)",
                        color: "var(--text-muted)",
                      }}
                    >
                      {skill.name}.md
                    </span>
                  </div>
                  <div
                    style={{
                      fontSize: 12,
                      color: "var(--text-muted)",
                      marginTop: 2,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {skill.description || "(无描述)"}
                  </div>
                </div>
                <div
                  className="agentconfig-item-actions"
                  onClick={(e) => e.stopPropagation()}
                >
                  <button
                    className="btn btn-sm"
                    onClick={() => handleEdit(skill)}
                    title="编辑"
                  >
                    <Pencil size={11} />
                  </button>
                  <button
                    className="btn btn-sm"
                    onClick={() => handleDelete(skill)}
                    title="删除"
                    style={{ color: "var(--accent-red)" }}
                  >
                    <Trash2 size={11} />
                  </button>
                </div>
              </div>
              {expandedId === skill.id && (
                <div className="agentconfig-item-detail">
                  <div
                    style={{
                      marginBottom: 8,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                    }}
                  >
                    <span className="text-xs text-muted">技能内容：</span>
                    <div style={{ display: "flex", gap: 4 }}>
                      <button
                        className={`btn btn-sm ${viewMode === "rendered" ? "btn-primary" : ""}`}
                        onClick={() => setViewMode("rendered")}
                        title="渲染预览"
                        style={{ padding: "2px 8px", fontSize: 11 }}
                      >
                        <Eye size={10} />
                        渲染
                      </button>
                      <button
                        className={`btn btn-sm ${viewMode === "source" ? "btn-primary" : ""}`}
                        onClick={() => setViewMode("source")}
                        title="源码查看"
                        style={{ padding: "2px 8px", fontSize: 11 }}
                      >
                        <Code size={10} />
                        源码
                      </button>
                    </div>
                  </div>
                  {viewMode === "rendered" ? (
                    <div
                      className="agent-chat-msg-markdown"
                      style={{
                        background: "var(--bg-primary)",
                        border: "1px solid var(--border-subtle)",
                        borderRadius: 6,
                        padding: "10px 14px",
                        maxHeight: 400,
                        overflow: "auto",
                      }}
                    >
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{fullContent || "(空)"}</ReactMarkdown>
                    </div>
                  ) : (
                    <pre
                      style={{
                        background: "var(--bg-primary)",
                        border: "1px solid var(--border-subtle)",
                        borderRadius: 6,
                        padding: 10,
                        fontSize: 12,
                        lineHeight: 1.6,
                        overflow: "auto",
                        maxHeight: 400,
                        whiteSpace: "pre-wrap",
                        wordBreak: "break-word",
                        margin: 0,
                        fontFamily: "var(--font-mono)",
                      }}
                    >
                      {fullContent || "(空)"}
                    </pre>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* 添加/编辑表单弹窗 */}
      {showForm && (
        <SkillFormModal
          form={form}
          setForm={setForm}
          editing={!!editingId}
          saving={saving}
          onSave={handleSave}
          onClose={() => setShowForm(false)}
        />
      )}
    </>
  );
}

// ===== MCP 面板 =====
function McpPanel({
  showToast,
}: {
  showToast: (type: ToastType, msg: string) => void;
}) {
  const [servers, setServers] = useState<McpServerInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<McpFormState>(emptyMcpForm);
  const [saving, setSaving] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [tools, setTools] = useState<McpTool[]>([]);
  const [connectingId, setConnectingId] = useState<string | null>(null);

  const loadServers = useCallback(async () => {
    setLoading(true);
    try {
      const data = await mcpApi.list();
      setServers(data.servers);
    } catch (e) {
      showToast("error", e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    loadServers();
  }, [loadServers]);

  const handleAdd = () => {
    setForm(emptyMcpForm);
    setShowForm(true);
  };

  const handleExpand = async (server: McpServerInfo) => {
    if (expandedId === server.id) {
      setExpandedId(null);
      return;
    }
    if (server.status === "connected") {
      try {
        const data = await mcpApi.tools(server.id);
        setTools(data.tools);
      } catch {
        setTools([]);
      }
    } else {
      setTools([]);
    }
    setExpandedId(server.id);
  };

  const handleConnect = async (server: McpServerInfo) => {
    setConnectingId(server.id);
    try {
      const result = await mcpApi.connect(server.id);
      if (result.status === "connected") {
        showToast("success", `连接成功，发现 ${result.tool_count || 0} 个工具`);
      } else {
        showToast("error", result.error || "连接失败");
      }
      loadServers();
    } catch (e) {
      showToast("error", e instanceof Error ? e.message : "连接失败");
    } finally {
      setConnectingId(null);
    }
  };

  const handleDisconnect = async (server: McpServerInfo) => {
    try {
      await mcpApi.disconnect(server.id);
      showToast("success", "已断开连接");
      loadServers();
    } catch (e) {
      showToast("error", e instanceof Error ? e.message : "断开失败");
    }
  };

  const handleDelete = async (server: McpServerInfo) => {
    if (!confirm(`确认删除 MCP 服务器「${server.name}」？`)) return;
    try {
      await mcpApi.delete(server.id);
      setServers((prev) => prev.filter((s) => s.id !== server.id));
      showToast("success", "服务器已删除");
    } catch (e) {
      showToast("error", e instanceof Error ? e.message : "删除失败");
    }
  };

  const handleSave = async () => {
    if (!form.name.trim()) {
      showToast("error", "请填写服务器名称");
      return;
    }
    if (form.transport === "stdio" && !form.command.trim()) {
      showToast("error", "stdio 模式需要填写启动命令");
      return;
    }
    if (form.transport === "http" && !form.url.trim()) {
      showToast("error", "http 模式需要填写 URL");
      return;
    }
    setSaving(true);
    try {
      const args = form.args
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean);

      await mcpApi.create({
        name: form.name,
        transport: form.transport,
        command: form.command,
        args,
        url: form.url,
        enabled: form.enabled,
      });
      showToast("success", "MCP 服务器已添加");
      setShowForm(false);
      loadServers();
    } catch (e) {
      showToast("error", e instanceof Error ? e.message : "添加失败");
    } finally {
      setSaving(false);
    }
  };

  const statusColor = (status: string): string => {
    switch (status) {
      case "connected":
        return "var(--accent-green)";
      case "connecting":
        return "var(--accent-yellow)";
      case "error":
        return "var(--accent-red)";
      default:
        return "var(--text-muted)";
    }
  };

  const statusLabel = (status: string): string => {
    switch (status) {
      case "connected":
        return "已连接";
      case "connecting":
        return "连接中";
      case "error":
        return "错误";
      default:
        return "未连接";
    }
  };

  if (loading) {
    return (
      <div className="empty-state">
        <div className="spinner" style={{ width: 28, height: 28 }} />
        <span>加载 MCP 服务器列表...</span>
      </div>
    );
  }

  return (
    <>
      {/* 说明卡片 */}
      <div className="card mb-4" style={{ borderColor: "var(--border-subtle)" }}>
        <div
          style={{
            display: "flex",
            alignItems: "flex-start",
            gap: 10,
            fontSize: 12,
            color: "var(--text-secondary)",
            lineHeight: 1.7,
          }}
        >
          <Plug
            size={16}
            style={{ color: "var(--accent-blue)", flexShrink: 0, marginTop: 1 }}
          />
          <div>
            MCP (Model Context Protocol) 服务器为 Agent 提供外部工具能力。
            连接成功后，服务器提供的工具会自动注入到 LLM 的可用工具列表中，Agent 可按需调用。
            支持 stdio（本地进程）和 http（远程端点）两种传输方式。
          </div>
        </div>
      </div>

      {/* 服务器列表 */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginBottom: 12,
        }}
      >
        <span style={{ fontWeight: 600, fontSize: 14 }}>
          MCP 服务器 ({servers.length})
        </span>
        <button className="btn btn-primary btn-sm" onClick={handleAdd}>
          <Plus size={12} />
          添加服务器
        </button>
      </div>

      {servers.length === 0 ? (
        <div className="card" style={{ textAlign: "center", padding: 40 }}>
          <Server size={32} style={{ opacity: 0.2, marginBottom: 8 }} />
          <div style={{ color: "var(--text-muted)", fontSize: 13 }}>
            暂无 MCP 服务器，点击「添加服务器」开始连接外部工具
          </div>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {servers.map((server) => (
            <div key={server.id} className="agentconfig-item-card">
              <div
                className="agentconfig-item-header"
                onClick={() => handleExpand(server)}
              >
                {expandedId === server.id ? (
                  <ChevronDown size={14} style={{ flexShrink: 0 }} />
                ) : (
                  <ChevronRight size={14} style={{ flexShrink: 0 }} />
                )}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 8,
                    }}
                  >
                    <span style={{ fontWeight: 600, fontSize: 13 }}>
                      {server.name}
                    </span>
                    <span
                      className="agentconfig-badge"
                      style={{
                        background: "var(--bg-tertiary)",
                        color: "var(--text-muted)",
                      }}
                    >
                      {server.transport}
                    </span>
                    <span
                      className="agentconfig-badge"
                      style={{
                        background: `${statusColor(server.status)}22`,
                        color: statusColor(server.status),
                      }}
                    >
                      <span
                        style={{
                          display: "inline-block",
                          width: 6,
                          height: 6,
                          borderRadius: "50%",
                          background: statusColor(server.status),
                          marginRight: 4,
                        }}
                      />
                      {statusLabel(server.status)}
                    </span>
                    {server.status === "connected" && server.tool_count > 0 && (
                      <span
                        className="agentconfig-badge"
                        style={{
                          background: "rgba(88,166,255,0.12)",
                          color: "var(--accent-blue)",
                        }}
                      >
                        {server.tool_count} 工具
                      </span>
                    )}
                  </div>
                  <div
                    style={{
                      fontSize: 12,
                      color: "var(--text-muted)",
                      marginTop: 2,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {server.transport === "stdio"
                      ? `${server.command} ...`
                      : server.url}
                  </div>
                  {server.last_error && (
                    <div
                      style={{
                        fontSize: 11,
                        color: "var(--accent-red)",
                        marginTop: 2,
                      }}
                    >
                      {server.last_error}
                    </div>
                  )}
                </div>
                <div
                  className="agentconfig-item-actions"
                  onClick={(e) => e.stopPropagation()}
                >
                  {server.status === "connected" ? (
                    <button
                      className="btn btn-sm"
                      onClick={() => handleDisconnect(server)}
                    >
                      <PlugZap size={11} />
                      断开
                    </button>
                  ) : (
                    <button
                      className="btn btn-sm btn-primary"
                      onClick={() => handleConnect(server)}
                      disabled={connectingId === server.id}
                    >
                      {connectingId === server.id ? (
                        <RefreshCw size={11} className="spin" />
                      ) : (
                        <Plug size={11} />
                      )}
                      连接
                    </button>
                  )}
                  <button
                    className="btn btn-sm"
                    onClick={() => handleDelete(server)}
                    title="删除"
                    style={{ color: "var(--accent-red)" }}
                  >
                    <Trash2 size={11} />
                  </button>
                </div>
              </div>
              {expandedId === server.id && (
                <div className="agentconfig-item-detail">
                  {server.status === "connected" && tools.length > 0 ? (
                    <>
                      <div
                        style={{
                          marginBottom: 6,
                          display: "flex",
                          alignItems: "center",
                          gap: 6,
                        }}
                      >
                        <Wrench size={12} style={{ color: "var(--accent-blue)" }} />
                        <span className="text-xs text-muted">
                          发现的工具 ({tools.length})
                        </span>
                      </div>
                      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                        {tools.map((tool, i) => (
                          <div
                            key={i}
                            style={{
                              padding: "6px 10px",
                              background: "var(--bg-primary)",
                              border: "1px solid var(--border-subtle)",
                              borderRadius: 4,
                              fontSize: 12,
                            }}
                          >
                            <div style={{ fontWeight: 600, color: "var(--accent-cyan)" }}>
                              {tool.name}
                            </div>
                            {tool.description && (
                              <div
                                style={{
                                  color: "var(--text-muted)",
                                  marginTop: 2,
                                  fontSize: 11,
                                }}
                              >
                                {tool.description}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    </>
                  ) : (
                    <div
                      style={{
                        fontSize: 12,
                        color: "var(--text-muted)",
                        textAlign: "center",
                        padding: 8,
                      }}
                    >
                      {server.status === "connected"
                        ? "未发现工具"
                        : "服务器未连接，连接后可查看工具列表"}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* 添加表单弹窗 */}
      {showForm && (
        <McpFormModal
          form={form}
          setForm={setForm}
          saving={saving}
          onSave={handleSave}
          onClose={() => setShowForm(false)}
        />
      )}
    </>
  );
}

// ===== Skill 表单弹窗（Markdown 编辑器） =====
function SkillFormModal({
  form,
  setForm,
  editing,
  saving,
  onSave,
  onClose,
}: {
  form: SkillFormState;
  setForm: React.Dispatch<React.SetStateAction<SkillFormState>>;
  editing: boolean;
  saving: boolean;
  onSave: () => void;
  onClose: () => void;
}) {
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFileImport = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (ev) => {
      const text = (ev.target?.result as string) || "";
      setForm((prev) => ({ ...prev, content: text }));
      if (fileInputRef.current) fileInputRef.current.value = "";
    };
    reader.readAsText(file);
  };

  return (
    <div className="sidebar-save-overlay" onClick={() => !saving && onClose()}>
      <div
        className="agentconfig-modal"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sidebar-save-header">
          <span>{editing ? "编辑技能" : "添加技能"}</span>
          <button
            className="sidebar-save-close"
            onClick={() => !saving && onClose()}
          >
            <X size={14} />
          </button>
        </div>
        <div className="sidebar-save-body" style={{ maxHeight: "60vh", overflow: "auto" }}>
          {/* 技能名称 */}
          <div style={{ marginBottom: 12 }}>
            <label className="text-xs text-muted" style={{ display: "block", marginBottom: 4 }}>
              技能名称 (文件名) — 唯一标识符
            </label>
            <input
              className="input w-full"
              value={form.name}
              onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))}
              placeholder="如 cve-search"
              disabled={editing}
              style={editing ? { opacity: 0.5 } : undefined}
            />
            <div className="text-xs text-muted mt-1">
              只允许小写字母、数字和连字符，将创建为 ~/.hemlock/skills/{form.name || "name"}.md
            </div>
          </div>

          {/* Markdown 内容编辑器 */}
          <div>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
              <label className="text-xs text-muted">
                技能内容 (Markdown)
              </label>
              <button
                className="btn btn-sm"
                onClick={() => fileInputRef.current?.click()}
                title="从 .md 文件导入内容"
              >
                <Upload size={10} />
                导入文件
              </button>
              <input
                ref={fileInputRef}
                type="file"
                accept=".md,.txt"
                onChange={handleFileImport}
                style={{ display: "none" }}
              />
            </div>
            <textarea
              className="input w-full"
              value={form.content}
              onChange={(e) => setForm((p) => ({ ...p, content: e.target.value }))}
              placeholder={
                "# 技能显示名称\n\n" +
                "一句话描述技能用途，会注入系统提示词。\n\n" +
                "## 使用方法\n\n" +
                "在此编写完整操作指导，Agent 调用 invoke_skill 时会加载此内容。\n\n" +
                "## 注意事项\n\n" +
                "- 第一行 H1 标题 = 显示名称\n" +
                "- H1 后第一段 = 简短描述\n" +
                "- 完整内容 = 激活时加载的指令"
              }
              rows={16}
              style={{
                resize: "vertical",
                fontFamily: "monospace",
                fontSize: 12,
                lineHeight: 1.5,
              }}
            />
            <div className="text-xs text-muted mt-1">
              第一行 H1 标题作为显示名称，H1 后第一段作为简短描述
            </div>
          </div>
        </div>
        <div className="sidebar-save-footer">
          <button className="btn" onClick={onClose} disabled={saving}>
            取消
          </button>
          <button className="btn btn-primary" onClick={onSave} disabled={saving}>
            {saving ? "保存中..." : "保存"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ===== MCP 表单弹窗 =====
function McpFormModal({
  form,
  setForm,
  saving,
  onSave,
  onClose,
}: {
  form: McpFormState;
  setForm: React.Dispatch<React.SetStateAction<McpFormState>>;
  saving: boolean;
  onSave: () => void;
  onClose: () => void;
}) {
  return (
    <div className="sidebar-save-overlay" onClick={() => !saving && onClose()}>
      <div
        className="agentconfig-modal"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sidebar-save-header">
          <span>添加 MCP 服务器</span>
          <button
            className="sidebar-save-close"
            onClick={() => !saving && onClose()}
          >
            <X size={14} />
          </button>
        </div>
        <div className="sidebar-save-body" style={{ maxHeight: "60vh", overflow: "auto" }}>
          {/* 名称 */}
          <div style={{ marginBottom: 12 }}>
            <label className="text-xs text-muted" style={{ display: "block", marginBottom: 4 }}>
              服务器名称
            </label>
            <input
              className="input w-full"
              value={form.name}
              onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))}
              placeholder="如 sqlite-tools"
            />
          </div>

          {/* 传输方式 */}
          <div style={{ marginBottom: 12 }}>
            <label className="text-xs text-muted" style={{ display: "block", marginBottom: 4 }}>
              传输方式 (transport)
            </label>
            <div style={{ display: "flex", gap: 8 }}>
              <button
                className={`btn btn-sm ${form.transport === "stdio" ? "btn-primary" : ""}`}
                onClick={() => setForm((p) => ({ ...p, transport: "stdio" }))}
                style={{ flex: 1 }}
              >
                stdio (本地进程)
              </button>
              <button
                className={`btn btn-sm ${form.transport === "http" ? "btn-primary" : ""}`}
                onClick={() => setForm((p) => ({ ...p, transport: "http" }))}
                style={{ flex: 1 }}
              >
                http (远程端点)
              </button>
            </div>
          </div>

          {/* stdio 模式字段 */}
          {form.transport === "stdio" && (
            <>
              <div style={{ marginBottom: 12 }}>
                <label className="text-xs text-muted" style={{ display: "block", marginBottom: 4 }}>
                  启动命令 (command)
                </label>
                <input
                  className="input w-full"
                  value={form.command}
                  onChange={(e) => setForm((p) => ({ ...p, command: e.target.value }))}
                  placeholder="如 npx 或 node"
                />
              </div>
              <div style={{ marginBottom: 12 }}>
                <label className="text-xs text-muted" style={{ display: "block", marginBottom: 4 }}>
                  参数 (args) — 每行一个
                </label>
                <textarea
                  className="input w-full"
                  value={form.args}
                  onChange={(e) => setForm((p) => ({ ...p, args: e.target.value }))}
                  placeholder={"如：\n@modelcontextprotocol/server-sqlite\n--db-path\n./test.db"}
                  rows={4}
                  style={{ resize: "vertical", fontFamily: "monospace", fontSize: 12 }}
                />
              </div>
            </>
          )}

          {/* http 模式字段 */}
          {form.transport === "http" && (
            <div style={{ marginBottom: 12 }}>
              <label className="text-xs text-muted" style={{ display: "block", marginBottom: 4 }}>
                服务器 URL
              </label>
              <input
                className="input w-full"
                value={form.url}
                onChange={(e) => setForm((p) => ({ ...p, url: e.target.value }))}
                placeholder="http://localhost:3000/mcp"
              />
            </div>
          )}

          {/* 启用开关 */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "8px 12px",
              background: "var(--bg-tertiary)",
              borderRadius: 6,
              border: "1px solid var(--border-subtle)",
            }}
          >
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(e) => setForm((p) => ({ ...p, enabled: e.target.checked }))}
              id="mcp-enabled"
              style={{ cursor: "pointer" }}
            />
            <label
              htmlFor="mcp-enabled"
              style={{ fontSize: 12, cursor: "pointer", color: "var(--text-secondary)" }}
            >
              启用后自动连接（应用启动时）
            </label>
          </div>
        </div>
        <div className="sidebar-save-footer">
          <button className="btn" onClick={onClose} disabled={saving}>
            取消
          </button>
          <button className="btn btn-primary" onClick={onSave} disabled={saving}>
            {saving ? "添加中..." : "添加"}
          </button>
        </div>
      </div>
    </div>
  );
}
