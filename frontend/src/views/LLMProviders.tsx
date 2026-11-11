import { useState, useEffect, useCallback } from "react";
import {
  Plus,
  Trash2,
  Pencil,
  Key,
  Check,
  AlertCircle,
  X,
  Star,
  RefreshCw,
} from "lucide-react";
import {
  llmProvidersApi,
  PROVIDER_TYPES,
  PROTOCOL_OPTIONS,
  type LLMProvider,
  type ProviderCreateRequest,
} from "../api/llmProviders";

type ToastType = "success" | "error";
interface ToastState {
  type: ToastType;
  msg: string;
}

interface FormState {
  name: string;
  provider: string;
  protocol: string;
  api_key: string;
  base_url: string;
  model: string;
  context_window: string;
  compact_limit: string;
  is_default: boolean;
}

const emptyForm: FormState = {
  name: "",
  provider: "custom",
  protocol: "openai",
  api_key: "",
  base_url: "",
  model: "",
  context_window: "",
  compact_limit: "",
  is_default: false,
};

export function LLMProviders() {
  const [providers, setProviders] = useState<LLMProvider[]>([]);
  const [loading, setLoading] = useState(true);
  const [toast, setToast] = useState<ToastState | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm);
  const [saving, setSaving] = useState(false);

  const showToast = useCallback((type: ToastType, msg: string) => {
    setToast({ type, msg });
    window.setTimeout(() => setToast(null), 2500);
  }, []);

  /** 当前正在编辑的 Provider（用于表单中展示脱敏 Key） */
  const editingProvider = editingId
    ? providers.find((p) => p.id === editingId) ?? null
    : null;

  const loadProviders = useCallback(async () => {
    setLoading(true);
    try {
      const data = await llmProvidersApi.list();
      setProviders(data.providers);
    } catch (e) {
      showToast("error", `加载失败: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    loadProviders();
  }, [loadProviders]);

  const handleProviderTypeChange = (providerKey: string) => {
    setForm((prev) => ({
      ...prev,
      provider: providerKey,
      // 选 Anthropic 服务商时默认走 Anthropic 原生协议；切回其他服务商恢复 OpenAI 兼容
      protocol: providerKey === "anthropic" ? "anthropic" : "openai",
    }));
  };

  const handleAdd = () => {
    setForm(emptyForm);
    setEditingId(null);
    setShowForm(true);
  };

  const handleEdit = (p: LLMProvider) => {
    setForm({
      name: p.name,
      provider: p.provider,
      protocol: p.protocol || "openai",
      api_key: "",
      base_url: p.base_url,
      model: p.model,
      context_window: p.context_window ? String(p.context_window) : "",
      compact_limit: p.compact_limit ? String(p.compact_limit) : "",
      is_default: p.is_default,
    });
    setEditingId(p.id);
    setShowForm(true);
  };

  const handleSave = async () => {
    if (!form.name.trim()) {
      showToast("error", "名称不能为空");
      return;
    }
    // 编辑时 Key 可留空 = 保持原 Key 不变（后端不传即不更新）；新增必须填
    if (!editingId && !form.api_key.trim()) {
      showToast("error", "API Key 不能为空");
      return;
    }
    const compactLimitTrim = form.compact_limit.trim();
    if (compactLimitTrim && (!Number.isFinite(Number(compactLimitTrim)) || Number(compactLimitTrim) < 16384)) {
      showToast("error", "压缩阈值最小 16384 tokens");
      return;
    }
    setSaving(true);
    try {
      const contextWindow = form.context_window.trim()
        ? Number(form.context_window)
        : editingId
        ? 0 // 编辑时清空 = 恢复自动兜底（后端 0 归一为 NULL）
        : undefined;
      const compactLimit = compactLimitTrim
        ? Number(compactLimitTrim)
        : editingId
        ? 0 // 编辑时清空 = 跟随全局设置（后端 0 归一为 NULL）
        : undefined;
      if (editingId) {
        await llmProvidersApi.update(editingId, {
          name: form.name,
          provider: form.provider,
          protocol: form.protocol,
          api_key: form.api_key.trim() || undefined,
          base_url: form.base_url,
          model: form.model,
          context_window: contextWindow,
          compact_limit: compactLimit,
          is_default: form.is_default,
        });
        showToast("success", "更新成功");
      } else {
        const payload: ProviderCreateRequest = {
          name: form.name,
          provider: form.provider,
          protocol: form.protocol,
          api_key: form.api_key,
          base_url: form.base_url,
          model: form.model,
          context_window: contextWindow,
          compact_limit: compactLimit,
          is_default: form.is_default,
        };
        await llmProvidersApi.create(payload);
        showToast("success", "创建成功");
      }
      setShowForm(false);
      setEditingId(null);
      setForm(emptyForm);
      await loadProviders();
    } catch (e) {
      showToast("error", `保存失败: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string, name: string) => {
    if (!confirm(`确认删除 "${name}" ?`)) return;
    try {
      await llmProvidersApi.delete(id);
      showToast("success", "删除成功");
      await loadProviders();
    } catch (e) {
      showToast("error", `删除失败: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const handleSetDefault = async (id: string) => {
    try {
      await llmProvidersApi.setDefault(id);
      showToast("success", "已设为默认");
      await loadProviders();
    } catch (e) {
      showToast("error", `设置失败: ${e instanceof Error ? e.message : String(e)}`);
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
        <span className="view-title">API Key 管理</span>
        <div className="flex gap-2" style={{ marginLeft: "auto" }}>
          <button className="btn btn-sm" onClick={loadProviders} disabled={loading}>
            <RefreshCw size={13} className={loading ? "spin" : ""} />
            刷新
          </button>
          <button className="btn btn-sm btn-primary" onClick={handleAdd}>
            <Plus size={13} />
            添加
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

      <div className="scroll-container" style={{ padding: 16, maxWidth: 900, margin: "0 auto", width: "100%", display: "flex", flexDirection: "column" }}>
        {loading ? (
          <div className="empty-state" style={{ flex: 1, minHeight: 300, width: "100%" }}>
            <Key size={48} />
            <span>加载中...</span>
          </div>
        ) : providers.length === 0 && !showForm ? (
          <div className="empty-state" style={{ flex: 1, minHeight: 300, width: "100%" }}>
            <Key size={48} />
            <span>暂无 API Key 配置</span>
            <span className="text-xs text-muted">点击"添加"创建第一组 API Key</span>
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            {providers.map((p) => (
              <div
                key={p.id}
                className="card"
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 12,
                  padding: "12px 16px",
                }}
              >
                {/* 默认标记 */}
                <div
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    background: p.is_default
                      ? "var(--accent-green)"
                      : "var(--border-default)",
                    flexShrink: 0,
                  }}
                  title={p.is_default ? "默认" : "非默认"}
                />

                {/* 名称 + Provider */}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="flex items-center gap-2">
                    <span style={{ fontSize: 13, fontWeight: 500, color: "var(--text-primary)" }}>
                      {p.name}
                    </span>
                    {p.is_default && (
                      <span
                        style={{
                          fontSize: 10,
                          padding: "1px 6px",
                          borderRadius: 999,
                          background: "var(--accent-green)",
                          color: "#fff",
                        }}
                      >
                        默认
                      </span>
                    )}
                  </div>
                  <div className="text-xs text-muted" style={{ marginTop: 2 }}>
                    {p.provider} · {p.protocol === "anthropic" ? "Anthropic 原生" : "OpenAI 兼容"} · {p.model} · {p.api_key_masked}
                    {p.compact_limit != null && p.compact_limit > 0 && (
                      <> · 压缩阈值 {p.compact_limit >= 1_000_000 ? `${(p.compact_limit / 1_000_000).toFixed(1).replace(/\.0$/, "")}M` : `${Math.round(p.compact_limit / 1000)}K`}</>
                    )}
                  </div>
                </div>

                {/* 操作 */}
                <div className="flex gap-2" style={{ flexShrink: 0 }}>
                  {!p.is_default && (
                    <button
                      className="btn btn-sm"
                      onClick={() => handleSetDefault(p.id)}
                      title="设为默认"
                    >
                      <Star size={13} />
                    </button>
                  )}
                  <button
                    className="btn btn-sm"
                    onClick={() => handleEdit(p)}
                    title="编辑"
                  >
                    <Pencil size={13} />
                  </button>
                  <button
                    className="btn btn-sm btn-danger"
                    onClick={() => handleDelete(p.id, p.name)}
                    title="删除"
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              </div>
            ))}
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
                {editingId ? "编辑 API Key" : "添加 API Key"}
              </span>
              <button
                className="btn btn-sm"
                onClick={() => {
                  setShowForm(false);
                  setEditingId(null);
                  setForm(emptyForm);
                }}
              >
                <X size={14} />
              </button>
            </div>

            <div className="flex flex-col gap-4">
              {/* 名称 */}
              <div>
                <label style={labelStyle}>名称</label>
                <input
                  style={inputStyle}
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  placeholder="如：DeepSeek-V3 生产环境"
                />
              </div>

              {/* Provider 预设 */}
              <div>
                <label style={labelStyle}>服务商</label>
                <select
                  style={inputStyle}
                  value={form.provider}
                  onChange={(e) => handleProviderTypeChange(e.target.value)}
                >
                  <option value="openai">OpenAI</option>
                  <option value="deepseek">DeepSeek</option>
                  <option value="anthropic">Anthropic</option>
                  <option value="gemini">Gemini</option>
                  <option value="qwen">通义千问</option>
                  <option value="kimi">Kimi</option>
                  <option value="zhipu">智谱</option>
                  <option value="baichuan">百川</option>
                  <option value="ollama">Ollama (本地)</option>
                  <option value="custom">自定义</option>
                </select>
              </div>

              {/* 接口协议 */}
              <div>
                <label style={labelStyle}>接口协议</label>
                <select
                  style={inputStyle}
                  value={form.protocol}
                  onChange={(e) => setForm({ ...form, protocol: e.target.value })}
                >
                  {PROTOCOL_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>{opt.label}</option>
                  ))}
                </select>
                <span className="text-muted" style={{ fontSize: 11, display: "block", marginTop: 4 }}>
                  绝大多数服务商和中转站用 OpenAI 兼容协议。只有当中转站明确说明是
                  Anthropic / Claude 原生格式（/v1/messages）时才选 Anthropic 原生。
                </span>
              </div>

              {/* API Key（编辑时展示脱敏值，留空即保持原 Key，无需重填） */}
              <div>
                <label style={labelStyle}>
                  API Key {editingId && <span className="text-muted">（当前 {editingProvider?.api_key_masked || "已设置"} · 留空保持不变）</span>}
                </label>
                <input
                  style={inputStyle}
                  type="password"
                  value={form.api_key}
                  onChange={(e) => setForm({ ...form, api_key: e.target.value })}
                  placeholder={editingId ? "留空保持原 Key 不变" : "sk-..."}
                />
              </div>

              {/* Base URL */}
              <div>
                <label style={labelStyle}>Base URL</label>
                <input
                  style={inputStyle}
                  value={form.base_url}
                  onChange={(e) => setForm({ ...form, base_url: e.target.value })}
                  placeholder="请填写服务商的 API Base URL"
                />
              </div>

              {/* Model */}
              <div>
                <label style={labelStyle}>默认模型</label>
                <input
                  style={inputStyle}
                  value={form.model}
                  onChange={(e) => setForm({ ...form, model: e.target.value })}
                  placeholder="请填写模型名称"
                />
              </div>

              {/* 上下文窗口 */}
              <div>
                <label style={labelStyle}>上下文窗口（tokens）</label>
                <input
                  style={inputStyle}
                  value={form.context_window}
                  onChange={(e) => setForm({ ...form, context_window: e.target.value })}
                  placeholder="如：1000000（1M）、131072（128K），留空则使用默认值"
                />
                <span className="text-muted" style={{ fontSize: 11, display: "block", marginTop: 4 }}>
                  该模型实际支持的上下文窗口：当前主流大模型普遍为 1M（100 万），旧模型可能是 128K。
                </span>
              </div>

              {/* 上下文压缩阈值（按模型设置，替代原设置页的全局阈值） */}
              <div>
                <label style={labelStyle}>上下文压缩阈值（tokens）</label>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center", marginBottom: 6 }}>
                  {([
                    { v: 200_000, label: "200K" },
                    { v: 400_000, label: "400K（推荐）" },
                    { v: 600_000, label: "600K" },
                    { v: 1_000_000, label: "1M" },
                  ] as const).map((p) => (
                    <button
                      key={p.v}
                      type="button"
                      onClick={() => setForm({ ...form, compact_limit: String(p.v) })}
                      style={{
                        padding: "3px 10px",
                        fontSize: 12,
                        border: form.compact_limit === String(p.v)
                          ? "1px solid var(--accent-blue)"
                          : "1px solid var(--border-default)",
                        borderRadius: 4,
                        background: form.compact_limit === String(p.v)
                          ? "var(--accent-blue)"
                          : "var(--bg-tertiary)",
                        color: form.compact_limit === String(p.v) ? "#fff" : "var(--text-secondary)",
                        cursor: "pointer",
                      }}
                    >
                      {p.label}
                    </button>
                  ))}
                  <input
                    style={{ ...inputStyle, width: 110, padding: "4px 8px" }}
                    type="number"
                    min={16384}
                    step={10000}
                    value={form.compact_limit}
                    onChange={(e) => setForm({ ...form, compact_limit: e.target.value })}
                    placeholder="自定义"
                    title="自定义阈值（tokens），最小 16384"
                  />
                  <button
                    type="button"
                    onClick={() => setForm({ ...form, compact_limit: "" })}
                    style={{
                      padding: "3px 10px",
                      fontSize: 12,
                      border: form.compact_limit === ""
                        ? "1px solid var(--accent-blue)"
                        : "1px solid var(--border-default)",
                      borderRadius: 4,
                      background: form.compact_limit === "" ? "var(--accent-blue)" : "var(--bg-tertiary)",
                      color: form.compact_limit === "" ? "#fff" : "var(--text-secondary)",
                      cursor: "pointer",
                    }}
                  >
                    跟随全局
                  </button>
                </div>
                <span className="text-muted" style={{ fontSize: 11, display: "block", marginTop: 4 }}>
                  对话累积超过该值才自动压缩（早期对话替换为进展摘要），实际触发不会超过上面窗口的 80%。
                  阈值越低压缩越频繁、摘要失真越多；按经验 400K 是摘要质量与累积长度的平衡点。
                  不同 Key 的窗口不同，建议按模型分别设置；「跟随全局」沿用设置页里的全局默认值（旧版本配置继续生效）。
                </span>
              </div>

              {/* 默认开关 */}
              <label
                className="flex items-center gap-2"
                style={{ cursor: "pointer", fontSize: 13 }}
              >
                <input
                  type="checkbox"
                  checked={form.is_default}
                  onChange={(e) => setForm({ ...form, is_default: e.target.checked })}
                />
                设为默认 Provider
              </label>

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
                    setEditingId(null);
                    setForm(emptyForm);
                  }}
                >
                  取消
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
