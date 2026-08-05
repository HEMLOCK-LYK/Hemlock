import { useState, useEffect, useCallback } from "react";
import {
  Save,
  Server,
  Palette,
  Info,
  Check,
  AlertCircle,
  RotateCcw,
  Sliders,
  ShieldCheck,
  Download,
  Trash2,
  ShieldAlert,
  Zap,
  Key,
} from "lucide-react";
import { settingsApi } from "../api/settings";
import type { AppSettings } from "../api/settings";
import { llmProvidersApi, type LLMProvider } from "../api/llmProviders";
import { getCertInfo, installCert, uninstallCert, getCertDownloadUrl } from "../api/proxy";
import type { CertInfo } from "../api/proxy";
import { useTheme } from "../context/ThemeContext";
import { THEME_VARS } from "../themes";
import type { ThemeName } from "../themes";
import { resetSecurityDeclaration } from "../components/SecurityDeclaration";

type ToastType = "success" | "error";

interface ToastState {
  type: ToastType;
  msg: string;
}

export function Settings() {
  const { currentTheme, themes, setTheme, setCustomTheme, resetCustomTheme } = useTheme();

  // ===== 自定义主题状态 =====
  const [showCustomTheme, setShowCustomTheme] = useState(false);
  const [customColors, setCustomColors] = useState<Record<string, string>>({});

  // 可自定义的 CSS 变量列表
  const customThemeVars: { key: string; label: string; group: string }[] = [
    { key: "--bg-primary", label: "主背景色", group: "背景" },
    { key: "--bg-secondary", label: "次背景色", group: "背景" },
    { key: "--bg-tertiary", label: "三级背景色", group: "背景" },
    { key: "--text-primary", label: "主文本色", group: "文本" },
    { key: "--text-secondary", label: "次文本色", group: "文本" },
    { key: "--text-muted", label: "弱化文本色", group: "文本" },
    { key: "--accent-blue", label: "蓝色强调", group: "强调色" },
    { key: "--accent-green", label: "绿色强调", group: "强调色" },
    { key: "--accent-red", label: "红色强调", group: "强调色" },
    { key: "--accent-yellow", label: "黄色强调", group: "强调色" },
    { key: "--accent-purple", label: "紫色强调", group: "强调色" },
    { key: "--accent-cyan", label: "青色强调", group: "强调色" },
    { key: "--border-default", label: "默认边框", group: "边框" },
  ];

  // 从当前主题读取颜色值初始化自定义面板
  const initCustomColors = useCallback(() => {
    const vars = THEME_VARS[currentTheme];
    if (!vars) return;
    const init: Record<string, string> = {};
    customThemeVars.forEach((v) => {
      init[v.key] = vars[v.key] || "";
    });
    setCustomColors(init);
  }, [currentTheme]);

  // 应用自定义颜色
  const handleApplyCustom = () => {
    const cleaned: Record<string, string> = {};
    Object.entries(customColors).forEach(([k, v]) => {
      if (v) cleaned[k] = v;
    });
    setCustomTheme(cleaned);
    showToast("success", "自定义主题已应用");
  };

  // 重置为预设主题
  const handleResetCustom = () => {
    resetCustomTheme();
    initCustomColors();
    showToast("success", "已重置为预设主题");
  };

  // ===== 加载状态 =====
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  // ===== 模型路由配置 =====
  const [modelLight, setModelLight] = useState("");
  const [modelPowerful, setModelPowerful] = useState("");
  const [routingEnabled, setRoutingEnabled] = useState(false);
  const [routingProviders, setRoutingProviders] = useState<LLMProvider[]>([]);
  const [providerLight, setProviderLight] = useState("");
  const [providerPowerful, setProviderPowerful] = useState("");

  // ===== Agent 安全与验证配置 =====
  const [llmAssistedVerify, setLlmAssistedVerify] = useState(false);
  const [sensitiveOpConfirm, setSensitiveOpConfirm] = useState(true);
  const [showRiskWarning, setShowRiskWarning] = useState(false);

  // ===== 代理设置 =====
  const [proxyHost, setProxyHost] = useState("127.0.0.1");
  const [proxyPort, setProxyPort] = useState<number>(8080);
  const [upstreamProxy, setUpstreamProxy] = useState("");
  const [apiPort, setApiPort] = useState<number>(8000);

  // ===== 证书管理 =====
  const [certInfo, setCertInfo] = useState<CertInfo | null>(null);
  const [certLoading, setCertLoading] = useState(false);
  const [certInstalling, setCertInstalling] = useState(false);
  const [certDownloading, setCertDownloading] = useState(false);

  // ===== 保存状态 =====
  const [savingRouting, setSavingRouting] = useState(false);
  const [savingProxy, setSavingProxy] = useState(false);
  const [savingAgent, setSavingAgent] = useState(false);
  const [toast, setToast] = useState<ToastState | null>(null);

  // 显示提示（自动消失）
  const showToast = useCallback((type: ToastType, msg: string) => {
    setToast({ type, msg });
    window.setTimeout(() => setToast(null), 2500);
  }, []);

  // 主题切换时同步自定义颜色面板
  useEffect(() => {
    if (showCustomTheme) {
      initCustomColors();
    }
  }, [currentTheme, showCustomTheme, initCustomColors]);

  // ===== 初始化：加载设置 =====
  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const settingsData = await settingsApi.get();
        if (cancelled) return;

        setModelLight(settingsData.llm_model_light || "");
        setModelPowerful(settingsData.llm_model_powerful || "");
        setRoutingEnabled(settingsData.model_routing_enabled ?? false);
        setProviderLight(settingsData.llm_provider_light || "");
        setProviderPowerful(settingsData.llm_provider_powerful || "");
        setLlmAssistedVerify(settingsData.llm_assisted_verify_enabled ?? false);
        setSensitiveOpConfirm(settingsData.sensitive_op_confirm ?? true);
        setProxyHost(settingsData.proxy_host || "127.0.0.1");
        setProxyPort(settingsData.proxy_port ?? 8080);
        setUpstreamProxy(settingsData.upstream_proxy || "");
        setApiPort(settingsData.api_port ?? 8000);

        setLoadError(null);
      } catch (err) {
        if (!cancelled) {
          setLoadError(err instanceof Error ? err.message : "加载设置失败");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    // 加载 Provider 列表用于模型路由下拉选择
    llmProvidersApi.list().then((resp) => {
      setRoutingProviders(resp.providers);
    }).catch(() => {});

    return () => {
      cancelled = true;
    };
  }, []);

  // ===== 保存模型路由配置 =====
  const handleSaveRouting = async () => {
    setSavingRouting(true);
    try {
      await settingsApi.update({
        llm_model_light: modelLight,
        llm_model_powerful: modelPowerful,
        llm_provider_light: providerLight,
        llm_provider_powerful: providerPowerful,
        model_routing_enabled: routingEnabled,
      } as Partial<AppSettings>);
      showToast("success", "模型路由配置已保存");
    } catch (err) {
      showToast("error", err instanceof Error ? err.message : "保存失败");
    } finally {
      setSavingRouting(false);
    }
  };

  // ===== 保存 Agent 安全与验证配置 =====
  const handleSaveAgent = async () => {
    setSavingAgent(true);
    try {
      await settingsApi.update({
        llm_assisted_verify_enabled: llmAssistedVerify,
        sensitive_op_confirm: sensitiveOpConfirm,
      } as Partial<AppSettings>);
      showToast("success", "Agent 配置已保存");
    } catch (err) {
      showToast("error", err instanceof Error ? err.message : "保存失败");
    } finally {
      setSavingAgent(false);
    }
  };

  // ===== 切换敏感操作确认开关（关闭时弹出风险提示）=====
  const handleToggleSensitiveConfirm = () => {
    if (sensitiveOpConfirm) {
      // 从开→关，弹出风险提示
      setShowRiskWarning(true);
    } else {
      // 从关→开，直接开启
      setSensitiveOpConfirm(true);
    }
  };

  const handleConfirmDisableSensitive = () => {
    setSensitiveOpConfirm(false);
    setShowRiskWarning(false);
  };

  // ===== 保存代理设置 =====
  const handleSaveProxy = async () => {
    setSavingProxy(true);
    try {
      const updated = await settingsApi.update({
        proxy_host: proxyHost,
        proxy_port: proxyPort,
        upstream_proxy: upstreamProxy,
        api_port: apiPort,
      });
      setProxyHost(updated.proxy_host);
      setProxyPort(updated.proxy_port);
      setUpstreamProxy(updated.upstream_proxy || "");
      setApiPort(updated.api_port);
      showToast("success", "代理设置已保存");
    } catch (err) {
      showToast("error", err instanceof Error ? err.message : "保存失败");
    } finally {
      setSavingProxy(false);
    }
  };

  // ===== 证书管理 =====
  const refreshCertInfo = useCallback(async () => {
    setCertLoading(true);
    try {
      const info = await getCertInfo();
      setCertInfo(info);
    } catch {
      setCertInfo(null);
    } finally {
      setCertLoading(false);
    }
  }, []);

  const handleInstallCert = async () => {
    setCertInstalling(true);
    try {
      const result = await installCert();
      if (result.success) {
        showToast("success", result.message);
      } else {
        showToast("error", result.message);
      }
    } catch (err: any) {
      // 后端返回 500 等错误时，提取有用信息
      const detail = err?.response?.data?.detail || err?.message || "安装失败";
      showToast("error", detail);
    } finally {
      // 无论成功失败都刷新证书状态（可能已安装但报错了）
      await refreshCertInfo();
      setCertInstalling(false);
    }
  };

  const handleUninstallCert = async () => {
    setCertInstalling(true);
    try {
      const result = await uninstallCert();
      if (result.success) {
        showToast("success", result.message);
      } else {
        showToast("error", result.message);
      }
    } catch (err: any) {
      const detail = err?.response?.data?.detail || err?.message || "卸载失败";
      showToast("error", detail);
    } finally {
      await refreshCertInfo();
      setCertInstalling(false);
    }
  };

  const handleDownloadCert = async () => {
    setCertDownloading(true);
    try {
      const url = getCertDownloadUrl();
      const resp = await fetch(url);
      if (!resp.ok) throw new Error("下载失败");
      const blob = await resp.blob();
      const blobUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = blobUrl;
      a.download = "mitmproxy-ca-cert.pem";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(blobUrl);
      showToast("success", "证书已下载到浏览器默认下载目录");
    } catch (err) {
      showToast("error", err instanceof Error ? err.message : "下载失败");
    } finally {
      setCertDownloading(false);
    }
  };

  useEffect(() => {
    refreshCertInfo();
  }, [refreshCertInfo]);

  // ===== 主题预览颜色（从 THEME_VARS 提取代表性色板）=====
  const getThemePreviewColors = (name: ThemeName): string[] => {
    const vars = THEME_VARS[name];
    if (!vars) return [];
    return [
      vars["--bg-primary"],
      vars["--bg-secondary"],
      vars["--accent-blue"],
      vars["--accent-green"],
      vars["--accent-red"],
    ];
  };

  // ===== 加载中 =====
  if (loading) {
    return (
      <div className="flex flex-col h-full">
        <div className="view-header">
          <span className="view-title">设置</span>
        </div>
        <div className="empty-state">
          <div className="spinner" style={{ width: 28, height: 28 }} />
          <span>正在加载设置...</span>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      <div className="view-header">
        <span className="view-title">设置</span>
      </div>

      <div className="scroll-container" style={{ padding: 16, maxWidth: 720 }}>
        {/* ===== 模型路由卡片 ===== */}
        <div className="card mb-4">
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              marginBottom: 16,
            }}
          >
            <Zap size={16} style={{ color: "var(--accent-yellow)" }} />
            <span style={{ fontWeight: 600, fontSize: 14 }}>智能模型路由</span>
          </div>

          {/* API Key 管理跳转提示 */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "8px 12px",
              marginBottom: 16,
              borderRadius: 8,
              border: "1px solid var(--border-default)",
              background: "var(--bg-tertiary)",
              fontSize: 12,
              color: "var(--text-secondary)",
            }}
          >
            <Key size={14} style={{ color: "var(--accent-blue)", flexShrink: 0 }} />
            <span>API Key 与服务商配置请前往</span>
            <a
              href="#/llm-providers"
              style={{
                color: "var(--accent-blue)",
                textDecoration: "none",
                fontWeight: 500,
              }}
            >
              API Key 管理
            </a>
            <span>页面</span>
          </div>

          <div className="text-xs text-muted" style={{ marginBottom: 12 }}>
            启用后，Agent 会根据任务复杂度自动选择已配置的 API Key：简单问答用轻量模型，复杂漏洞分析用强力模型，常规任务用标准模型（即默认 Provider）。未选择的层级回退到标准模型。
          </div>

          {/* 路由开关 */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              marginBottom: 12,
            }}
          >
            <span style={{ fontSize: 13, fontWeight: 500 }}>启用智能路由</span>
            <button
              className="btn btn-sm"
              onClick={() => setRoutingEnabled((v) => !v)}
              style={{
                background: routingEnabled
                  ? "rgba(46, 160, 67, 0.12)"
                  : "var(--bg-secondary)",
                border: routingEnabled
                  ? "1px solid var(--accent-green)"
                  : "1px solid var(--border-default)",
                color: routingEnabled
                  ? "var(--accent-green)"
                  : "var(--text-secondary)",
              }}
            >
              {routingEnabled ? "已启用" : "未启用"}
            </button>
          </div>

          {routingEnabled && (
            <>
              <div style={{ marginBottom: 8 }}>
                <label
                  className="text-xs text-muted"
                  style={{ display: "block", marginBottom: 3 }}
                >
                  轻量模型 (Light) — 简单问答/标题生成
                </label>
                <select
                  className="input w-full"
                  value={providerLight}
                  onChange={(e) => {
                    setProviderLight(e.target.value);
                    const p = routingProviders.find(p => p.id === e.target.value);
                    setModelLight(p?.model || "");
                  }}
                  style={{
                    width: "100%",
                    padding: "8px 10px",
                    background: "var(--bg-tertiary)",
                    border: "1px solid var(--border-default)",
                    borderRadius: "var(--radius-sm)",
                    color: "var(--text-primary)",
                    fontSize: 13,
                  }}
                >
                  <option value="">留空 — 回退到标准模型</option>
                  {routingProviders.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name} ({p.model})
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label
                  className="text-xs text-muted"
                  style={{ display: "block", marginBottom: 3 }}
                >
                  强力模型 (Powerful) — 漏洞分析/利用链推导
                </label>
                <select
                  className="input w-full"
                  value={providerPowerful}
                  onChange={(e) => {
                    setProviderPowerful(e.target.value);
                    const p = routingProviders.find(p => p.id === e.target.value);
                    setModelPowerful(p?.model || "");
                  }}
                  style={{
                    width: "100%",
                    padding: "8px 10px",
                    background: "var(--bg-tertiary)",
                    border: "1px solid var(--border-default)",
                    borderRadius: "var(--radius-sm)",
                    color: "var(--text-primary)",
                    fontSize: 13,
                  }}
                >
                  <option value="">留空 — 回退到标准模型</option>
                  {routingProviders.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name} ({p.model})
                    </option>
                  ))}
                </select>
              </div>
            </>
          )}

          {/* 保存按钮 */}
          <div
            style={{
              marginTop: 16,
              display: "flex",
              justifyContent: "flex-end",
            }}
          >
            <button
              className="btn btn-primary btn-sm"
              onClick={handleSaveRouting}
              disabled={savingRouting}
            >
              <Save size={12} />
              {savingRouting ? "保存中..." : "保存路由配置"}
            </button>
          </div>
        </div>

        {/* ===== Agent 安全与验证配置卡片 ===== */}
        <div className="card mb-4">
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              marginBottom: 16,
            }}
          >
            <ShieldCheck size={16} style={{ color: "var(--accent-yellow)" }} />
            <span style={{ fontWeight: 600, fontSize: 14 }}>Agent 安全与验证</span>
          </div>

          {/* LLM 辅助验证开关 */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              marginBottom: 12,
              paddingBottom: 12,
              borderBottom: "1px solid var(--border-subtle)",
            }}
          >
            <div>
              <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 3 }}>
                LLM 辅助漏洞验证
              </div>
              <div className="text-xs text-muted" style={{ maxWidth: 420 }}>
                <strong>开启</strong>：规则验证 + LLM辅助分析（混合模式，消耗额外Token，精准度高）<br />
                <strong>关闭</strong>：跳过规则验证，由 Agent AI 自行判断是否为漏洞（不消耗额外Token，依赖AI判断力）
              </div>
            </div>
            <button
              className="btn btn-sm"
              onClick={() => setLlmAssistedVerify((v) => !v)}
              style={{
                background: llmAssistedVerify
                  ? "rgba(46, 160, 67, 0.12)"
                  : "var(--bg-secondary)",
                border: llmAssistedVerify
                  ? "1px solid var(--accent-green)"
                  : "1px solid var(--border-default)",
                color: llmAssistedVerify
                  ? "var(--accent-green)"
                  : "var(--text-secondary)",
                flexShrink: 0,
              }}
            >
              {llmAssistedVerify ? "已启用" : "未启用"}
            </button>
          </div>

          {/* 敏感操作确认开关 */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              marginBottom: 12,
            }}
          >
            <div>
              <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 3 }}>
                敏感操作人工确认
              </div>
              <div className="text-xs text-muted" style={{ maxWidth: 420 }}>
                开启后，Agent 执行敏感操作（如命令执行、批量爆破、数据删除等）前需用户手动批准。关闭后所有操作自动执行。
              </div>
            </div>
            <button
              className="btn btn-sm"
              onClick={handleToggleSensitiveConfirm}
              style={{
                background: sensitiveOpConfirm
                  ? "rgba(46, 160, 67, 0.12)"
                  : "rgba(248, 81, 73, 0.12)",
                border: sensitiveOpConfirm
                  ? "1px solid var(--accent-green)"
                  : "1px solid var(--accent-red)",
                color: sensitiveOpConfirm
                  ? "var(--accent-green)"
                  : "var(--accent-red)",
                flexShrink: 0,
              }}
            >
              {sensitiveOpConfirm ? "已启用" : "已关闭"}
            </button>
          </div>

          {/* 关闭敏感确认的风险提示弹窗 */}
          {showRiskWarning && (
            <div
              style={{
                marginTop: 8,
                marginBottom: 8,
                padding: 12,
                borderRadius: 8,
                border: "1px solid var(--accent-red)",
                background: "rgba(248, 81, 73, 0.08)",
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  marginBottom: 8,
                  fontSize: 13,
                  fontWeight: 600,
                  color: "var(--accent-red)",
                }}
              >
                <ShieldAlert size={14} />
                风险警告
              </div>
              <div
                className="text-xs"
                style={{ color: "var(--text-secondary)", lineHeight: 1.7, marginBottom: 10 }}
              >
                关闭敏感操作确认后，Agent 将自动执行所有操作（包括命令执行、批量请求、数据修改等），
                <strong style={{ color: "var(--accent-red)" }}>无需用户批准</strong>。
                这可能导致：
                <br />
                • 意外的数据删除或修改
                <br />
                • 对目标系统造成不可逆的影响
                <br />
                • 触发安全告警或 WAF 封禁
                <br />
                <br />
                <strong style={{ color: "var(--accent-red)" }}>
                  关闭即代表您已知悉上述风险，并自行承担一切后果。
                </strong>
              </div>
              <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
                <button
                  className="btn btn-sm"
                  onClick={() => setShowRiskWarning(false)}
                >
                  取消
                </button>
                <button
                  className="btn btn-sm"
                  onClick={handleConfirmDisableSensitive}
                  style={{
                    background: "rgba(248, 81, 73, 0.15)",
                    border: "1px solid var(--accent-red)",
                    color: "var(--accent-red)",
                  }}
                >
                  我已知晓风险，确认关闭
                </button>
              </div>
            </div>
          )}

          {/* 保存按钮 */}
          <div
            style={{
              marginTop: 12,
              display: "flex",
              justifyContent: "flex-end",
            }}
          >
            <button
              className="btn btn-primary btn-sm"
              onClick={handleSaveAgent}
              disabled={savingAgent}
            >
              <Save size={12} />
              {savingAgent ? "保存中..." : "保存 Agent 配置"}
            </button>
          </div>
        </div>

        {/* ===== 2.2 代理设置卡片 ===== */}
        <div className="card mb-4">
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              marginBottom: 16,
            }}
          >
            <Server size={16} style={{ color: "var(--accent-blue)" }} />
            <span style={{ fontWeight: 600, fontSize: 14 }}>代理设置</span>
          </div>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr 1fr",
              gap: 12,
            }}
          >
            <div>
              <label
                className="text-xs text-muted"
                style={{ display: "block", marginBottom: 4 }}
              >
                代理监听地址
              </label>
              <input
                className="input w-full"
                value={proxyHost}
                onChange={(e) => setProxyHost(e.target.value)}
                placeholder="127.0.0.1"
              />
            </div>
            <div>
              <label
                className="text-xs text-muted"
                style={{ display: "block", marginBottom: 4 }}
              >
                代理监听端口
              </label>
              <input
                className="input w-full"
                type="number"
                value={proxyPort}
                onChange={(e) => setProxyPort(Number(e.target.value))}
                placeholder="8080"
              />
            </div>
            <div>
              <label
                className="text-xs text-muted"
                style={{ display: "block", marginBottom: 4 }}
              >
                API 服务端口
              </label>
              <input
                className="input w-full"
                type="number"
                value={apiPort}
                onChange={(e) => setApiPort(Number(e.target.value))}
                placeholder="8000"
              />
            </div>
          </div>

          {/* 上游代理 */}
          <div style={{ marginTop: 12 }}>
            <label
              className="text-xs text-muted"
              style={{ display: "block", marginBottom: 4 }}
            >
              上游代理 (Upstream Proxy)
            </label>
            <input
              className="input w-full"
              value={upstreamProxy}
              onChange={(e) => setUpstreamProxy(e.target.value)}
              placeholder="留空不使用，格式: http://host:port 或 http://user:pass@host:port"
            />
            <div className="text-xs text-muted mt-2">
              设置后，所有抓包流量将通过此代理转发。适用于级联代理或企业内网环境。需重启代理生效。
            </div>
          </div>

          <div className="text-xs text-muted mt-2">
            将浏览器/系统代理设置为「{proxyHost}:{proxyPort}」即可抓包，API
            服务监听 {apiPort} 端口
          </div>

          <div
            style={{
              marginTop: 16,
              display: "flex",
              justifyContent: "flex-end",
            }}
          >
            <button
              className="btn btn-primary btn-sm"
              onClick={handleSaveProxy}
              disabled={savingProxy}
            >
              <Save size={12} />
              {savingProxy ? "保存中..." : "保存代理设置"}
            </button>
          </div>

          {/* ===== 证书管理 ===== */}
          <div
            style={{
              marginTop: 16,
              padding: 12,
              borderRadius: 4,
              border: "1px solid var(--border-default)",
              background: "var(--bg-secondary)",
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                marginBottom: 12,
              }}
            >
              <ShieldCheck
                size={14}
                style={{
                  color: certInfo?.installed
                    ? "var(--accent-green)"
                    : "var(--accent-orange)",
                }}
              />
              <span style={{ fontWeight: 600, fontSize: 13 }}>
                HTTPS 证书管理
              </span>
              {certInfo && (
                <span
                  className="text-xs"
                  style={{
                    marginLeft: "auto",
                    padding: "2px 8px",
                    borderRadius: 3,
                    background: certInfo.installed
                      ? "rgba(63,185,80,0.15)"
                      : certInfo.available
                      ? "rgba(255,159,67,0.15)"
                      : "rgba(248,81,73,0.15)",
                    color: certInfo.installed
                      ? "var(--accent-green)"
                      : certInfo.available
                      ? "var(--accent-orange)"
                      : "var(--accent-red)",
                  }}
                >
                  {certInfo.installed
                    ? "已安装"
                    : certInfo.available
                    ? "未安装"
                    : "未生成"}
                </span>
              )}
            </div>

            <div className="text-xs text-muted" style={{ marginBottom: 12 }}>
              抓取 HTTPS 流量需要安装 mitmproxy CA 证书。点击「安装证书」自动添加到系统信任存储（需要管理员权限）。
            </div>

            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button
                className="btn btn-sm btn-primary"
                onClick={handleInstallCert}
                disabled={certInstalling || certLoading}
              >
                {certInstalling ? (
                  <Check size={12} />
                ) : (
                  <ShieldCheck size={12} />
                )}
                {certInstalling ? "处理中..." : "安装证书"}
              </button>
              <button
                className="btn btn-sm"
                onClick={handleDownloadCert}
                disabled={certDownloading || certLoading}
              >
                <Download size={12} /> {certDownloading ? "下载中..." : "下载证书"}
              </button>
              {certInfo?.installed && (
                <button
                  className="btn btn-sm"
                  onClick={handleUninstallCert}
                  disabled={certInstalling}
                  style={{ color: "var(--accent-red)" }}
                >
                  <Trash2 size={12} /> 卸载证书
                </button>
              )}
            </div>

            {certInfo && !certInfo.available && (
              <div
                style={{
                  marginTop: 8,
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "6px 10px",
                  borderRadius: 3,
                  background: "rgba(248,81,73,0.08)",
                  fontSize: 11,
                  color: "var(--accent-red)",
                }}
              >
                <ShieldAlert size={12} />
                证书尚未生成，请先在流量页面启动代理，再回来安装证书
              </div>
            )}
          </div>
        </div>

        {/* ===== 2.4 主题设置卡片 ===== */}
        <div className="card mb-4">
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              marginBottom: 16,
            }}
          >
            <Palette size={16} style={{ color: "var(--accent-cyan)" }} />
            <span style={{ fontWeight: 600, fontSize: 14 }}>主题设置</span>
          </div>

          <div className="settings-theme-grid">
            {themes.map((theme) => {
              const colors = getThemePreviewColors(theme.name);
              const active = currentTheme === theme.name;
              return (
                <div
                  key={theme.name}
                  className={`settings-theme-card ${active ? "active" : ""}`}
                  onClick={() => setTheme(theme.name)}
                >
                  <div
                    className="settings-theme-preview"
                    style={{ display: "flex" }}
                  >
                    {colors.map((c, i) => (
                      <div key={i} style={{ flex: 1, background: c }} />
                    ))}
                  </div>
                  <div
                    style={{
                      fontSize: 12,
                      fontWeight: 600,
                      color: "var(--text-primary)",
                    }}
                  >
                    {theme.label}
                  </div>
                  <div
                    className="text-xs text-muted"
                    style={{ marginTop: 2 }}
                  >
                    {theme.description}
                  </div>
                  {active && (
                    <div
                      style={{
                        marginTop: 4,
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        gap: 4,
                        color: "var(--accent-blue)",
                        fontSize: 11,
                      }}
                    >
                      <Check size={12} /> 当前
                    </div>
                  )}
                </div>
              );
            })}
          </div>
          <div className="text-xs text-muted mt-2">
            点击卡片即时切换主题，设置已持久化到本地
          </div>

          {/* 自定义主题切换按钮 */}
          <div
            style={{
              marginTop: 12,
              paddingTop: 12,
              borderTop: "1px solid var(--border-subtle)",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <span className="text-xs text-muted">
              想要更多控制？自定义主题颜色
            </span>
            <button
              className="btn btn-sm"
              onClick={() => {
                if (!showCustomTheme) initCustomColors();
                setShowCustomTheme((v) => !v);
              }}
            >
              <Sliders size={12} />
              {showCustomTheme ? "收起自定义" : "展开自定义"}
            </button>
          </div>

          {/* 自定义主题编辑面板 */}
          {showCustomTheme && (
            <div
              style={{
                marginTop: 12,
                padding: 12,
                background: "var(--bg-primary)",
                border: "1px solid var(--border-default)",
                borderRadius: "var(--radius-md)",
              }}
            >
              {Object.entries(
                customThemeVars.reduce<
                  Record<string, typeof customThemeVars>
                >((acc, v) => {
                  (acc[v.group] = acc[v.group] || []).push(v);
                  return acc;
                }, {})
              ).map(([group, vars]) => (
                <div key={group} style={{ marginBottom: 12 }}>
                  <div
                    style={{
                      fontSize: 11,
                      fontWeight: 600,
                      color: "var(--text-muted)",
                      textTransform: "uppercase",
                      letterSpacing: 0.5,
                      marginBottom: 6,
                    }}
                  >
                    {group}
                  </div>
                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))",
                      gap: 8,
                    }}
                  >
                    {vars.map((v) => (
                      <div
                        key={v.key}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 8,
                          padding: "4px 8px",
                          background: "var(--bg-tertiary)",
                          borderRadius: "var(--radius-sm)",
                          border: "1px solid var(--border-subtle)",
                        }}
                      >
                        <input
                          type="color"
                          value={customColors[v.key] || "#000000"}
                          onChange={(e) =>
                            setCustomColors((prev) => ({
                              ...prev,
                              [v.key]: e.target.value,
                            }))
                          }
                          style={{
                            width: 24,
                            height: 24,
                            border: "1px solid var(--border-default)",
                            borderRadius: "var(--radius-sm)",
                            cursor: "pointer",
                            background: "none",
                            padding: 0,
                          }}
                        />
                        <span
                          style={{
                            fontSize: 11,
                            color: "var(--text-secondary)",
                            flex: 1,
                          }}
                        >
                          {v.label}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}

              <div
                style={{
                  display: "flex",
                  gap: 8,
                  justifyContent: "flex-end",
                  marginTop: 8,
                }}
              >
                <button className="btn btn-sm" onClick={handleResetCustom}>
                  <RotateCcw size={12} />
                  重置
                </button>
                <button
                  className="btn btn-sm btn-primary"
                  onClick={handleApplyCustom}
                >
                  <Check size={12} />
                  应用自定义
                </button>
              </div>
            </div>
          )}
        </div>

        {/* ===== 2.4 关于卡片 ===== */}
        <div className="card">
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              marginBottom: 12,
            }}
          >
            <Info size={16} style={{ color: "var(--accent-green)" }} />
            <span style={{ fontWeight: 600, fontSize: 14 }}>关于 Hemlock</span>
          </div>
          <div
            className="text-xs text-secondary"
            style={{ lineHeight: 1.9 }}
          >
            <div style={{ display: "flex", gap: 8 }}>
              <span className="text-muted" style={{ minWidth: 64 }}>
                版本
              </span>
              <span>Hemlock v0.3.0</span>
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <span className="text-muted" style={{ minWidth: 64 }}>
                作者
              </span>
              <span>IHateTheWorld</span>
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <span className="text-muted" style={{ minWidth: 64 }}>
                联系
              </span>
              <span>1878093812@qq.com</span>
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <span className="text-muted" style={{ minWidth: 64 }}>
                定位
              </span>
              <span>AI Agent + 类 BurpSuite 抓包融合安全测试工具</span>
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <span className="text-muted" style={{ minWidth: 64 }}>
                架构
              </span>
              <span>FastAPI (后端) + React (前端) + mitmproxy (抓包代理)</span>
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <span className="text-muted" style={{ minWidth: 64 }}>
                当前主题
              </span>
              <span>
                {themes.find((t) => t.name === currentTheme)?.label ??
                  currentTheme}
              </span>
            </div>
            <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
              <span className="text-muted" style={{ minWidth: 64 }}>
                安全声明
              </span>
              <button
                onClick={() => {
                  resetSecurityDeclaration();
                  showToast("success", "安全声明已重置，重启软件后生效");
                }}
                style={{
                  padding: "2px 10px",
                  fontSize: 12,
                  border: "1px solid var(--border-default)",
                  borderRadius: "var(--radius-sm)",
                  background: "transparent",
                  color: "var(--text-secondary)",
                  cursor: "pointer",
                }}
              >
                重新查看安全声明
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* ===== 成功/失败提示 Toast ===== */}
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

      {/* ===== 后端加载错误提示 ===== */}
      {loadError && (
        <div
          style={{
            position: "fixed",
            bottom: 40,
            left: "50%",
            transform: "translateX(-50%)",
            zIndex: 200,
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "8px 16px",
            borderRadius: 6,
            fontSize: 12,
            background: "var(--bg-secondary)",
            border: "1px solid var(--accent-red)",
            color: "var(--accent-red)",
          }}
        >
          <AlertCircle size={14} />
          无法从后端加载设置: {loadError}
        </div>
      )}
    </div>
  );
}
