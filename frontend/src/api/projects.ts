import client from "./client";
import type {
  Project,
  ProjectWithStats,
  CreateProjectRequest,
  SaveProjectRequest,
} from "../types";

// ===== Projects =====

export async function listProjects(includeTemp = true): Promise<ProjectWithStats[]> {
  const { data } = await client.get<ProjectWithStats[]>("/projects", {
    params: { include_temp: includeTemp },
  });
  return data;
}

export async function getProject(projectId: string): Promise<ProjectWithStats> {
  const { data } = await client.get<ProjectWithStats>(`/projects/${projectId}`);
  return data;
}

export async function createProject(
  req: CreateProjectRequest
): Promise<Project> {
  const { data } = await client.post<Project>("/projects", req);
  return data;
}

export async function createTempProject(): Promise<Project> {
  const { data } = await client.post<Project>("/projects/temp");
  return data;
}

export async function saveTempProject(
  projectId: string,
  req: SaveProjectRequest
): Promise<Project> {
  const { data } = await client.post<Project>(`/projects/${projectId}/save`, req);
  return data;
}

// 更新项目信息（名称 / 授权范围）
export async function updateProject(
  projectId: string,
  req: Partial<CreateProjectRequest>
): Promise<Project> {
  const { data } = await client.patch<Project>(`/projects/${projectId}`, req);
  return data;
}

export async function deleteProject(projectId: string): Promise<void> {
  await client.delete(`/projects/${projectId}`);
}

// 设置当前活跃项目（代理层捕获的流量将关联到此项目）
export async function setActiveProject(
  projectId: string | null
): Promise<void> {
  await client.post("/projects/active", { project_id: projectId });
}
