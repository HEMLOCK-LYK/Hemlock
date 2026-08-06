import client from "./client";

// ===== 类型 =====

export interface ReconEngine {
  id: string;
  name: string;
  description: string;
  key_required: boolean;
  key_label: string;
  key_hint: string;
  doc_url: string;
  has_key: boolean;
}

export interface ReconReport {
  filename: string;
  size: number;
  size_human: string;
  modified: string;
}

export interface SearchResult {
  engines: string[];
  total_engines: number;
  errors: string[];
  report_file: string;
  report_preview: string;
  results: Record<string, unknown>;
}

/** AI 信息收集对话步骤 */
export interface ReconStep {
  type: string;
  data: unknown;
  timestamp: string;
}

/** AI 信息收集对话任务 */
export interface ReconConversation {
  id: string;
  project_id: string | null;
  goal: string;
  status: string;
  steps: ReconStep[];
  started_at: string | null;
  ended_at: string | null;
}

/** AI 对话响应 */
export interface ReconChatResponse {
  reply: string;
  tool_calls: Array<{
    tool: string;
    args: Record<string, unknown>;
    result: Record<string, unknown>;
    timestamp: string;
  }>;
}

// ===== 引擎 & Key 管理 =====

export async function getEngines(): Promise<ReconEngine[]> {
  const resp = await client.get("/recon/engines");
  return resp.data.engines ?? [];
}

export async function getKeys(): Promise<Record<string, string>> {
  const resp = await client.get("/recon/keys");
  return resp.data.keys ?? {};
}

export async function setKey(engine: string, key: string): Promise<void> {
  await client.post("/recon/keys", { engine, key });
}

export async function deleteKey(engine: string): Promise<void> {
  await client.delete(`/recon/keys/${engine}`);
}

// ===== 手动搜索 =====

export async function search(
  query: string,
  engines: string[],
  size: number = 100
): Promise<SearchResult> {
  const resp = await client.post("/recon/search", { query, engines, size });
  return resp.data;
}

// ===== 报告文件管理 =====

export async function listReports(): Promise<ReconReport[]> {
  const resp = await client.get("/recon/reports");
  return resp.data.reports ?? [];
}

export async function getReportContent(filename: string): Promise<string> {
  const resp = await client.get(`/recon/reports/${encodeURIComponent(filename)}`);
  return resp.data.content ?? "";
}

export async function deleteReport(filename: string): Promise<void> {
  await client.delete(`/recon/reports/${encodeURIComponent(filename)}`);
}

// ===== AI 信息收集助手 =====

export async function createReconConversation(
  projectId?: string | null,
  goal?: string
): Promise<ReconConversation> {
  const resp = await client.post("/recon/agent/conversations", {
    project_id: projectId ?? null,
    goal: goal ?? "",
  });
  return resp.data;
}

export async function reconChat(
  taskId: string,
  message: string,
  projectId?: string | null
): Promise<ReconChatResponse> {
  const resp = await client.post("/recon/agent/chat", {
    task_id: taskId,
    message,
    project_id: projectId ?? null,
  });
  return resp.data;
}

export async function listReconConversations(
  projectId?: string | null,
  limit: number = 50
): Promise<ReconConversation[]> {
  const resp = await client.get("/recon/agent/conversations", {
    params: { project_id: projectId ?? undefined, limit },
  });
  const tasks = resp.data ?? [];
  // 后端已过滤 [RECON] 前缀，前端去掉前缀用于显示
  return tasks.map((t: Record<string, unknown>) => ({
    id: t.id as string,
    project_id: (t.project_id as string | null) ?? null,
    goal: ((t.goal as string) || "").replace(/^\[RECON\]/, ""),
    status: (t.status as string) || "idle",
    steps: (t.steps as ReconStep[]) ?? [],
    started_at: (t.started_at as string | null) ?? null,
    ended_at: (t.ended_at as string | null) ?? null,
  }));
}

export async function getReconConversation(
  taskId: string
): Promise<ReconConversation> {
  const resp = await client.get(`/recon/agent/conversations/${taskId}`);
  return resp.data;
}

export async function deleteReconConversation(taskId: string): Promise<void> {
  await client.delete(`/recon/agent/conversations/${taskId}`);
}
