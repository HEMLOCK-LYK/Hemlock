import client from "./client";
import type {
  FlowListResponse,
  Flow,
  ReplayResponse,
} from "../types";

export async function listFlows(params: {
  page?: number;
  page_size?: number;
  method?: string;
  url?: string;
  source?: string;
  status?: number;
  project_id?: string;
  hide_noise?: boolean;
  host?: string;
  tags?: string;
  search?: string;
}): Promise<FlowListResponse> {
  const { data } = await client.get<FlowListResponse>("/flows", { params });
  return data;
}

export async function getFlow(flowId: string): Promise<Flow> {
  const { data } = await client.get<Flow>(`/flows/${flowId}`);
  return data;
}

export async function replayFlow(flowId: string): Promise<ReplayResponse> {
  const { data } = await client.post<ReplayResponse>(`/flows/${flowId}/replay`);
  return data;
}

// ===== 登录态一键迁移（设计稿第一期，来源 B）=====

export interface MigrateAuthResult {
  migrated: boolean;
  error?: string;
  hint?: string;
  session_id?: string;
  flow_id?: string;
  host?: string;
  injected?: string[];
  migrated_from?: string;
  validation?: {
    valid: boolean | null;
    status: number;
    reason: string;
  };
}

/** 把流量请求头中的认证信息迁移到 AI 会话（task_id 缺省 = 项目最近任务） */
export async function migrateFlowAuth(
  flowId: string,
  taskId?: string
): Promise<MigrateAuthResult> {
  const { data } = await client.post<MigrateAuthResult>(`/flows/${flowId}/migrate-auth`, {
    task_id: taskId ?? null,
  });
  return data;
}

/** 清空指定项目的所有流量（不包括 intruder 源） */
export async function clearFlows(projectId: string): Promise<{ deleted: number; project_id: string }> {
  const { data } = await client.delete<{ deleted: number; project_id: string }>("/flows", {
    params: { project_id: projectId },
  });
  return data;
}

// ===== 杂包管理 =====

export async function getNoiseStats(projectId: string): Promise<{
  total: number;
  summary: { reason: string; count: number }[];
}> {
  const { data } = await client.get("/flows/noise/stats", {
    params: { project_id: projectId },
  });
  return data;
}

export async function listNoiseFlows(params: {
  project_id: string;
  page?: number;
  page_size?: number;
}): Promise<FlowListResponse> {
  const { data } = await client.get<FlowListResponse>("/flows/noise/list", {
    params,
  });
  return data;
}

export async function markFlowNoise(
  flowId: string,
  isNoise: boolean,
  reason: string = ""
): Promise<{ id: string; is_noise: boolean; noise_reason: string }> {
  const { data } = await client.post(`/flows/${flowId}/noise`, {
    is_noise: isNoise,
    reason,
  });
  return data;
}

// ===== 抽样排序分析 =====

export interface NoiseAnalysisItem {
  type: "url" | "host";
  key: string;
  count: number;
  is_flagged: boolean;
  url_variety?: number;
}

export async function getNoiseAnalysis(
  limit: number = 20
): Promise<{ items: NoiseAnalysisItem[] }> {
  const { data } = await client.get("/flows/noise/analysis", {
    params: { limit },
  });
  return data;
}

export interface FlaggedTargets {
  urls: { key: string; reason: string }[];
  hosts: { host: string; reason: string }[];
}

export async function getFlaggedTargets(): Promise<FlaggedTargets> {
  const { data } = await client.get("/flows/noise/flagged");
  return data;
}
