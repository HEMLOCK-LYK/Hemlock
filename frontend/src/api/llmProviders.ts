import client from "./client";

// ===== 类型定义 =====

export interface LLMProvider {
  id: string;
  name: string;
  provider: string;
  protocol: string;
  base_url: string;
  model: string;
  context_window: number | null;
  is_default: boolean;
  api_key_masked: string;
}

export interface ProviderListResponse {
  providers: LLMProvider[];
  total: number;
}

export interface ProviderCreateRequest {
  name: string;
  provider: string;
  protocol?: string;
  api_key: string;
  base_url: string;
  model: string;
  context_window?: number | null;
  is_default?: boolean;
}

export interface ProviderUpdateRequest {
  name?: string;
  provider?: string;
  protocol?: string;
  api_key?: string;
  base_url?: string;
  model?: string;
  context_window?: number | null;
  is_default?: boolean;
}

// 接口协议：OpenAI 兼容（/v1/chat/completions）或 Anthropic 原生（/v1/messages）
export const PROTOCOL_OPTIONS: { value: string; label: string }[] = [
  { value: "openai", label: "OpenAI 兼容（/v1/chat/completions）" },
  { value: "anthropic", label: "Anthropic 原生（/v1/messages）" },
];

// ===== API =====

export const llmProvidersApi = {
  list: async (): Promise<ProviderListResponse> => {
    const { data } = await client.get<ProviderListResponse>("/llm-providers");
    return data;
  },

  create: async (payload: ProviderCreateRequest): Promise<LLMProvider> => {
    const { data } = await client.post<LLMProvider>("/llm-providers", payload);
    return data;
  },

  update: async (id: string, payload: ProviderUpdateRequest): Promise<LLMProvider> => {
    const { data } = await client.put<LLMProvider>(`/llm-providers/${id}`, payload);
    return data;
  },

  delete: async (id: string): Promise<void> => {
    await client.delete(`/llm-providers/${id}`);
  },

  setDefault: async (id: string): Promise<LLMProvider> => {
    const { data } = await client.post<LLMProvider>(`/llm-providers/${id}/set-default`);
    return data;
  },
};

// 常用 Provider 类型（仅用于分类，不预填 base_url 和 model，由用户自行填写）
export const PROVIDER_TYPES: string[] = [
  "openai",
  "deepseek",
  "anthropic",
  "gemini",
  "qwen",
  "kimi",
  "zhipu",
  "baichuan",
  "ollama",
  "custom",
];
