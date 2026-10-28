import { useState, useEffect, useCallback } from "react";
import { Brain, Plus, Search, Trash2, Edit3, X, Tag, BookOpen, ArrowLeft, FolderOpen } from "lucide-react";
import {
  memoryApi,
  type Memory,
  type MemoryType,
  type KnowledgeBase,
  type MemoryCreateRequest,
} from "../api/memory";
import { Spinner } from "../components/Common";
import { MarkdownView } from "../components/MarkdownView";

const TYPE_LABELS: Record<MemoryType, string> = {
  user: "使用者",
  feedback: "反馈",
  project: "项目",
  reference: "参考",
  kb: "知识库",
};

const TYPE_COLORS: Record<MemoryType, string> = {
  user: "var(--accent-blue)",
  feedback: "var(--accent-yellow)",
  project: "var(--accent-green)",
  reference: "var(--text-muted)",
  kb: "var(--accent-purple)",
};

// 作用域：长期记忆（经验 + 知识库，跨项目）/ 项目记忆（仅本项目）
type Scope = "global" | "project";

type FilterType = "all" | "user" | "feedback" | "reference" | "kb";

const FILTER_TABS: { value: FilterType; label: string }[] = [
  { value: "all", label: "全部" },
  { value: "user", label: "使用者" },
  { value: "feedback", label: "反馈" },
  { value: "reference", label: "参考" },
  { value: "kb", label: "知识库" },
];

interface EditorState {
  id: string | null;
  name: string;
  description: string;
  type: MemoryType;
  trigger: string;
  content: string;
  kb: string | null; // 非空 = 知识库条目
}

const EMPTY_EDITOR: EditorState = {
  id: null,
  name: "",
  description: "",
  type: "feedback",
  trigger: "",
  content: "",
  kb: null,
};

