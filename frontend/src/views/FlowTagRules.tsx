import { useState, useEffect, useCallback, useRef } from "react";
import {
  Plus,
  Trash2,
  Edit3,
  Download,
  Upload,
  Save,
  X,
  Search,
  Tag,
  AlertCircle,
  Check,
} from "lucide-react";
import {
  getFlowTagRules,
  updateFlowTagRules,
  FLOWTAG_COLORS,
  FLOWTAG_COLOR_OPTIONS,
  FLOWTAG_SCOPE_OPTIONS,
} from "../api/flowtag";
import { Spinner, EmptyState } from "../components/Common";
import type { FlowTagRule } from "../types";

// ===== 常量 =====

const DIRTY_STORAGE_KEY = "flowtag_rules_dirty";

/** 新建规则的默认值 */
const EMPTY_RULE: FlowTagRule = {
  name: "",
  f_regex: "",
  s_regex: "",
  format: "{0}",
  color: "red",
  scope: "any",
  enabled: true,
  sensitive: false,
};

/** scope 值 -> 中文标签 */
const SCOPE_LABEL_MAP: Record<string, string> = FLOWTAG_SCOPE_OPTIONS.reduce(
  (acc, opt) => {
    acc[opt.value] = opt.label;
    return acc;
  },
  {} as Record<string, string>
);

function getScopeLabel(scope: string): string {
  return SCOPE_LABEL_MAP[scope] ?? scope;
}

/** color 值 -> 十六进制颜色 */
function getColorHex(color: string): string {
  return FLOWTAG_COLORS[color] ?? "";
}

type ToastType = "success" | "error" | "warning";

interface ToastState {
  type: ToastType;
  msg: string;
}

// ===== 组件 =====

