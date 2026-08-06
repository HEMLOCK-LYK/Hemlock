import { useState } from "react";
import {
  FolderPlus,
  Folder,
  Trash2,
  X,
  Shield,
  Activity,
  Bug,
  Plus,
  RefreshCw,
  AlertCircle,
  Zap,
} from "lucide-react";
import { useProject } from "../context/ProjectContext";
import { Spinner } from "../components/Common";
import type { ProjectWithStats } from "../types";

export function ProjectSelector() {
  const {
    projects,
    loading,
    error: contextError,
    selectProject,
    createProject,
    startTempProject,
    deleteProject,
    refreshProjects,
  } = useProject();

  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const [starting, setStarting] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [createError, setCreateError] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [refreshError, setRefreshError] = useState<string | null>(null);

  // 只显示已保存的项目（非临时项目）
  const savedProjects = projects.filter((p) => !p.is_temp);

  const listError = refreshError || contextError;

  // 立即开始（创建临时项目）
  const handleStartNow = async () => {
    setStarting(true);
    try {
      await startTempProject();
    } catch (e: unknown) {
      console.error("[ProjectSelector] 创建临时项目失败", e);
    } finally {
      setStarting(false);
    }
  };

  // 新建项目
  const handleCreate = async () => {
    if (!name.trim()) return;
    setCreating(true);
    setCreateError(null);
    try {
      const project = await createProject({
        name: name.trim(),
        scope: [],
      });
      setName("");
      setShowCreate(false);
      selectProject(project.id);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setCreateError(msg);
      console.error("[ProjectSelector] 创建项目失败", e);
    } finally {
      setCreating(false);
    }
  };

  // 删除项目
  const handleDelete = async () => {
    if (!confirmDelete) return;
    setDeleteError(null);
    try {
      await deleteProject(confirmDelete);
      setConfirmDelete(null);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setDeleteError(msg);
      console.error("[ProjectSelector] 删除项目失败", e);
    }
  };

  // 刷新列表
  const handleRefresh = async () => {
    setRefreshError(null);
    try {
      await refreshProjects();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      setRefreshError(msg);
    }
  };

  return (
    <div className="project-selector">
      <div className="project-selector-inner">
        {/* 品牌区 */}
        <div className="project-selector-brand">
          <Shield size={40} className="project-selector-brand-icon" />
          <h1 className="project-selector-title">Hemlock</h1>
          <p className="project-selector-subtitle">AI 安全测试平台</p>
        </div>

        {/* 主操作区：立即开始 + 新建项目 */}
        <div className="project-selector-actions">
          <button
            className="project-selector-action project-selector-action-primary"
            onClick={handleStartNow}
            disabled={starting}
          >
            {starting ? <Spinner size={20} /> : <Zap size={20} />}
            <span>立即开始</span>
            <span className="project-selector-action-desc">临时项目，退出自动清理</span>
          </button>
          <button
            className="project-selector-action"
            onClick={() => setShowCreate(true)}
          >
            <FolderPlus size={20} />
            <span>新建项目</span>
            <span className="project-selector-action-desc">保存到历史，方便后续使用</span>
          </button>
        </div>

        {/* 列表加载错误提示 */}
        {listError && (
          <div className="project-selector-error-banner">
            <AlertCircle size={14} />
            <span>加载失败：{listError}</span>
            <span className="project-selector-error-hint">
              请确认后端服务已启动 (http://127.0.0.1:9876)
            </span>
          </div>
        )}

        {/* 历史项目 */}
        <div className="project-selector-section">
          <div className="project-selector-section-header">
            <div className="project-selector-section-title">历史项目</div>
            <button
              className="btn btn-sm"
              onClick={handleRefresh}
              disabled={loading}
            >
              {loading ? <Spinner size={12} /> : <RefreshCw size={12} />}
            </button>
          </div>
          {loading && savedProjects.length === 0 ? (
            <div className="project-selector-loading">
              <Spinner size={20} />
              <span>加载中...</span>
            </div>
          ) : savedProjects.length === 0 ? (
            <div className="project-selector-empty">
              {listError
                ? "无法连接后端服务，请先启动后端"
                : "暂无历史项目"}
            </div>
          ) : (
            <div className="project-selector-list">
              {savedProjects.map((p) => (
                <ProjectCard
                  key={p.id}
                  project={p}
                  onSelect={() => selectProject(p.id)}
                  onDelete={() => setConfirmDelete(p.id)}
                />
              ))}
            </div>
          )}
        </div>
      </div>

      {/* 新建项目弹窗 */}
      {showCreate && (
        <div
          className="project-selector-modal-overlay"
          onClick={() => !creating && setShowCreate(false)}
        >
          <div
            className="project-selector-modal"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="project-selector-modal-header">
              <span>新建项目</span>
              <button
                className="project-selector-modal-close"
                onClick={() => !creating && setShowCreate(false)}
              >
                <X size={16} />
              </button>
            </div>
            <div className="project-selector-modal-body">
              <label className="project-selector-field-label">
                项目名称
              </label>
              <input
                className="input project-selector-field"
                placeholder="例如：Web 安全测试"
                value={name}
                onChange={(e) => setName(e.target.value)}
                autoFocus
                onKeyDown={(e) => {
                  if (e.key === "Enter" && name.trim() && !creating) {
                    handleCreate();
                  }
                }}
              />
              {createError && (
                <div className="project-selector-error-banner">
                  <AlertCircle size={14} />
                  <span>{createError}</span>
                </div>
              )}
            </div>
            <div className="project-selector-modal-footer">
              <button
                className="btn"
                onClick={() => setShowCreate(false)}
                disabled={creating}
              >
                取消
              </button>
              <button
                className="btn btn-primary"
                onClick={handleCreate}
                disabled={!name.trim() || creating}
              >
                {creating ? <Spinner size={12} /> : <Plus size={12} />}
                创建
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 删除确认弹窗 */}
      {confirmDelete && (
        <div
          className="project-selector-modal-overlay"
          onClick={() => setConfirmDelete(null)}
        >
          <div
            className="project-selector-modal project-selector-modal-sm"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="project-selector-modal-header">
              <span>删除项目</span>
            </div>
            <div className="project-selector-modal-body">
              确定要删除该项目吗？该操作不可恢复，项目下的所有流量和漏洞数据将被删除。
              {deleteError && (
                <div className="project-selector-error-banner" style={{ marginTop: 12 }}>
                  <AlertCircle size={14} />
                  <span>{deleteError}</span>
                </div>
              )}
            </div>
            <div className="project-selector-modal-footer">
              <button
                className="btn"
                onClick={() => setConfirmDelete(null)}
              >
                取消
              </button>
              <button className="btn btn-danger" onClick={handleDelete}>
                <Trash2 size={12} />
                删除
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ===== 子组件：项目卡片 =====

interface ProjectCardProps {
  project: ProjectWithStats;
  onSelect: () => void;
  onDelete: () => void;
}

function ProjectCard({ project, onSelect, onDelete }: ProjectCardProps) {
  return (
    <div className="project-selector-card" onClick={onSelect}>
      <div className="project-selector-card-icon">
        <Folder size={18} />
      </div>
      <div className="project-selector-card-info">
        <div className="project-selector-card-name">{project.name}</div>
        <div className="project-selector-card-meta">
          <span className="project-selector-stat">
            <Activity size={11} />
            {project.flow_count ?? 0} 个流量
          </span>
          <span className="project-selector-stat">
            <Bug size={11} />
            {project.finding_count ?? 0} 漏洞
          </span>
          {project.created_at && (
            <span className="project-selector-stat project-selector-stat-time">
              {new Date(project.created_at).toLocaleDateString("zh-CN")}
            </span>
          )}
        </div>
      </div>
      <button
        className="project-selector-card-delete"
        onClick={(e) => {
          e.stopPropagation();
          onDelete();
        }}
        title="删除项目"
      >
        <Trash2 size={14} />
      </button>
    </div>
  );
}
