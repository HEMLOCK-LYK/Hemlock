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
  api_key: string;
  base_url: string;
  model: string;
  context_window: string;
  is_default: boolean;
}

const emptyForm: FormState = {
  name: "",
  provider: "custom",
  api_key: "",
  base_url: "",
  model: "",
  context_window: "",
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
      api_key: "",
      base_url: p.base_url,
      model: p.model,
      context_window: p.context_window ? String(p.context_window) : "",
      is_default: p.is_default,
    });
    setEditingId(p.id);
    setShowForm(true);
  };

  const handleSave = async () => {
    if (!form.name.trim() || !form.api_key.trim()) {
      showToast("error", "名称和 API Key 不能为空");
      return;
    }
    setSaving(true);
    try {
      const contextWindow = form.context_window.trim()
        ? Number(form.context_window)
        : undefined;
      if (editingId) {
        await llmProvidersApi.update(editingId, {
          name: form.name,
          provider: form.provider,
          api_key: form.api_key || undefined,
          base_url: form.base_url,
          model: form.model,
          context_window: contextWindow,
          is_default: form.is_default,
        });
        showToast("success", "更新成功");
      } else {
        const payload: ProviderCreateRequest = {
          name: form.name,
          provider: form.provider,
          api_key: form.api_key,
          base_url: form.base_url,
          model: form.model,
          context_window: contextWindow,
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
                    {p.provider} · {p.model} · {p.api_key_masked}
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

              {/* API Key */}
              <div>
                <label style={labelStyle}>
                  API Key {editingId && <span className="text-muted">(留空则不修改)</span>}
                </label>
                <input
                  style={inputStyle}
                  type="password"
                  value={form.api_key}
                  onChange={(e) => setForm({ ...form, api_key: e.target.value })}
                  placeholder="sk-..."
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
                  当前主流大模型普遍为 1M（100 万），旧模型可能是 128K。此值决定 Agent
                  自动压缩触发时机（窗口的 50%）：越大对话保留原文越久、缓存命中省得越多，但上下文过大时单轮推理延迟会升高。
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