export function FlowTagRules({ embedded = false }: { embedded?: boolean }) {
  // ----- 数据状态 -----
  const [rules, setRules] = useState<FlowTagRule[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [toast, setToast] = useState<ToastState | null>(null);

  // ----- 搜索 -----
  const [searchTerm, setSearchTerm] = useState("");

  // ----- 弹窗 -----
  const [modalOpen, setModalOpen] = useState(false);
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [formData, setFormData] = useState<FlowTagRule>(EMPTY_RULE);
  const [regexWarning, setRegexWarning] = useState<string | null>(null);

  // ----- 文件导入 ref -----
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // ===== 工具方法 =====

  const showToast = useCallback((type: ToastType, msg: string) => {
    setToast({ type, msg });
    window.setTimeout(() => setToast(null), 2500);
  }, []);

  const markDirty = useCallback(() => {
    setDirty(true);
    try {
      localStorage.setItem(DIRTY_STORAGE_KEY, "1");
    } catch {
      /* localStorage 不可用时静默忽略 */
    }
  }, []);

  const clearDirty = useCallback(() => {
    setDirty(false);
    try {
      localStorage.removeItem(DIRTY_STORAGE_KEY);
    } catch {
      /* ignore */
    }
  }, []);

  // ===== 数据加载 =====

  const loadRules = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await getFlowTagRules();
      setRules(data.rules ?? []);
    } catch (err) {
      setLoadError(
        err instanceof Error ? err.message : "加载 FlowTag 规则失败"
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadRules();
  }, [loadRules]);

  // 页面卸载时若有未保存改动，给出提示
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (dirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);

  // ===== 保存 =====

  const handleSave = useCallback(async () => {
    if (rules.length === 0) {
      showToast("warning", "当前没有规则可保存");
      return;
    }
    setSaving(true);
    try {
      const data = await updateFlowTagRules(rules);
      setRules(data.rules ?? rules);
      clearDirty();
      showToast("success", `已保存 ${rules.length} 条规则`);
    } catch (err) {
      showToast(
        "error",
        err instanceof Error ? err.message : "保存失败"
      );
    } finally {
      setSaving(false);
    }
  }, [rules, clearDirty, showToast]);

  // ===== 行内切换 =====

  const handleToggleEnabled = useCallback(
    (index: number, value: boolean) => {
      setRules((prev) =>
        prev.map((r, i) => (i === index ? { ...r, enabled: value } : r))
      );
      markDirty();
    },
    [markDirty]
  );

  const handleToggleSensitive = useCallback(
    (index: number, value: boolean) => {
      setRules((prev) =>
        prev.map((r, i) => (i === index ? { ...r, sensitive: value } : r))
      );
      markDirty();
    },
    [markDirty]
  );

  // ===== 新建 / 编辑 =====

  const openAddModal = useCallback(() => {
    setEditingIndex(null);
    setFormData({ ...EMPTY_RULE });
    setRegexWarning(null);
    setModalOpen(true);
  }, []);

  const openEditModal = useCallback((index: number) => {
    setEditingIndex(index);
    setFormData({ ...rules[index] });
    setRegexWarning(null);
    setModalOpen(true);
  }, [rules]);

  const closeModal = useCallback(() => {
    setModalOpen(false);
    setEditingIndex(null);
    setRegexWarning(null);
  }, []);

  /** 校验正则表达式（仅警告，不阻断保存） */
  const validateRegex = useCallback((pattern: string): string | null => {
    if (!pattern) return null;
    try {
      // eslint-disable-next-line no-new
      new RegExp(pattern);
      return null;
    } catch (e) {
      return `正则表达式可能无效：${e instanceof Error ? e.message : String(e)}`;
    }
  }, []);

  const handleFormChange = useCallback(
    <K extends keyof FlowTagRule>(field: K, value: FlowTagRule[K]) => {
      setFormData((prev) => ({ ...prev, [field]: value }));
      if (field === "f_regex") {
        setRegexWarning(validateRegex(value as string));
      }
    },
    [validateRegex]
  );

  const handleFormSubmit = useCallback(() => {
    const name = formData.name.trim();
    if (!name) {
      showToast("error", "请填写规则名称");
      return;
    }
    if (!formData.f_regex.trim()) {
      showToast("error", "请填写正则表达式");
      return;
    }

    // 新建时检查重名
    if (editingIndex === null) {
      const dup = rules.findIndex((r) => r.name === name);
      if (dup !== -1) {
        showToast("error", `已存在同名规则「${name}」，无法重复添加`);
        return;
      }
      setRules((prev) => [...prev, { ...formData, name }]);
      showToast("success", `已添加规则「${name}」`);
    } else {
      // 编辑时检查与其他行重名
      const dup = rules.findIndex(
        (r, i) => i !== editingIndex && r.name === name
      );
      if (dup !== -1) {
        showToast("error", `已存在同名规则「${name}」`);
        return;
      }
      setRules((prev) =>
        prev.map((r, i) => (i === editingIndex ? { ...formData, name } : r))
      );
      showToast("success", `已更新规则「${name}」`);
    }

    markDirty();
    closeModal();
  }, [formData, editingIndex, rules, showToast, markDirty, closeModal]);

  // ===== 删除 =====

  const handleDelete = useCallback(
    (index: number) => {
      const rule = rules[index];
      if (!rule) return;
      const ok = window.confirm(`确认删除规则「${rule.name}」吗？`);
      if (!ok) return;
      setRules((prev) => prev.filter((_, i) => i !== index));
      markDirty();
      showToast("success", `已删除规则「${rule.name}」`);
    },
    [rules, markDirty, showToast]
  );

  // ===== 导入 / 导出 =====

  const handleExport = useCallback(() => {
    try {
      const payload = JSON.stringify(rules, null, 2);
      const blob = new Blob([payload], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "flowtag_rules_export.json";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      showToast("success", `已导出 ${rules.length} 条规则`);
    } catch (err) {
      showToast(
        "error",
        err instanceof Error ? err.message : "导出失败"
      );
    }
  }, [rules, showToast]);

  const handleImportClick = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  const handleImportFile = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      // 重置 input value 以便重复选择同一文件
      if (fileInputRef.current) fileInputRef.current.value = "";
      if (!file) return;

      try {
        const text = await file.text();
        const parsed: unknown = JSON.parse(text);

        // 兼容数组或 { rules: [...] } 两种结构
        let imported: FlowTagRule[] = [];
        if (Array.isArray(parsed)) {
          imported = parsed as FlowTagRule[];
        } else if (
          parsed &&
          typeof parsed === "object" &&
          Array.isArray((parsed as { rules?: unknown }).rules)
        ) {
          imported = (parsed as { rules: FlowTagRule[] }).rules;
        } else {
          showToast("error", "JSON 格式不正确，应为规则数组");
          return;
        }

        // 过滤并规范化字段
        const normalized: FlowTagRule[] = imported
          .filter((r) => r && typeof r === "object")
          .map((r) => ({
            name: String(r.name ?? "").trim(),
            f_regex: String(r.f_regex ?? ""),
            s_regex: String(r.s_regex ?? ""),
            format: String(r.format ?? "{0}"),
            color: String(r.color ?? "red"),
            scope: String(r.scope ?? "any"),
            enabled: r.enabled !== false,
            sensitive: r.sensitive === true,
          }))
          .filter((r) => r.name && r.f_regex);

        if (normalized.length === 0) {
          showToast("error", "文件中未找到有效规则（需包含 name 与 f_regex）");
          return;
        }

        // 按名称合并：导入规则覆盖同名已有规则
        setRules((prev) => {
          const map = new Map<string, FlowTagRule>();
          prev.forEach((r) => map.set(r.name, r));
          let mergedCount = 0;
          let addedCount = 0;
          normalized.forEach((r) => {
            if (map.has(r.name)) mergedCount++;
            else addedCount++;
            map.set(r.name, r);
          });
          showToast(
            "success",
            `导入完成：新增 ${addedCount} 条，覆盖 ${mergedCount} 条`
          );
          return Array.from(map.values());
        });
        markDirty();
      } catch (err) {
        showToast(
          "error",
          err instanceof Error ? err.message : "导入失败：JSON 解析错误"
        );
      }
    },
    [showToast, markDirty]
  );

  // ===== 过滤后的展示列表 =====

  const filteredRules = useCallback(() => {
    if (!searchTerm.trim()) return rules;
    const kw = searchTerm.trim().toLowerCase();
    return rules.filter(
      (r) =>
        r.name.toLowerCase().includes(kw) ||
        r.f_regex.toLowerCase().includes(kw) ||
        r.scope.toLowerCase().includes(kw)
    );
  }, [rules, searchTerm]);

  const displayRules = filteredRules();

  // ===== 渲染 =====

  if (loading) {
    return (
      <div className="flex flex-col h-full">
        {!embedded && (
          <div className="view-header">
            <span className="view-title">FlowTag 规则</span>
          </div>
        )}
        <div className="empty-state">
          <Spinner size={28} />
          <span>正在加载 FlowTag 规则...</span>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* ===== 视图头部（嵌入模式下隐藏，由外层模态框提供标题）===== */}
      {!embedded && (
        <div className="view-header">
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <Tag size={15} style={{ color: "var(--accent-purple)" }} />
            <span className="view-title">FlowTag 规则</span>
            {dirty && (
              <span
                style={{
                  fontSize: 11,
                  color: "var(--accent-yellow)",
                  display: "flex",
                  alignItems: "center",
                  gap: 4,
                }}
              >
                <AlertCircle size={11} />
                未保存的改动
              </span>
            )}
            <span className="text-xs text-muted" style={{ marginLeft: 4 }}>
              共 {rules.length} 条
            </span>
          </div>

          <div className="view-actions">
            {/* 搜索框 */}
            <div
              style={{
                position: "relative",
                display: "flex",
                alignItems: "center",
              }}
            >
              <Search
                size={12}
                style={{
                  position: "absolute",
                  left: 8,
                  color: "var(--text-muted)",
                  pointerEvents: "none",
                }}
              />
              <input
                className="input"
                style={{ paddingLeft: 26, width: 180 }}
                placeholder="搜索规则..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
              />
            </div>

            {/* 隐藏的文件导入 input */}
            <input
              ref={fileInputRef}
              type="file"
              accept="application/json,.json"
              style={{ display: "none" }}
              onChange={handleImportFile}
            />

            <button
              className="btn btn-sm"
              onClick={handleImportClick}
              title="从 JSON 文件导入规则"
            >
              <Upload size={12} />
              导入
            </button>
            <button
              className="btn btn-sm"
              onClick={handleExport}
              title="导出当前规则为 JSON 文件"
              disabled={rules.length === 0}
            >
              <Download size={12} />
              导出
            </button>
            <button
              className="btn btn-sm btn-primary"
              onClick={openAddModal}
              title="新建规则"
            >
              <Plus size={12} />
              新建
            </button>
            <button
              className="btn btn-sm btn-primary"
              onClick={handleSave}
              disabled={saving || !dirty}
              title="保存全部规则到后端"
            >
              {saving ? <Spinner size={12} /> : <Save size={12} />}
              {saving ? "保存中..." : "保存"}
            </button>
          </div>
        </div>
      )}

      {/* 嵌入模式下的工具栏 */}
      {embedded && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "8px 12px",
            borderBottom: "1px solid var(--border-default)",
            background: "var(--bg-secondary)",
            gap: 8,
            flexWrap: "wrap",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <div
              style={{
                position: "relative",
                display: "flex",
                alignItems: "center",
              }}
            >
              <Search
                size={12}
                style={{
                  position: "absolute",
                  left: 8,
                  color: "var(--text-muted)",
                  pointerEvents: "none",
                }}
              />
              <input
                className="input"
                style={{ paddingLeft: 26, width: 160 }}
                placeholder="搜索规则..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
              />
            </div>
            {dirty && (
              <span
                style={{
                  fontSize: 11,
                  color: "var(--accent-yellow)",
                  display: "flex",
                  alignItems: "center",
                  gap: 4,
                }}
              >
                <AlertCircle size={11} />
                未保存
              </span>
            )}
            <span className="text-xs text-muted">
              共 {rules.length} 条
            </span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <input
              ref={fileInputRef}
              type="file"
              accept="application/json,.json"
              style={{ display: "none" }}
              onChange={handleImportFile}
            />
            <button
              className="btn btn-sm"
              onClick={handleImportClick}
              title="从 JSON 文件导入规则"
            >
              <Upload size={12} />
              导入
            </button>
            <button
              className="btn btn-sm"
              onClick={handleExport}
              title="导出当前规则为 JSON 文件"
              disabled={rules.length === 0}
            >
              <Download size={12} />
              导出
            </button>
            <button
              className="btn btn-sm btn-primary"
              onClick={openAddModal}
              title="新建规则"
            >
              <Plus size={12} />
              新建
            </button>
            <button
              className="btn btn-sm btn-primary"
              onClick={handleSave}
              disabled={saving || !dirty}
              title="保存全部规则到后端"
            >
              {saving ? <Spinner size={12} /> : <Save size={12} />}
              {saving ? "保存中" : "保存"}
            </button>
          </div>
        </div>
      )}

      {/* ===== 后端加载错误 ===== */}
      {loadError && (
        <div
          style={{
            margin: 12,
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "8px 12px",
            borderRadius: 6,
            fontSize: 12,
            background: "var(--bg-secondary)",
            border: "1px solid var(--accent-red)",
            color: "var(--accent-red)",
          }}
        >
          <AlertCircle size={14} />
          无法加载 FlowTag 规则：{loadError}
          <button
            className="btn btn-sm"
            style={{ marginLeft: "auto" }}
            onClick={loadRules}
          >
            重试
          </button>
        </div>
      )}

      {/* ===== 规则表格 ===== */}
      <div
        className="scroll-container"
        style={{ flex: 1, padding: 0, minHeight: 0, overflow: "auto" }}
      >
        {rules.length === 0 ? (
          <EmptyState
            icon={<Tag size={36} />}
            message={
              loadError
                ? "规则加载失败，请重试"
                : "暂无 FlowTag 规则，点击「新建」或「导入」开始"
            }
          />
        ) : displayRules.length === 0 ? (
          <EmptyState
            icon={<Search size={36} />}
            message="未匹配到任何规则"
          />
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th style={{ width: 50, textAlign: "center" }}>启用</th>
                <th style={{ width: 160 }}>名称</th>
                <th>正则表达式</th>
                <th style={{ width: 70, textAlign: "center" }}>颜色</th>
                <th style={{ width: 90 }}>范围</th>
                <th style={{ width: 60, textAlign: "center" }}>敏感</th>
                <th style={{ width: 90, textAlign: "center" }}>操作</th>
              </tr>
            </thead>
            <tbody>
              {displayRules.map((rule) => {
                // displayRules 是 rules 的子集，用 name 作为稳定 key
                const actualIndex = rules.findIndex(
                  (r) => r.name === rule.name
                );
                const colorHex = getColorHex(rule.color);
                return (
                  <tr key={rule.name}>
                    {/* 启用 */}
                    <td style={{ textAlign: "center" }}>
                      <input
                        type="checkbox"
                        checked={rule.enabled}
                        onChange={(e) =>
                          actualIndex !== -1 &&
                          handleToggleEnabled(actualIndex, e.target.checked)
                        }
                      />
                    </td>
                    {/* 名称 */}
                    <td
                      style={{
                        fontSize: 12,
                        fontWeight: 500,
                        color: rule.enabled
                          ? "var(--text-primary)"
                          : "var(--text-muted)",
                      }}
                    >
                      {rule.name}
                    </td>
                    {/* 正则表达式 - 截断 + tooltip */}
                    <td
                      title={rule.f_regex}
                      style={{
                        maxWidth: 320,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                        fontFamily: "var(--font-mono)",
                        fontSize: 11,
                        color: "var(--text-secondary)",
                      }}
                    >
                      {rule.f_regex}
                    </td>
                    {/* 颜色 */}
                    <td style={{ textAlign: "center" }}>
                      {colorHex ? (
                        <span
                          title={rule.color}
                          style={{
                            display: "inline-block",
                            width: 12,
                            height: 12,
                            borderRadius: "50%",
                            background: colorHex,
                            border: "1px solid rgba(0,0,0,0.2)",
                            verticalAlign: "middle",
                          }}
                        />
                      ) : (
                        <span
                          className="text-xs text-muted"
                          title="无颜色"
                        >
                          -
                        </span>
                      )}
                    </td>
                    {/* 范围 */}
                    <td style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                      {getScopeLabel(rule.scope)}
                    </td>
                    {/* 敏感（大小写敏感） */}
                    <td style={{ textAlign: "center" }}>
                      <input
                        type="checkbox"
                        checked={rule.sensitive}
                        onChange={(e) =>
                          actualIndex !== -1 &&
                          handleToggleSensitive(actualIndex, e.target.checked)
                        }
                        title="大小写敏感"
                      />
                    </td>
                    {/* 操作 */}
                    <td style={{ textAlign: "center" }}>
                      <div
                        style={{
                          display: "inline-flex",
                          gap: 4,
                        }}
                      >
                        <button
                          className="btn btn-sm"
                          onClick={() =>
                            actualIndex !== -1 && openEditModal(actualIndex)
                          }
                          title="编辑规则"
                        >
                          <Edit3 size={12} />
                        </button>
                        <button
                          className="btn btn-sm"
                          onClick={() =>
                            actualIndex !== -1 && handleDelete(actualIndex)
                          }
                          title="删除规则"
                          style={{ color: "var(--accent-red)" }}
                        >
                          <Trash2 size={12} />
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* ===== 新建 / 编辑弹窗 ===== */}
      {modalOpen && (
        <div
          className="modal-overlay"
          style={{ zIndex: 2100 }}
          onClick={closeModal}
        >
          <div
            className="modal-container"
            style={{ width: "90%", maxWidth: 560, maxHeight: "85vh" }}
            onClick={(e) => e.stopPropagation()}
          >
            {/* 弹窗头部 */}
            <div className="modal-header">
              <div className="modal-header-title">
                <Tag size={15} style={{ color: "var(--accent-purple)" }} />
                {editingIndex === null ? "新建规则" : "编辑规则"}
              </div>
              <button
                className="modal-close-btn"
                onClick={closeModal}
              >
                <X size={14} />
              </button>
            </div>

            {/* 弹窗内容 */}
            <div
              style={{
                padding: 16,
                overflowY: "auto",
                fontSize: 13,
                display: "flex",
                flexDirection: "column",
                gap: 12,
              }}
            >
              {/* 名称 */}
              <div>
                <label
                  className="text-xs text-muted"
                  style={{ display: "block", marginBottom: 4 }}
                >
                  名称 <span style={{ color: "var(--accent-red)" }}>*</span>
                </label>
                <input
                  className="input w-full"
                  value={formData.name}
                  onChange={(e) => handleFormChange("name", e.target.value)}
                  placeholder="规则名称，如 身份证号"
                />
              </div>

              {/* 正则表达式 */}
              <div>
                <label
                  className="text-xs text-muted"
                  style={{ display: "block", marginBottom: 4 }}
                >
                  正则表达式 <span style={{ color: "var(--accent-red)" }}>*</span>
                </label>
                <textarea
                  className="textarea w-full"
                  style={{ minHeight: 60, fontSize: 12 }}
                  value={formData.f_regex}
                  onChange={(e) => handleFormChange("f_regex", e.target.value)}
                  placeholder="例如：\b\d{17}[\dXx]\b"
                  rows={3}
                />
                {regexWarning && (
                  <div
                    style={{
                      marginTop: 6,
                      display: "flex",
                      alignItems: "center",
                      gap: 6,
                      padding: "6px 10px",
                      borderRadius: 4,
                      background: "rgba(210,153,34,0.1)",
                      border: "1px solid var(--accent-yellow)",
                      fontSize: 11,
                      color: "var(--accent-yellow)",
                    }}
                  >
                    <AlertCircle size={12} />
                    {regexWarning}
                    <span style={{ marginLeft: "auto", opacity: 0.7 }}>
                      仍可保存（后端校验）
                    </span>
                  </div>
                )}
              </div>

              {/* 第二正则 */}
              <div>
                <label
                  className="text-xs text-muted"
                  style={{ display: "block", marginBottom: 4 }}
                >
                  第二正则（可选）
                </label>
                <textarea
                  className="textarea w-full"
                  style={{ minHeight: 50, fontSize: 12 }}
                  value={formData.s_regex}
                  onChange={(e) => handleFormChange("s_regex", e.target.value)}
                  placeholder="用于二次过滤，留空表示不启用"
                  rows={2}
                />
              </div>

              {/* 格式化 */}
              <div>
                <label
                  className="text-xs text-muted"
                  style={{ display: "block", marginBottom: 4 }}
                >
                  格式化
                </label>
                <input
                  className="input w-full"
                  value={formData.format}
                  onChange={(e) => handleFormChange("format", e.target.value)}
                  placeholder="{0}"
                />
                <div className="text-xs text-muted mt-2">
                  使用 {"{0}"} 占位符表示匹配内容，默认 {"{0}"}
                </div>
              </div>

              {/* 颜色 + 范围 */}
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr",
                  gap: 12,
                }}
              >
                {/* 颜色 */}
                <div>
                  <label
                    className="text-xs text-muted"
                    style={{ display: "block", marginBottom: 4 }}
                  >
                    颜色
                  </label>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span
                      style={{
                        display: "inline-block",
                        width: 12,
                        height: 12,
                        borderRadius: "50%",
                        background: getColorHex(formData.color) || "transparent",
                        border: "1px solid var(--border-default)",
                        flexShrink: 0,
                      }}
                    />
                    <select
                      className="select w-full"
                      value={formData.color}
                      onChange={(e) => handleFormChange("color", e.target.value)}
                    >
                      {FLOWTAG_COLOR_OPTIONS.map((opt) => (
                        <option key={opt.value} value={opt.value}>
                          {opt.label}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>

                {/* 范围 */}
                <div>
                  <label
                    className="text-xs text-muted"
                    style={{ display: "block", marginBottom: 4 }}
                  >
                    范围
                  </label>
                  <select
                    className="select w-full"
                    value={formData.scope}
                    onChange={(e) => handleFormChange("scope", e.target.value)}
                  >
                    {FLOWTAG_SCOPE_OPTIONS.map((opt) => (
                      <option key={opt.value} value={opt.value}>
                        {opt.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              {/* 开关项 */}
              <div
                style={{
                  display: "flex",
                  gap: 24,
                  padding: "8px 0",
                  borderTop: "1px solid var(--border-subtle)",
                }}
              >
                <label
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    cursor: "pointer",
                    fontSize: 13,
                  }}
                >
                  <input
                    type="checkbox"
                    checked={formData.sensitive}
                    onChange={(e) =>
                      handleFormChange("sensitive", e.target.checked)
                    }
                  />
                  大小写敏感
                </label>
                <label
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    cursor: "pointer",
                    fontSize: 13,
                  }}
                >
                  <input
                    type="checkbox"
                    checked={formData.enabled}
                    onChange={(e) =>
                      handleFormChange("enabled", e.target.checked)
                    }
                  />
                  启用
                </label>
              </div>
            </div>

            {/* 弹窗底部按钮 */}
            <div
              className="modal-footer"
              style={{ justifyContent: "flex-end" }}
            >
              <button className="btn btn-sm" onClick={closeModal}>
                取消
              </button>
              <button
                className="btn btn-sm btn-primary"
                onClick={handleFormSubmit}
              >
                <Check size={12} />
                {editingIndex === null ? "添加" : "保存"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ===== Toast 提示 ===== */}
      {toast && (
        <div
          style={{
            position: "fixed",
            top: 16,
            right: 16,
            zIndex: 3000,
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
                : toast.type === "warning"
                ? "var(--accent-yellow)"
                : "var(--accent-red)",
            color: toast.type === "warning" ? "#000" : "#fff",
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
