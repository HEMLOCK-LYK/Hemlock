import { useState, useEffect, useCallback } from "react";
import {
  Plus,
  Trash2,
  Pencil,
  Users,
  Check,
  AlertCircle,
  X,
  RefreshCw,
  Eye,
  Shield,
  Swords,
  Crosshair,
  Satellite,
  FileText,
  User,
} from "lucide-react";
import { collabApi, type RoleSummary, type RoleDetail } from "../api/collab";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type ToastType = "success" | "error";
interface ToastState {
  type: ToastType;
  msg: string;
}

interface FormState {
  name: string;
  label: string;
  description: string;
  category: string;
  system_prompt: string;
}

const emptyForm: FormState = {
  name: "",
  label: "",
  description: "",
  category: "custom",
  system_prompt: "",
};

const CATEGORY_LABELS: Record<string, string> = {
  recon: "侦察",
  attack: "攻击",
  verify: "验证",
  fingerprint: "指纹",
  report: "报告",
  custom: "自定义",
};

const CATEGORY_ICONS: Record<string, React.ComponentType<{ size?: number }>> = {
  recon: Satellite,
  attack: Swords,
  verify: Shield,
  fingerprint: Crosshair,
  report: FileText,
  custom: User,
};

export function AgentRoles() {
  const [roles, setRoles] = useState<RoleSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [toast, setToast] = useState<ToastState | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [editingName, setEditingName] = useState<string | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm);
  const [saving, setSaving] = useState(false);
  const [viewingRole, setViewingRole] = useState<RoleDetail | null>(null);

  const showToast = useCallback((type: ToastType, msg: string) => {
    setToast({ type, msg });
    window.setTimeout(() => setToast(null), 2500);
  }, []);

  const loadRoles = useCallback(async () => {
    setLoading(true);
    try {
      const data = await collabApi.listRoles();
      setRoles(data);
    } catch (e) {
      showToast("error", `加载失败: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    loadRoles();
  }, [loadRoles]);

  const handleAdd = () => {
    setForm(emptyForm);
    setEditingName(null);
    setShowForm(true);
  };

  const handleEdit = async (name: string) => {
    try {
      const detail = await collabApi.getRole(name);
      setForm({
        name: detail.name,
        label: detail.label,
        description: detail.description,
        category: detail.category,
        system_prompt: detail.system_prompt,
      });
      setEditingName(name);
      setShowForm(true);
    } catch (e) {
      showToast("error", `获取角色详情失败: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const handleView = async (name: string) => {
    try {
      const detail = await collabApi.getRole(name);
      setViewingRole(detail);
    } catch (e) {
      showToast("error", `获取角色详情失败: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const handleSave = async () => {
    if (!form.label.trim() || !form.system_prompt.trim()) {
      showToast("error", "显示名和系统提示词不能为空");
      return;
    }
    if (!editingName && !form.name.trim()) {
      showToast("error", "角色标识不能为空");
      return;
    }
    setSaving(true);
    try {
      if (editingName) {
        await collabApi.updateRole(editingName, {
          label: form.label,
          description: form.description,
          category: form.category,
          system_prompt: form.system_prompt,
        });
        showToast("success", "更新成功");
      } else {
        await collabApi.createRole({
          name: form.name,
          label: form.label,
          description: form.description,
          category: form.category,
          system_prompt: form.system_prompt,
        });
        showToast("success", "创建成功");
      }
      setShowForm(false);
      setEditingName(null);
      setForm(emptyForm);
      await loadRoles();
    } catch (e) {
      showToast("error", `保存失败: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (name: string, label: string) => {
    if (!confirm(`确认删除角色 "${label}" ?`)) return;
    try {
      await collabApi.deleteRole(name);
      showToast("success", "删除成功");
      await loadRoles();
    } catch (e) {
      showToast("error", `删除失败: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const inputStyle: React.CSSProperties = {
    width: "100%",
    padding: "8px 10px",
    background: "var(--bg-tertiary)",
    border: "1px solid var(--border-default)",
    borderRadius: "var(--radius-sm)",
    color: "var(--text-primary)",
    fontSize: 13,
    outline: "none",
  };

  const labelStyle: React.CSSProperties = {
    display: "block",
    fontSize: 12,
    color: "var(--text-secondary)",
    marginBottom: 4,
  };

  return (
    <div className="flex flex-col h-full">
      <div className="view-header">
        <span className="view-title">Agent 角色管理</span>
        <div className="flex gap-2" style={{ marginLeft: "auto" }}>
          <button className="btn btn-sm" onClick={loadRoles} disabled={loading}>
            <RefreshCw size={13} className={loading ? "spin" : ""} />
            刷新
          </button>
          <button className="btn btn-sm btn-primary" onClick={handleAdd}>
            <Plus size={13} />
            新建角色
          </button>
        </div>
      </div>

      {/* Toast */}
      {toast && (
        <div
          style={{
            padding: "8px 16px",
            background:
              toast.type === "success" ? "var(--accent-green)" : "var(--accent-red)",
            color: "#fff",
            fontSize: 12,
            display: "flex",
            alignItems: "center",
            gap: 6,
          }}
        >
          {toast.type === "success" ? <Check size={14} /> : <AlertCircle size={14} />}
          {toast.msg}
        </div>
      )}

      <div className="scroll-container" style={{ padding: 16, maxWidth: 900 }}>
        {loading ? (
          <div className="empty-state">
            <Users size={40} />
            <span>加载中...</span>
          </div>
        ) : roles.length === 0 && !showForm ? (
          <div className="empty-state">
            <Users size={40} />
            <span>暂无角色</span>
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            {roles.map((role) => {
              const Icon = CATEGORY_ICONS[role.category] || User;
              return (
                <div
                  key={role.name}
                  className="card"
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 12,
                    padding: "12px 16px",
                  }}
                >
                  {/* 图标 */}
                  <div
                    style={{
                      width: 36,
                      height: 36,
                      borderRadius: "var(--radius-sm)",
                      background: "var(--bg-tertiary)",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      flexShrink: 0,
                      color: "var(--accent-blue)",
                    }}
                  >
                    <Icon size={18} />
                  </div>

                  {/* 信息 */}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="flex items-center gap-2">
                      <span style={{ fontSize: 13, fontWeight: 500, color: "var(--text-primary)" }}>
                        {role.label}
                      </span>
                      <span className="text-xs text-muted font-mono">{role.name}</span>
                      {role.is_builtin && (
                        <span
                          style={{
                            fontSize: 10,
                            padding: "1px 6px",
                            borderRadius: 999,
                            background: "var(--bg-tertiary)",
                            color: "var(--text-muted)",
                            border: "1px solid var(--border-default)",
                          }}
                        >
                          内置
                        </span>
                      )}
                      <span
                        style={{
                          fontSize: 10,
                          padding: "1px 6px",
                          borderRadius: 999,
                          background: "var(--accent-purple)",
                          color: "#fff",
                        }}
                      >
                        {CATEGORY_LABELS[role.category] || role.category}
                      </span>
                    </div>
                    <div className="text-xs text-muted" style={{ marginTop: 2 }}>
                      {role.description}
                    </div>
                  </div>

                  {/* 操作 */}
                  <div className="flex gap-2" style={{ flexShrink: 0 }}>
                    <button
                      className="btn btn-sm"
                      onClick={() => handleView(role.name)}
                      title="查看提示词"
                    >
                      <Eye size={13} />
                    </button>
                    <button
                      className="btn btn-sm"
                      onClick={() => handleEdit(role.name)}
                      title="编辑"
                    >
                      <Pencil size={13} />
                    </button>
                    {!role.is_builtin && (
                      <button
                        className="btn btn-sm btn-danger"
                        onClick={() => handleDelete(role.name, role.label)}
                        title="删除"
                      >
                        <Trash2 size={13} />
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* 添加/编辑表单 */}
        {showForm && (
          <div
            className="card"
            style={{
              marginTop: 16,
              padding: 20,
              border: "1px solid var(--accent-blue)",
            }}
          >
            <div className="flex items-center justify-between mb-4">
              <span style={{ fontSize: 14, fontWeight: 500 }}>
                {editingName ? "编辑角色" : "新建角色"}
              </span>
              <button
                className="btn btn-sm"
                onClick={() => {
                  setShowForm(false);
                  setEditingName(null);
                  setForm(emptyForm);
                }}
              >
                <X size={14} />
              </button>
            </div>

            <div className="flex flex-col gap-4">
              {/* 角色标识 */}
              <div>
                <label style={labelStyle}>
                  角色标识 {editingName && <span className="text-muted">(不可修改)</span>}
                </label>
                <input
                  style={{ ...inputStyle, opacity: editingName ? 0.5 : 1 }}
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  placeholder="如：custom-scanner"
                  disabled={!!editingName}
                />
              </div>

              {/* 显示名 */}
              <div>
                <label style={labelStyle}>显示名</label>
                <input
                  style={inputStyle}
                  value={form.label}
                  onChange={(e) => setForm({ ...form, label: e.target.value })}
                  placeholder="如：自定义扫描器"
                />
              </div>

              {/* 描述 */}
              <div>
                <label style={labelStyle}>描述</label>
                <input
                  style={inputStyle}
                  value={form.description}
                  onChange={(e) => setForm({ ...form, description: e.target.value })}
                  placeholder="一句话描述角色职责"
                />
              </div>

              {/* 分类 */}
              <div>
                <label style={labelStyle}>分类</label>
                <select
                  style={inputStyle}
                  value={form.category}
                  onChange={(e) => setForm({ ...form, category: e.target.value })}
                >
                  <option value="recon">侦察</option>
                  <option value="attack">攻击</option>
                  <option value="verify">验证</option>
                  <option value="fingerprint">指纹</option>
                  <option value="report">报告</option>
                  <option value="custom">自定义</option>
                </select>
              </div>

              {/* 系统提示词 */}
              <div>
                <label style={labelStyle}>系统提示词 (Markdown)</label>
                <textarea
                  style={{
                    ...inputStyle,
                    minHeight: 300,
                    fontFamily: "var(--font-mono)",
                    resize: "vertical",
                    lineHeight: 1.6,
                  }}
                  value={form.system_prompt}
                  onChange={(e) => setForm({ ...form, system_prompt: e.target.value })}
                  placeholder="# 角色：你的角色名\n\n## 角色定位\n描述这个角色的职责..."
                />
              </div>

              {/* 保存按钮 */}
              <div className="flex gap-2" style={{ marginTop: 4 }}>
                <button
                  className="btn btn-sm btn-primary"
                  onClick={handleSave}
                  disabled={saving}
                >
                  <Check size={13} />
                  {saving ? "保存中..." : "保存"}
                </button>
                <button
                  className="btn btn-sm"
                  onClick={() => {
                    setShowForm(false);
                    setEditingName(null);
                    setForm(emptyForm);
                  }}
                >
                  取消
                </button>
              </div>
            </div>
          </div>
        )}

        {/* 查看角色提示词（只读弹窗） */}
        {viewingRole && (
          <div
            className="modal-overlay"
            onClick={() => setViewingRole(null)}
            style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", zIndex: 1000, display: "flex", alignItems: "center", justifyContent: "center" }}
          >
            <div
              className="modal-container"
              onClick={(e) => e.stopPropagation()}
              style={{ maxWidth: 700, width: "90%", maxHeight: "80vh", display: "flex", flexDirection: "column" }}
            >
              <div className="modal-header">
                <span className="modal-header-title">
                  {viewingRole.label} <span className="text-muted font-mono text-xs">({viewingRole.name})</span>
                </span>
                <button className="modal-close-btn" onClick={() => setViewingRole(null)}>
                  <X size={16} />
                </button>
              </div>
              <div className="modal-body scroll-container" style={{ padding: 16 }}>
                <div className="agent-chat-msg-markdown">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {viewingRole.system_prompt || "(空)"}
                  </ReactMarkdown>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