export function MemoryManager() {
  const [scope, setScope] = useState<Scope>("global");
  const [memories, setMemories] = useState<Memory[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<FilterType>("all");
  const [searchText, setSearchText] = useState("");
  const [searchResults, setSearchResults] = useState<Memory[] | null>(null);
  const [selected, setSelected] = useState<Memory | null>(null);
  const [showEditor, setShowEditor] = useState(false);
  const [editor, setEditor] = useState<EditorState>(EMPTY_EDITOR);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);

  // ===== 知识库 =====
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [activeKb, setActiveKb] = useState<string | null>(null);
  const [showKbModal, setShowKbModal] = useState(false);
  const [kbForm, setKbForm] = useState({ name: "", description: "" });
  const [kbDeleteConfirm, setKbDeleteConfirm] = useState<KnowledgeBase | null>(null);
  const [kbSaving, setKbSaving] = useState(false);

  const loadKbs = useCallback(async () => {
    try {
      const { kbs: data } = await memoryApi.listKbs();
      setKbs(data);
    } catch {
      // 非致命
    }
  }, []);

  const loadMemories = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      let data: Memory[];
      if (scope === "project") {
        // 项目记忆：仅 project 类型
        ({ memories: data } = await memoryApi.list("project"));
      } else if (activeKb) {
        // 某个知识库内的条目
        ({ memories: data } = await memoryApi.list(undefined, undefined, activeKb));
      } else if (filter === "kb") {
        // 知识库总览：显示库卡片，不列条目
        data = [];
      } else if (filter === "all") {
        // 长期记忆全部：经验 + 知识库条目（排除项目记忆）
        const resp = await memoryApi.list();
        data = resp.memories.filter((m) => m.type !== "project");
      } else {
        ({ memories: data } = await memoryApi.list(filter));
      }
      setMemories(data);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [scope, filter, activeKb]);

  useEffect(() => {
    loadMemories();
  }, [loadMemories]);

  useEffect(() => {
    loadKbs();
  }, [loadKbs]);

  const handleSearch = async () => {
    if (!searchText.trim()) {
      setSearchResults(null);
      return;
    }
    setLoading(true);
    try {
      const { results } = await memoryApi.search(searchText.trim());
      setSearchResults(results);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  const handleClearSearch = () => {
    setSearchText("");
    setSearchResults(null);
  };

  const openNew = () => {
    setEditor({ ...EMPTY_EDITOR, kb: activeKb, type: activeKb ? "kb" : "feedback" });
    setShowEditor(true);
  };

  const openEdit = (mem: Memory) => {
    setEditor({
      id: mem.id,
      name: mem.name,
      description: mem.description,
      type: mem.type,
      trigger: mem.trigger.join(", "),
      content: mem.content,
      kb: mem.kb,
    });
    setSelected(null);
    setShowEditor(true);
  };

  const handleSave = async () => {
    if (!editor.name.trim() || !editor.description.trim()) {
      setError("名称和描述不能为空");
      return;
    }

    setSaving(true);
    setError(null);

    const triggerList = editor.trigger
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);

    try {
      const type = editor.kb ? "kb" : editor.type;
      if (editor.id) {
        await memoryApi.update(editor.id, {
          name: editor.name,
          description: editor.description,
          type,
          trigger: triggerList,
          content: editor.content,
          ...(editor.kb ? { kb: editor.kb } : {}),
        });
      } else {
        const payload: MemoryCreateRequest = {
          name: editor.name,
          description: editor.description,
          type,
          trigger: triggerList,
          content: editor.content,
          ...(editor.kb ? { kb: editor.kb } : {}),
        };
        await memoryApi.create(payload);
      }
      setShowEditor(false);
      await loadMemories();
      await loadKbs();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await memoryApi.delete(id);
      setDeleteConfirm(null);
      setSelected(null);
      await loadMemories();
      await loadKbs();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  // ===== 知识库管理 =====

  const handleCreateKb = async () => {
    if (!kbForm.name.trim()) {
      setError("知识库名称不能为空");
      return;
    }
    setKbSaving(true);
    setError(null);
    try {
      await memoryApi.createKb(kbForm.name.trim(), kbForm.description.trim());
      setShowKbModal(false);
      setKbForm({ name: "", description: "" });
      await loadKbs();
      // 建完直接进入该库
      setActiveKb(kbForm.name.trim());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setKbSaving(false);
    }
  };

  const handleDeleteKb = async (kb: KnowledgeBase) => {
    try {
      await memoryApi.deleteKb(kb.name);
      setKbDeleteConfirm(null);
      if (activeKb === kb.name) setActiveKb(null);
      await loadKbs();
      await loadMemories();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const displayList = searchResults ?? memories;
  const kbView = scope === "global" && filter === "kb" && !activeKb;

  const scopeBtn = (value: Scope, label: string) => (
    <button
      key={value}
      onClick={() => {
        setScope(value);
        setActiveKb(null);
        setSearchResults(null);
      }}
      style={{
        padding: "5px 14px",
        fontSize: 13,
        fontWeight: 600,
        border: "1px solid var(--border-default)",
        borderRadius: 4,
        background: scope === value ? "var(--accent-blue)" : "var(--bg-secondary)",
        color: scope === value ? "#fff" : "var(--text-secondary)",
        cursor: "pointer",
        transition: "all 0.15s",
      }}
    >
      {label}
    </button>
  );

  return (
    <div className="memory-manager" style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* Header */}
      <div style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "12px 16px",
        borderBottom: "1px solid var(--border-default)",
        background: "var(--bg-secondary)",
        flexShrink: 0,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Brain size={18} style={{ color: "var(--accent-blue)" }} />
          <span style={{ fontSize: 15, fontWeight: 600, color: "var(--text-primary)" }}>
            记忆库
          </span>
          <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
            ({displayList.length})
          </span>
          <div style={{ display: "flex", gap: 4, marginLeft: 12 }}>
            {scopeBtn("global", "长期记忆")}
            {scopeBtn("project", "项目记忆")}
          </div>
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          {scope === "global" && filter === "kb" && (
            <button
              className="btn btn-sm"
              onClick={() => { setKbForm({ name: "", description: "" }); setShowKbModal(true); }}
            >
              <FolderOpen size={12} />
              新建知识库
            </button>
          )}
          {(!kbView) && (
            <button className="btn btn-sm btn-primary" onClick={openNew}>
              <Plus size={12} />
              {activeKb ? "新建条目" : "新建记忆"}
            </button>
          )}
        </div>
      </div>

      {/* Search + Filter */}
      <div style={{
        display: "flex",
        gap: 8,
        padding: "10px 16px",
        borderBottom: "1px solid var(--border-default)",
        alignItems: "center",
        flexShrink: 0,
      }}>
        <div style={{ position: "relative", flex: 1, maxWidth: 360 }}>
          <Search size={13} style={{
            position: "absolute",
            left: 8,
            top: "50%",
            transform: "translateY(-50%)",
            color: "var(--text-muted)",
          }} />
          <input
            className="input"
            placeholder="搜索关键词..."
            value={searchText}
            onChange={(e) => setSearchText(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") handleSearch(); }}
            style={{ paddingLeft: 28, height: 30, fontSize: 13 }}
          />
          {searchResults && (
            <button
              onClick={handleClearSearch}
              style={{
                position: "absolute",
                right: 6,
                top: "50%",
                transform: "translateY(-50%)",
                background: "none",
                border: "none",
                cursor: "pointer",
                color: "var(--text-muted)",
                padding: 2,
              }}
            >
              <X size={14} />
            </button>
          )}
        </div>
        {scope === "global" && (
          <div style={{ display: "flex", gap: 2 }}>
            {FILTER_TABS.map((tab) => (
              <button
                key={tab.value}
                onClick={() => { setFilter(tab.value); setActiveKb(null); setSearchResults(null); }}
                style={{
                  padding: "4px 10px",
                  fontSize: 12,
                  border: "1px solid var(--border-default)",
                  borderRadius: 4,
                  background: filter === tab.value ? "var(--accent-blue)" : "var(--bg-secondary)",
                  color: filter === tab.value ? "#fff" : "var(--text-secondary)",
                  cursor: "pointer",
                  transition: "all 0.15s",
                }}
              >
                {tab.label}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Error */}
      {error && (
        <div style={{
          padding: "8px 16px",
          background: "rgba(255, 77, 79, 0.1)",
          color: "var(--accent-red)",
          fontSize: 13,
          borderBottom: "1px solid var(--border-default)",
        }}>
          {error}
          <button onClick={() => setError(null)} style={{ float: "right", background: "none", border: "none", color: "var(--accent-red)", cursor: "pointer" }}>
            <X size={14} />
          </button>
        </div>
      )}

      {/* 库内视图的返回/删除操作条（筛选栏与列表之间：库内视图） */}
      {activeKb && (
        <div style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "6px 16px",
          borderBottom: "1px solid var(--border-default)",
          background: "var(--bg-tertiary)",
          fontSize: 12,
          color: "var(--text-secondary)",
          flexShrink: 0,
        }}>
          <button
            className="btn btn-sm"
            onClick={() => setActiveKb(null)}
            title="返回知识库总览"
          >
            <ArrowLeft size={11} />
            返回
          </button>
          <BookOpen size={12} style={{ color: "var(--accent-purple)" }} />
          <span style={{ fontWeight: 600 }}>{activeKb}</span>
          <span style={{ color: "var(--text-muted)" }}>
            {memories.length} 条
          </span>
          <div style={{ flex: 1 }} />
          {(() => {
            const cur = kbs.find((k) => k.name === activeKb);
            return cur ? (
              <button
                className="btn btn-sm"
                onClick={() => setKbDeleteConfirm(cur)}
                style={{ color: "var(--accent-red)" }}
              >
                <Trash2 size={11} />
                删除知识库
              </button>
            ) : null;
          })()}
        </div>
      )}
      {/* List + Detail */}
      <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
        <div style={{
          flex: selected ? "1 1 40%" : "1 1 100%",
          overflow: "auto",
          borderRight: selected ? "1px solid var(--border-default)" : "none",
          transition: "flex 0.2s",
        }}>
          {loading ? (
            <div style={{ display: "flex", justifyContent: "center", padding: 40 }}>
              <Spinner size={20} />
            </div>
          ) : kbView ? (
            /* ===== 知识库总览：库卡片 ===== */
            kbs.length === 0 ? (
              <div style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                justifyContent: "center",
                padding: 60,
                color: "var(--text-muted)",
                fontSize: 13,
              }}>
                <BookOpen size={32} style={{ marginBottom: 12, opacity: 0.4 }} />
                暂无知识库——点击右上角「新建知识库」，按类型积累（如 绕WAF实战技巧 / nday利用指南 / 0day库）
              </div>
            ) : (
              kbs.map((kb) => (
                <div
                  key={kb.name}
                  onClick={() => setActiveKb(kb.name)}
                  style={{
                    padding: "14px 16px",
                    borderBottom: "1px solid var(--border-default)",
                    cursor: "pointer",
                    transition: "background 0.15s",
                  }}
                  onMouseEnter={(e) => { e.currentTarget.style.background = "var(--bg-hover)"; }}
                  onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                    <BookOpen size={14} style={{ color: "var(--accent-purple)" }} />
                    <span style={{ fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>
                      {kb.name}
                    </span>
                    <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                      {kb.entry_count} 条
                    </span>
                  </div>
                  {kb.description && (
                    <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                      {kb.description}
                    </div>
                  )}
                </div>
              ))
            )
          ) : displayList.length === 0 ? (
            <div style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              padding: 60,
              color: "var(--text-muted)",
              fontSize: 13,
            }}>
              <Brain size={32} style={{ marginBottom: 12, opacity: 0.4 }} />
              {searchResults
                ? "未找到匹配的记忆"
                : activeKb
                ? "该知识库暂无条目——点击右上角「新建条目」开始积累"
                : scope === "project"
                ? "暂无项目记忆（项目中的经验教训会出现在这里）"
                : "暂无记忆条目"}
            </div>
          ) : (
            displayList.map((mem) => (
              <div
                key={mem.id}
                onClick={() => setSelected(mem)}
                style={{
                  padding: "12px 16px",
                  borderBottom: "1px solid var(--border-default)",
                  cursor: "pointer",
                  background: selected?.id === mem.id ? "var(--bg-active)" : "transparent",
                  transition: "background 0.15s",
                }}
                onMouseEnter={(e) => {
                  if (selected?.id !== mem.id) e.currentTarget.style.background = "var(--bg-hover)";
                }}
                onMouseLeave={(e) => {
                  if (selected?.id !== mem.id) e.currentTarget.style.background = "transparent";
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                  <span style={{
                    fontSize: 10,
                    padding: "1px 6px",
                    borderRadius: 3,
                    fontWeight: 600,
                    color: "#fff",
                    background: TYPE_COLORS[mem.type],
                    flexShrink: 0,
                  }}>
                    {TYPE_LABELS[mem.type]}
                  </span>
                  {mem.kb && (
                    <span style={{
                      fontSize: 10,
                      padding: "1px 6px",
                      borderRadius: 3,
                      color: "var(--accent-purple)",
                      border: "1px solid var(--accent-purple)",
                      flexShrink: 0,
                    }}>
                      {mem.kb}
                    </span>
                  )}
                  <span style={{
                    fontSize: 13,
                    fontWeight: 600,
                    color: "var(--text-primary)",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}>
                    {mem.name}
                  </span>
                  {scope === "project" && mem.project_id && (
                    <span style={{ fontSize: 10, color: "var(--text-muted)", flexShrink: 0 }}>
                      {mem.project_id.substring(0, 8)}
                    </span>
                  )}
                </div>
                <div style={{
                  fontSize: 12,
                  color: "var(--text-secondary)",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  marginBottom: 4,
                }}>
                  {mem.description}
                </div>
                {mem.trigger.length > 0 && (
                  <div style={{
                    display: "flex",
                    gap: 4,
                    flexWrap: "nowrap",
                    overflow: "hidden",
                    height: 18,
                  }}>
                    {mem.trigger.slice(0, 4).map((t, i) => (
                      <span
                        key={i}
                        style={{
                          fontSize: 10,
                          padding: "1px 5px",
                          borderRadius: 3,
                          background: "var(--bg-secondary)",
                          border: "1px solid var(--border-default)",
                          color: "var(--text-muted)",
                          flexShrink: 0,
                          whiteSpace: "nowrap",
                        }}
                      >
                        {t}
                      </span>
                    ))}
                    {mem.trigger.length > 4 && (
                      <span style={{
                        fontSize: 10,
                        color: "var(--text-muted)",
                        lineHeight: "16px",
                      }}>
                        +{mem.trigger.length - 4}
                      </span>
                    )}
                  </div>
                )}
              </div>
            ))
          )}
        </div>

        {/* Detail Panel */}
        {selected && (
          <div style={{
            flex: "1 1 60%",
            display: "flex",
            flexDirection: "column",
            overflow: "auto",
            padding: 0,
          }}>
            <div style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              padding: "12px 16px",
              borderBottom: "1px solid var(--border-default)",
            }}>
              <span style={{ fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>
                记忆详情
              </span>
              <div style={{ display: "flex", gap: 6 }}>
                <button className="btn btn-sm" onClick={() => openEdit(selected)}>
                  <Edit3 size={12} />
                  编辑
                </button>
                <button
                  className="btn btn-sm"
                  onClick={() => setDeleteConfirm(selected.id)}
                  style={{ color: "var(--accent-red)" }}
                >
                  <Trash2 size={12} />
                  删除
                </button>
                <button className="btn btn-sm" onClick={() => setSelected(null)}>
                  <X size={12} />
                </button>
              </div>
            </div>

            <div style={{ padding: 16, overflow: "auto" }}>
              {/* Name + Type */}
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
                <span style={{
                  fontSize: 16,
                  fontWeight: 700,
                  color: "var(--text-primary)",
                }}>
                  {selected.name}
                </span>
                <span style={{
                  fontSize: 10,
                  padding: "2px 8px",
                  borderRadius: 3,
                  fontWeight: 600,
                  color: "#fff",
                  background: TYPE_COLORS[selected.type],
                }}>
                  {TYPE_LABELS[selected.type]}
                </span>
                {selected.kb && (
                  <span style={{
                    fontSize: 10,
                    padding: "2px 8px",
                    borderRadius: 3,
                    color: "var(--accent-purple)",
                    border: "1px solid var(--accent-purple)",
                  }}>
                    {selected.kb}
                  </span>
                )}
              </div>

              {/* Description */}
              <div style={{ marginBottom: 12 }}>
                <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 4 }}>描述</div>
                <div style={{ fontSize: 13, color: "var(--text-secondary)" }}>
                  {selected.description}
                </div>
              </div>

              {/* Triggers */}
              {selected.trigger.length > 0 && (
                <div style={{ marginBottom: 12 }}>
                  <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 4, display: "flex", alignItems: "center", gap: 4 }}>
                    <Tag size={11} />
                    触发关键词
                  </div>
                  <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                    {selected.trigger.map((t, i) => (
                      <span key={i} style={{
                        fontSize: 11,
                        padding: "2px 8px",
                        borderRadius: 3,
                        background: "var(--bg-secondary)",
                        border: "1px solid var(--border-default)",
                        color: "var(--text-secondary)",
                      }}>
                        {t}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Content（markdown 渲染，与 Agent 消息同款） */}
              {selected.content && (
                <div style={{ marginBottom: 12 }}>
                  <div style={{ fontSize: 11, color: "var(--text-muted)", marginBottom: 4 }}>内容</div>
                  <div style={{
                    background: "var(--bg-secondary)",
                    border: "1px solid var(--border-default)",
                    borderRadius: 4,
                    padding: 10,
                    margin: 0,
                  }}>
                    <MarkdownView content={selected.content} />
                  </div>
                </div>
              )}

              {/* Metadata */}
              <div style={{ display: "flex", gap: 16, fontSize: 11, color: "var(--text-muted)" }}>
                <span>创建: {selected.created_at ? new Date(selected.created_at).toLocaleString("zh-CN") : "-"}</span>
                <span>更新: {selected.updated_at ? new Date(selected.updated_at).toLocaleString("zh-CN") : "-"}</span>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Editor Modal */}
      {showEditor && (
        <div
          className="modal-overlay"
          onClick={() => !saving && setShowEditor(false)}
        >
          <div
            className="modal-container"
            onClick={(e) => e.stopPropagation()}
            style={{
              width: 560,
              maxWidth: "90vw",
              maxHeight: "80vh",
            }}
          >
            {/* Modal Header */}
            <div style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              padding: "12px 16px",
              borderBottom: "1px solid var(--border-default)",
            }}>
              <span style={{ fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>
                {editor.id ? "编辑记忆" : editor.kb ? `新建知识库条目（${editor.kb}）` : "新建记忆"}
              </span>
              <button
                onClick={() => !saving && setShowEditor(false)}
                style={{ background: "none", border: "none", cursor: "pointer", color: "var(--text-muted)" }}
              >
                <X size={16} />
              </button>
            </div>

            {/* Modal Body */}
            <div style={{ padding: 16, overflow: "auto", flex: 1 }}>
              {/* Name */}
              <div style={{ marginBottom: 12 }}>
                <label style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 4, display: "block" }}>
                  名称 <span style={{ color: "var(--accent-red)" }}>*</span>
                </label>
                <input
                  className="input"
                  placeholder="唯一标识，如 waf-encoding-bypass"
                  value={editor.name}
                  onChange={(e) => setEditor({ ...editor, name: e.target.value })}
                  style={{ width: "100%", fontSize: 13 }}
                />
              </div>

              {/* Type（知识库条目锁定类型，仅显示归属库） */}
              {editor.kb ? (
                <div style={{ marginBottom: 12 }}>
                  <label style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 4, display: "block" }}>
                    归属知识库
                  </label>
                  <div style={{
                    fontSize: 13,
                    padding: "5px 10px",
                    borderRadius: 4,
                    background: "var(--bg-secondary)",
                    border: "1px solid var(--border-default)",
                    color: "var(--accent-purple)",
                    display: "inline-block",
                  }}>
                    {editor.kb}
                  </div>
                </div>
              ) : (
                <div style={{ marginBottom: 12 }}>
                  <label style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 4, display: "block" }}>
                    类型 <span style={{ color: "var(--accent-red)" }}>*</span>
                  </label>
                  <div style={{ display: "flex", gap: 4 }}>
                    {(["user", "feedback", "project", "reference"] as MemoryType[]).map((t) => (
                      <button
                        key={t}
                        onClick={() => setEditor({ ...editor, type: t })}
                        style={{
                          padding: "4px 12px",
                          fontSize: 12,
                          border: "1px solid var(--border-default)",
                          borderRadius: 4,
                          background: editor.type === t ? TYPE_COLORS[t] : "var(--bg-secondary)",
                          color: editor.type === t ? "#fff" : "var(--text-secondary)",
                          cursor: "pointer",
                        }}
                      >
                        {TYPE_LABELS[t]}
                      </button>
                    ))}
                  </div>
                  <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
                    想沉淀知识库条目（绕WAF技巧/nday指南等）？切到「长期记忆 → 知识库」新建库后在库内添加
                  </div>
                </div>
              )}

              {/* Description */}
              <div style={{ marginBottom: 12 }}>
                <label style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 4, display: "block" }}>
                  描述 <span style={{ color: "var(--accent-red)" }}>*</span>
                </label>
                <input
                  className="input"
                  placeholder="一句话描述，是召回时的判断依据"
                  value={editor.description}
                  onChange={(e) => setEditor({ ...editor, description: e.target.value })}
                  style={{ width: "100%", fontSize: 13 }}
                />
              </div>

              {/* Trigger */}
              <div style={{ marginBottom: 12 }}>
                <label style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 4, display: "block" }}>
                  触发关键词（逗号分隔）
                </label>
                <input
                  className="input"
                  placeholder="WAF拦截, 403 Forbidden, payload被拦"
                  value={editor.trigger}
                  onChange={(e) => setEditor({ ...editor, trigger: e.target.value })}
                  style={{ width: "100%", fontSize: 13 }}
                />
                <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 4 }}>
                  覆盖用户可能表述的同义词；中文子串匹配，英文整词匹配
                </div>
              </div>

              {/* Content */}
              <div style={{ marginBottom: 12 }}>
                <label style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 4, display: "block" }}>
                  内容
                </label>
                <textarea
                  className="input"
                  placeholder={
                    !editor.kb && (editor.type === "feedback" || editor.type === "project")
                      ? "记忆正文...\n\n**Why:** 原因\n**How to apply:** 下次怎么做\n[[关联记忆名称]]"
                      : "记忆正文..."
                  }
                  value={editor.content}
                  onChange={(e) => setEditor({ ...editor, content: e.target.value })}
                  style={{
                    width: "100%",
                    fontSize: 13,
                    minHeight: 120,
                    resize: "vertical",
                    fontFamily: "inherit",
                  }}
                />
                {!editor.kb && (editor.type === "feedback" || editor.type === "project") && (
                  <div style={{ fontSize: 11, color: "var(--accent-yellow)", marginTop: 4 }}>
                    {editor.type === "feedback" ? "反馈" : "项目"}类记忆必须包含 Why 和 How to apply
                  </div>
                )}
              </div>
            </div>

            {/* Modal Footer */}
            <div style={{
              display: "flex",
              justifyContent: "flex-end",
              gap: 8,
              padding: "12px 16px",
              borderTop: "1px solid var(--border-default)",
            }}>
              <button className="btn" onClick={() => setShowEditor(false)} disabled={saving}>
                取消
              </button>
              <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
                {saving ? <Spinner size={12} /> : <Plus size={12} />}
                {editor.id ? "更新" : "创建"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 新建知识库 Modal */}
      {showKbModal && (
        <div className="modal-overlay" onClick={() => setShowKbModal(false)}>
          <div
            className="modal-container"
            onClick={(e) => e.stopPropagation()}
            style={{ width: 420, maxWidth: "90vw" }}
          >
            <div style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              padding: "12px 16px",
              borderBottom: "1px solid var(--border-default)",
            }}>
              <span style={{ fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>
                新建知识库
              </span>
              <button
                onClick={() => setShowKbModal(false)}
                style={{ background: "none", border: "none", cursor: "pointer", color: "var(--text-muted)" }}
              >
                <X size={16} />
              </button>
            </div>
            <div style={{ padding: 16 }}>
              <div style={{ marginBottom: 12 }}>
                <label style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 4, display: "block" }}>
                  库名 <span style={{ color: "var(--accent-red)" }}>*</span>
                </label>
                <input
                  className="input"
                  placeholder="如：绕WAF实战技巧 / nday利用指南 / 0day库"
                  value={kbForm.name}
                  onChange={(e) => setKbForm({ ...kbForm, name: e.target.value })}
                  style={{ width: "100%", fontSize: 13 }}
                />
              </div>
              <div style={{ marginBottom: 12 }}>
                <label style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 4, display: "block" }}>
                  描述
                </label>
                <input
                  className="input"
                  placeholder="这个库沉淀什么类型的知识"
                  value={kbForm.description}
                  onChange={(e) => setKbForm({ ...kbForm, description: e.target.value })}
                  style={{ width: "100%", fontSize: 13 }}
                />
              </div>
              <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
                Agent 在会话中也会向知识库沉淀条目（save_memory 指定 kb），库不存在时它会自动创建
              </div>
            </div>
            <div style={{
              display: "flex",
              justifyContent: "flex-end",
              gap: 8,
              padding: "12px 16px",
              borderTop: "1px solid var(--border-default)",
            }}>
              <button className="btn" onClick={() => setShowKbModal(false)} disabled={kbSaving}>
                取消
              </button>
              <button className="btn btn-primary" onClick={handleCreateKb} disabled={kbSaving}>
                {kbSaving ? <Spinner size={12} /> : <Plus size={12} />}
                创建
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete Confirmation — 单条记忆 */}
      {deleteConfirm && (
        <div
          className="modal-overlay"
          onClick={() => setDeleteConfirm(null)}
        >
          <div
            className="modal-container"
            onClick={(e) => e.stopPropagation()}
            style={{
              padding: 20,
              width: 320,
            }}
          >
            <div style={{ fontSize: 14, fontWeight: 600, color: "var(--text-primary)", marginBottom: 8 }}>
              确认删除
            </div>
            <div style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 16 }}>
              删除后无法恢复，确定要删除这条记忆吗？
            </div>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button className="btn" onClick={() => setDeleteConfirm(null)}>
                取消
              </button>
              <button
                className="btn"
                onClick={() => handleDelete(deleteConfirm)}
                style={{ background: "var(--accent-red)", color: "#fff" }}
              >
                <Trash2 size={12} />
                删除
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete Confirmation — 整个知识库 */}
      {kbDeleteConfirm && (
        <div className="modal-overlay" onClick={() => setKbDeleteConfirm(null)}>
          <div
            className="modal-container"
            onClick={(e) => e.stopPropagation()}
            style={{ padding: 20, width: 360 }}
          >
            <div style={{ fontSize: 14, fontWeight: 600, color: "var(--text-primary)", marginBottom: 8 }}>
              删除知识库「{kbDeleteConfirm.name}」
            </div>
            <div style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 16 }}>
              将连同库内 <b style={{ color: "var(--accent-red)" }}>{kbDeleteConfirm.entry_count}</b> 条条目一起删除，无法恢复。确定继续吗？
            </div>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button className="btn" onClick={() => setKbDeleteConfirm(null)}>
                取消
              </button>
              <button
                className="btn"
                onClick={() => handleDeleteKb(kbDeleteConfirm)}
                style={{ background: "var(--accent-red)", color: "#fff" }}
              >
                <Trash2 size={12} />
                删除知识库
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
