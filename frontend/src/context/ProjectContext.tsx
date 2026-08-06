import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  type ReactNode,
} from "react";
import {
  listProjects,
  createProject as apiCreateProject,
  createTempProject as apiCreateTempProject,
  saveTempProject as apiSaveTempProject,
  deleteProject as apiDeleteProject,
  getProject as apiGetProject,
  setActiveProject as apiSetActiveProject,
} from "../api/projects";
import type {
  Project,
  ProjectWithStats,
  CreateProjectRequest,
} from "../types";

const STORAGE_KEY = "hemlock.currentProjectId";

interface ProjectContextValue {
  projects: ProjectWithStats[];
  currentProject: ProjectWithStats | null;
  currentProjectId: string | null;
  loading: boolean;
  error: string | null;
  selectProject: (id: string) => void;
  clearProject: () => void;
  refreshProjects: () => Promise<ProjectWithStats[]>;
  createProject: (req: CreateProjectRequest) => Promise<Project>;
  startTempProject: () => Promise<Project>;
  saveProject: (name: string) => Promise<Project>;
  deleteProject: (id: string) => Promise<void>;
}

const ProjectContext = createContext<ProjectContextValue | null>(null);

export function ProjectProvider({ children }: { children: ReactNode }) {
  const [projects, setProjects] = useState<ProjectWithStats[]>([]);
  const [currentProjectId, setCurrentProjectId] = useState<string | null>(
    () => {
      try {
        return localStorage.getItem(STORAGE_KEY);
      } catch {
        return null;
      }
    }
  );
  const [currentProject, setCurrentProject] = useState<ProjectWithStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // 加载项目列表（后端启动期间自动重试，保证启动动画完整展示）
  const refreshProjects = useCallback(async (): Promise<
    ProjectWithStats[]
  > => {
    const MIN_LOAD_MS = 1200;
    const MAX_RETRIES = 40; // 最多重试 40 次（覆盖后端 20 秒启动窗口）
    const RETRY_DELAY = 500;
    const startTime = Date.now();

    for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
      try {
        const data = await listProjects(true);
        setProjects(data);
        setError(null);
        // 成功：保证至少展示 MIN_LOAD_MS 的启动动画
        const elapsed = Date.now() - startTime;
        const remaining = Math.max(0, MIN_LOAD_MS - elapsed);
        setTimeout(() => setLoading(false), remaining);
        return data;
      } catch (e) {
        if (attempt < MAX_RETRIES) {
          // 后端可能还在启动，等待后重试
          console.warn(`[ProjectContext] 后端未就绪，第 ${attempt + 1} 次重试...`);
          await new Promise((r) => setTimeout(r, RETRY_DELAY));
          continue;
        }
        // 重试耗尽，真正失败
        const msg = e instanceof Error ? e.message : String(e);
        console.error("[ProjectContext] 加载项目列表失败（重试耗尽）", e);
        setError(msg);
        setProjects([]);
        setLoading(false);
        throw e;
      }
    }
    setLoading(false);
    return [];
  }, []);

  // 初始加载
  useEffect(() => {
    refreshProjects().catch(() => {});
  }, [refreshProjects]);

  // 当 currentProjectId 变化时，从列表中查找或单独请求
  // 同时通知后端设置活跃项目（后端重启后会丢失内存中的活跃项目 ID）
  useEffect(() => {
    if (!currentProjectId) {
      setCurrentProject(null);
      return;
    }
    // 通知后端设置活跃项目（代理层捕获的流量将关联到此项目）
    // 每次恢复项目时都调用，确保后端重启后也能正确关联
    apiSetActiveProject(currentProjectId).catch(() => {});
    // 先从列表找
    const found = projects.find((p) => p.id === currentProjectId);
    if (found) {
      setCurrentProject(found);
      return;
    }
    // 列表没有（可能是临时项目在刷新后消失），单独请求
    apiGetProject(currentProjectId)
      .then((p) => setCurrentProject(p))
      .catch(() => {
        // 项目不存在（可能已被清理），清除选中
        try { localStorage.removeItem(STORAGE_KEY); } catch {}
        setCurrentProjectId(null);
        setCurrentProject(null);
      });
  }, [currentProjectId, projects]);

  const selectProject = useCallback((id: string) => {
    try {
      localStorage.setItem(STORAGE_KEY, id);
    } catch {}
    setCurrentProjectId(id);
    // 通知后端设置活跃项目（代理层捕获的流量将关联到此项目）
    apiSetActiveProject(id).catch(() => {});
  }, []);

  const clearProject = useCallback(() => {
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {}
    setCurrentProjectId(null);
    setCurrentProject(null);
    // 通知后端清除活跃项目
    apiSetActiveProject(null).catch(() => {});
  }, []);

  const createProject = useCallback(
    async (req: CreateProjectRequest): Promise<Project> => {
      const project = await apiCreateProject(req);
      await refreshProjects().catch(() => {});
      return project;
    },
    [refreshProjects]
  );

  const startTempProject = useCallback(async (): Promise<Project> => {
    const project = await apiCreateTempProject();
    await refreshProjects().catch(() => {});
    try { localStorage.setItem(STORAGE_KEY, project.id); } catch {}
    setCurrentProjectId(project.id);
    // 通知后端设置活跃项目
    apiSetActiveProject(project.id).catch(() => {});
    return project;
  }, [refreshProjects]);

  const saveProject = useCallback(
    async (name: string): Promise<Project> => {
      if (!currentProjectId) throw new Error("没有当前项目");
      const project = await apiSaveTempProject(currentProjectId, { name });
      await refreshProjects().catch(() => {});
      return project;
    },
    [currentProjectId, refreshProjects]
  );

  const deleteProject = useCallback(
    async (id: string): Promise<void> => {
      await apiDeleteProject(id);
      if (currentProjectId === id) {
        clearProject();
      }
      await refreshProjects().catch(() => {});
    },
    [refreshProjects, currentProjectId, clearProject]
  );

  const value: ProjectContextValue = {
    projects,
    currentProject,
    currentProjectId,
    loading,
    error,
    selectProject,
    clearProject,
    refreshProjects,
    createProject,
    startTempProject,
    saveProject,
    deleteProject,
  };

  return (
    <ProjectContext.Provider value={value}>{children}</ProjectContext.Provider>
  );
}

export function useProject(): ProjectContextValue {
  const ctx = useContext(ProjectContext);
  if (!ctx) {
    throw new Error("useProject 必须在 ProjectProvider 内部使用");
  }
  return ctx;
}
