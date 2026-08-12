import { useState } from "react";
import { NavLink } from "react-router-dom";
import {
  Activity,
  Crosshair,
  Repeat,
  Swords,
  Bug,
  Shield,
  Settings,
  Folder,
  ChevronLeft,
  Save,
  X,
  AlertCircle,
  Zap,
  Binary,
  Brain,
  Key,
  Users,
  Radar,
  CircleStop,
} from "lucide-react";
import { useProject } from "../context/ProjectContext";
import { Spinner } from "./Common";

const navSections = [
  {
    title: "流量",
    items: [
      { to: "/flows", label: "流量列表", icon: Activity },
    ],
  },
  {
    title: "工具",
    items: [
      { to: "/intercept", label: "拦截器", icon: CircleStop },
      { to: "/repeater", label: "重放器", icon: Repeat },
      { to: "/intruder", label: "爆破器", icon: Swords },
      { to: "/encoder", label: "编码解码", icon: Binary },
    ],
  },
  {
    title: "AI",
    items: [
      { to: "/agent", label: "AI 安全测试", icon: Crosshair },
      { to: "/recon", label: "AI 资产收集", icon: Radar },
      { to: "/agent/config", label: "Agent 扩展", icon: Zap },
      { to: "/agent/roles", label: "Agent 角色", icon: Users },
      { to: "/memory", label: "记忆管理", icon: Brain },
    ],
  },
  {
    title: "漏洞",
    items: [
      { to: "/findings", label: "漏洞管理", icon: Bug },
    ],
  },
  {
    title: "系统",
    items: [
      { to: "/llm-providers", label: "API Key 管理", icon: Key },
      { to: "/settings", label: "设置", icon: Settings },
    ],
  },
];

export function Sidebar() {
  const { currentProject, clearProject, saveProject } = useProject();
  const [showSave, setShowSave] = useState(false);
  const [saveName, setSaveName] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [showSwitchPrompt, setShowSwitchPrompt] = useState(false);

  const isTemp = currentProject?.is_temp;

  // 点击项目徽章：临时项目先提示保存，正式项目直接切换
  const handleProjectClick = () => {
    if (isTemp) {
      setShowSwitchPrompt(true);
    } else {
      clearProject();
    }
  };

  const handleSave = async () => {
    if (!saveName.trim()) return;
    setSaving(true);
    setSaveError(null);
    try {
      await saveProject(saveName.trim());
      setShowSave(false);
      setSaveName("");
    } catch (e: unknown) {
      setSaveError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <aside className="sidebar">
      <div className="sidebar-logo">
        <Crosshair size={20} />
        Hemlock
      </div>

      {/* 当前项目指示器 */}
      {currentProject && (
        <div className="sidebar-project-section">
          <button
            className="sidebar-project-badge"
            onClick={handleProjectClick}
            title="点击切换项目"
          >
            <Folder size={12} />
            <span className="sidebar-project-name">
              {currentProject.name}
              {isTemp && <span className="sidebar-temp-tag">临时</span>}
            </span>
            <ChevronLeft size={12} className="sidebar-project-switch" />
          </button>
          {isTemp && (
            <button
              className="btn btn-sm btn-primary sidebar-save-btn"
              onClick={() => {
                setSaveName(currentProject.name === "临时项目" ? "" : currentProject.name);
                setShowSave(true);
              }}
              title="保存为正式项目"
            >
              <Save size={11} />
              保存项目
            </button>
          )}
        </div>
      )}

      <nav className="sidebar-nav">
        {navSections.map((section) => (
          <div key={section.title}>
            <div className="sidebar-section">{section.title}</div>
            {section.items.map((item) => {
              const Icon = item.icon;
              return (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end
                  className={({ isActive }) =>
                    `sidebar-link ${isActive ? "active" : ""}`
                  }
                >
                  <Icon />
                  {item.label}
                </NavLink>
              );
            })}
          </div>
        ))}
      </nav>
      <div className="sidebar-footer">
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <Shield size={12} />
          v0.5.0
        </div>
      </div>

      {/* 保存项目弹窗 */}
      {showSave && (
        <div className="sidebar-save-overlay" onClick={() => !saving && setShowSave(false)}>
          <div className="sidebar-save-modal" onClick={(e) => e.stopPropagation()}>
            <div className="sidebar-save-header">
              <span>保存项目</span>
              <button
                className="sidebar-save-close"
                onClick={() => !saving && setShowSave(false)}
              >
                <X size={14} />
              </button>
            </div>
            <div className="sidebar-save-body">
              <label className="sidebar-save-label">项目名称</label>
              <input
                className="input"
                placeholder="输入项目名称"
                value={saveName}
                onChange={(e) => setSaveName(e.target.value)}
                autoFocus
                onKeyDown={(e) => {
                  if (e.key === "Enter" && saveName.trim() && !saving) {
                    handleSave();
                  }
                }}
              />
              {saveError && (
                <div className="sidebar-save-error">{saveError}</div>
              )}
            </div>
            <div className="sidebar-save-footer">
              <button
                className="btn"
                onClick={() => setShowSave(false)}
                disabled={saving}
              >
                取消
              </button>
              <button
                className="btn btn-primary"
                onClick={handleSave}
                disabled={!saveName.trim() || saving}
              >
                {saving ? <Spinner size={12} /> : <Save size={12} />}
                保存
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 切换项目提示（临时项目） */}
      {showSwitchPrompt && (
        <div className="sidebar-save-overlay" onClick={() => setShowSwitchPrompt(false)}>
          <div className="sidebar-save-modal" onClick={(e) => e.stopPropagation()}>
            <div className="sidebar-save-header">
              <span>切换项目</span>
            </div>
            <div className="sidebar-save-body">
              <div style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
                <AlertCircle size={16} style={{ color: "var(--accent-yellow)", flexShrink: 0, marginTop: 2 }} />
                <div>
                  <div style={{ marginBottom: 4 }}>当前是临时项目，切换后未保存的数据将丢失。</div>
                  <div className="text-xs text-muted">是否保存后再切换？</div>
                </div>
              </div>
            </div>
            <div className="sidebar-save-footer">
              <button
                className="btn"
                onClick={() => {
                  setShowSwitchPrompt(false);
                  clearProject();
                }}
              >
                不保存
              </button>
              <button
                className="btn btn-primary"
                onClick={() => {
                  setShowSwitchPrompt(false);
                  setSaveName(currentProject?.name === "临时项目" ? "" : currentProject?.name || "");
                  setShowSave(true);
                }}
              >
                <Save size={12} />
                保存
              </button>
            </div>
          </div>
        </div>
      )}
    </aside>
  );
}
