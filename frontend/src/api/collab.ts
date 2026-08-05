import client from "./client";

// ===== 类型定义 =====

export interface RoleSummary {
  name: string;
  label: string;
  description: string;
  category: string;
  is_builtin: boolean;
}

export interface RoleDetail extends RoleSummary {
  system_prompt: string;
}

export interface RoleCreateRequest {
  name: string;
  label: string;
  description: string;
  category?: string;
  system_prompt: string;
}

export interface RoleUpdateRequest {
  label: string;
  description: string;
  category?: string;
  system_prompt: string;
}

export interface CollabTaskRequest {
  project_id?: string;
  goal: string;
  target_url: string;
  roles: string[];
  role_providers?: Record<string, string>;
}

// ===== API =====

export const collabApi = {
  // 角色管理
  listRoles: async (): Promise<RoleSummary[]> => {
    const { data } = await client.get<RoleSummary[]>("/collab/roles");
    return data;
  },

  getRole: async (name: string): Promise<RoleDetail> => {
    const { data } = await client.get<RoleDetail>(`/collab/roles/${name}`);
    return data;
  },

  createRole: async (payload: RoleCreateRequest): Promise<RoleDetail> => {
    const { data } = await client.post<RoleDetail>("/collab/roles", payload);
    return data;
  },

  updateRole: async (name: string, payload: RoleUpdateRequest): Promise<RoleDetail> => {
    const { data } = await client.put<RoleDetail>(`/collab/roles/${name}`, payload);
    return data;
  },

  deleteRole: async (name: string): Promise<void> => {
    await client.delete(`/collab/roles/${name}`);
  },

  // 协作任务
  createCollabTask: async (payload: CollabTaskRequest): Promise<unknown> => {
    const { data } = await client.post("/collab/tasks", payload);
    return data;
  },
};
