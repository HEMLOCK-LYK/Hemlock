import client from "./client";

// ===== 类型定义 =====

export type MemoryType = "user" | "feedback" | "project" | "reference" | "kb";

export interface Memory {
  id: string;
  name: string;
  description: string;
  type: MemoryType;
  trigger: string[];
  content: string;
  project_id: string | null;
  kb: string | null;
  created_at: string;
  updated_at: string;
  matched_triggers?: string[];
  match_count?: number;
}

export interface KnowledgeBase {
  name: string;
  description: string;
  entry_count: number;
  updated_at: string;
}

export interface MemoryIndexItem {
  id: string;
  name: string;
  description: string;
  type: MemoryType;
  trigger: string[];
}

export interface MemoryCreateRequest {
  name: string;
  description: string;
  type: MemoryType;
  trigger?: string[];
  content?: string;
  project_id?: string | null;
  kb?: string | null;
}

export interface MemoryUpdateRequest {
  name?: string;
  description?: string;
  type?: MemoryType;
  trigger?: string[];
  content?: string;
  project_id?: string | null;
  kb?: string | null;
}

// ===== API =====

export const memoryApi = {
  list: async (
    type?: MemoryType,
    projectId?: string,
    kb?: string
  ): Promise<{ memories: Memory[]; total: number }> => {
    const params: Record<string, string> = {};
    if (type) params.type = type;
    if (projectId) params.project_id = projectId;
    if (kb) params.kb = kb;
    const { data } = await client.get<{ memories: Memory[]; total: number }>(
      "/memories",
      { params }
    );
    return data;
  },

  index: async (): Promise<{ index: MemoryIndexItem[]; total: number }> => {
    const { data } = await client.get<{ index: MemoryIndexItem[]; total: number }>(
      "/memories/index"
    );
    return data;
  },

  search: async (
    text: string,
    nResults?: number
  ): Promise<{ results: Memory[]; total: number }> => {
    const { data } = await client.post<{ results: Memory[]; total: number }>(
      "/memories/search",
      { text, n_results: nResults ?? 5 }
    );
    return data;
  },

  create: async (
    payload: MemoryCreateRequest
  ): Promise<{ id: string; name: string; message: string }> => {
    const { data } = await client.post<
      { id: string; name: string; message: string }
    >("/memories", payload);
    return data;
  },

  get: async (memoryId: string): Promise<Memory> => {
    const { data } = await client.get<Memory>(`/memories/${memoryId}`);
    return data;
  },

  update: async (
    memoryId: string,
    payload: MemoryUpdateRequest
  ): Promise<{ id: string; name: string; message: string }> => {
    const { data } = await client.put<
      { id: string; name: string; message: string }
    >(`/memories/${memoryId}`, payload);
    return data;
  },

  delete: async (memoryId: string): Promise<{ message: string }> => {
    const { data } = await client.delete<{ message: string }>(
      `/memories/${memoryId}`
    );
    return data;
  },

  // ===== 知识库 =====

  listKbs: async (): Promise<{ kbs: KnowledgeBase[]; total: number }> => {
    const { data } = await client.get<{ kbs: KnowledgeBase[]; total: number }>(
      "/memories/kb"
    );
    return data;
  },

  createKb: async (
    name: string,
    description: string
  ): Promise<KnowledgeBase & { message: string }> => {
    const { data } = await client.post<KnowledgeBase & { message: string }>(
      "/memories/kb",
      { name, description }
    );
    return data;
  },

  deleteKb: async (name: string): Promise<{ message: string }> => {
    const { data } = await client.delete<{ message: string }>(
      `/memories/kb/${encodeURIComponent(name)}`
    );
    return data;
  },
};
