import client from "./client";

// 应用设置
export interface AppSettings {
  llm_provider: string;
  llm_api_key: string;
  llm_base_url: string;
  llm_model: string;
  llm_model_light: string;
  llm_model_powerful: string;
  llm_provider_light: string;
  llm_provider_powerful: string;
  model_routing_enabled: boolean;
  proxy_host: string;
  proxy_port: number;
  upstream_proxy: string;
  api_port: number;
  has_api_key: boolean;
  auto_pentest: boolean;
  src_hunting: boolean;
  llm_assisted_verify_enabled: boolean;
  sensitive_op_confirm: boolean;
  browser_headed: boolean;
  compact_limit_tokens: number;
}

// LLM 服务商信息
export interface ProviderInfo {
  name: string;
  base_url: string;
}

// 设置 API
export const settingsApi = {
  // 获取当前设置
  get: async (): Promise<AppSettings> => {
    const { data } = await client.get<AppSettings>("/settings");
    return data;
  },
  // 更新设置
  update: async (payload: Partial<AppSettings>): Promise<AppSettings> => {
    const { data } = await client.patch<AppSettings>("/settings", payload);
    return data;
  },
  // 获取可用的 LLM 服务商列表
  providers: async (): Promise<{
    providers: Record<string, ProviderInfo>;
  }> => {
    const { data } = await client.get<{
      providers: Record<string, ProviderInfo>;
    }>("/models/providers");
    return data;
  },
};
