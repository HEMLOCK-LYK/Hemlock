import client from "./client";

// ===== Skill 类型 =====
// 文件方案：id = name（文件名），所有技能始终启用
export interface Skill {
  id: string; // 等同于 name（文件名）
  name: string;
  label: string;
  description: string;
  category: string;
  enabled: boolean; // 始终为 true（文件存在即启用）
  is_builtin: boolean;
  instructions_preview: string;
}

export interface SkillDetail {
  id: string;
  name: string;
  label: string;
  description: string;
  instructions: string; // 完整 Markdown 内容
  category: string;
  enabled: boolean;
  is_builtin: boolean;
}

// 创建/更新：推荐直接传 content（完整 Markdown），也兼容分离字段
export interface SkillCreateRequest {
  name: string;
  content?: string; // 完整 Markdown 内容（推荐）
  label?: string; // 兼容旧前端
  description?: string; // 兼容旧前端
  instructions?: string; // 兼容旧前端
}

export interface SkillUpdateRequest {
  content?: string; // 完整 Markdown 内容（推荐）
  label?: string; // 兼容旧前端
  description?: string; // 兼容旧前端
  instructions?: string; // 兼容旧前端
}

// ===== MCP 服务器类型 =====
export interface McpServerInfo {
  id: string;
  name: string;
  transport: string;
  enabled: boolean;
  status: string;
  tool_count: number;
  last_error: string;
  command: string;
  url: string;
}

export interface McpServerCreateRequest {
  name: string;
  transport?: string;
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  url?: string;
  enabled?: boolean;
}

export interface McpTool {
  name: string;
  description?: string;
  inputSchema?: Record<string, unknown>;
}

// ===== Skill API =====
export const skillsApi = {
  list: async (): Promise<{ skills: Skill[] }> => {
    const { data } = await client.get<{ skills: Skill[] }>("/skills");
    return data;
  },

  get: async (skillName: string): Promise<SkillDetail> => {
    const { data } = await client.get<SkillDetail>(`/skills/${skillName}`);
    return data;
  },

  create: async (
    payload: SkillCreateRequest
  ): Promise<{ id: string; name: string; message: string }> => {
    const { data } = await client.post<
      { id: string; name: string; message: string }
    >("/skills", payload);
    return data;
  },

  update: async (
    skillName: string,
    payload: SkillUpdateRequest
  ): Promise<{ message: string }> => {
    const { data } = await client.put<{ message: string }>(
      `/skills/${skillName}`,
      payload
    );
    return data;
  },

  delete: async (skillName: string): Promise<{ message: string }> => {
    const { data } = await client.delete<{ message: string }>(
      `/skills/${skillName}`
    );
    return data;
  },
};

// ===== MCP Server API =====
export const mcpApi = {
  list: async (): Promise<{ servers: McpServerInfo[] }> => {
    const { data } = await client.get<{ servers: McpServerInfo[] }>(
      "/skills/mcp/list"
    );
    return data;
  },

  create: async (
    payload: McpServerCreateRequest
  ): Promise<{ id: string; name: string; message: string }> => {
    const { data } = await client.post<
      { id: string; name: string; message: string }
    >("/skills/mcp", payload);
    return data;
  },

  connect: async (
    serverId: string
  ): Promise<{
    status: string;
    tool_count?: number;
    error?: string;
    message: string;
  }> => {
    const { data } = await client.post<
      { status: string; tool_count?: number; error?: string; message: string }
    >(`/skills/mcp/${serverId}/connect`);
    return data;
  },

  disconnect: async (
    serverId: string
  ): Promise<{ status: string; message: string }> => {
    const { data } = await client.post<
      { status: string; message: string }
    >(`/skills/mcp/${serverId}/disconnect`);
    return data;
  },

  delete: async (serverId: string): Promise<{ message: string }> => {
    const { data } = await client.delete<{ message: string }>(
      `/skills/mcp/${serverId}`
    );
    return data;
  },

  tools: async (serverId: string): Promise<{ tools: McpTool[] }> => {
    const { data } = await client.get<{ tools: McpTool[] }>(
      `/skills/mcp/${serverId}/tools`
    );
    return data;
  },
};
