import client from "./client";
import type {
  CreateTaskRequest,
  TaskResponse,
  TaskSummary,
  Finding,
  FindingDetail,
  Evidence,
  RetestResponse,
  Strategy,
  Preset,
  SessionInfo,
  ReportRequest,
  ChatResponse,
} from "../types";

// ===== Agent Task =====

export async function createTask(req: CreateTaskRequest): Promise<TaskResponse> {
  const resp = await client.post("/agent/tasks", req);
  return resp.data;
}

export async function listTasks(
  limit = 50,
  projectId?: string
): Promise<TaskSummary[]> {
  const params: Record<string, unknown> = { limit };
  if (projectId) params.project_id = projectId;
  const resp = await client.get("/agent/tasks", { params });
  return resp.data;
}

export async function getTask(taskId: string): Promise<TaskResponse> {
  const resp = await client.get(`/agent/tasks/${taskId}`);
  return resp.data;
}

export async function cancelTask(taskId: string): Promise<void> {
  await client.post(`/agent/tasks/${taskId}/cancel`);
}

export async function approveTask(taskId: string): Promise<void> {
  await client.post(`/agent/tasks/${taskId}/approve`);
}

export async function rejectTask(taskId: string): Promise<void> {
  await client.post(`/agent/tasks/${taskId}/reject`);
}

// 恢复中断的任务（从 checkpoint 续跑，仅自主模式 failed/paused 任务）
export async function resumeTask(taskId: string): Promise<void> {
  await client.post(`/agent/tasks/${taskId}/resume`);
}

// ===== Findings =====

export async function listFindings(
  projectId?: string,
  status?: string
): Promise<Finding[]> {
  const params: Record<string, string> = {};
  if (projectId) params.project_id = projectId;
  if (status) params.status = status;
  const resp = await client.get("/findings", { params });
  return resp.data;
}

export async function getFinding(findingId: string): Promise<FindingDetail> {
  const resp = await client.get(`/findings/${findingId}`);
  return resp.data;
}

export async function updateFindingStatus(
  findingId: string,
  status: string
): Promise<Finding> {
  const resp = await client.patch(`/findings/${findingId}/status`, { status });
  return resp.data;
}

export async function getEvidence(evidenceId: string): Promise<Evidence> {
  const resp = await client.get(`/evidence/${evidenceId}`);
  return resp.data;
}

// ===== Retest =====

export async function retestFinding(findingId: string): Promise<RetestResponse> {
  const resp = await client.post(`/findings/${findingId}/retest`);
  return resp.data;
}

// ===== Strategies =====

export async function listStrategies(): Promise<Strategy[]> {
  const resp = await client.get("/strategies");
  return resp.data;
}

// ===== Presets =====

export async function listPresets(): Promise<Preset[]> {
  const resp = await client.get("/agent/presets");
  return resp.data;
}

// ===== Sessions =====

export async function getSession(sessionId: string): Promise<SessionInfo> {
  const resp = await client.get(`/sessions/${sessionId}`);
  return resp.data;
}

export async function listSessions(): Promise<Record<string, unknown>[]> {
  const resp = await client.get("/sessions");
  return resp.data;
}

// ===== Reports =====

export async function generateReportUrl(req: ReportRequest): Promise<string> {
  // 返回可直接在浏览器中打开的报告 URL
  const params = new URLSearchParams();
  if (req.project_id) params.set("project_id", req.project_id);
  if (req.task_id) params.set("task_id", req.task_id);
  params.set("fmt", req.fmt);
  return `/api/reports?${params.toString()}`;
}

export async function generateReport(req: ReportRequest): Promise<string> {
  const resp = await client.post("/reports", req, {
    transformResponse: [(data) => data],
  });
  return resp.data;
}

// ===== 对话式 Agent API =====
// ChatResponse 类型从 ../types 引入，此处定义对话任务类型与接口。

// 对话任务
export interface ConversationTask {
  id: string;
  project_id: string | null;
  goal: string;
  status: string;
  steps: any[];
  started_at: string;
  ended_at: string | null;
}

// 对话式 Agent API
export const agentChatApi = {
  // 创建对话任务
  createConversation: async (
    project_id?: string | null,
    goal?: string
  ): Promise<ConversationTask> => {
    const { data } = await client.post<ConversationTask>(
      "/agent/conversations",
      { project_id, goal }
    );
    return data;
  },
  // 发送对话消息并获取回复
  chat: async (
    task_id: string,
    message: string,
    signal?: AbortSignal,
    provider_id?: string | null
  ): Promise<ChatResponse> => {
    const { data } = await client.post<ChatResponse>(
      "/agent/chat",
      { task_id, message, provider_id: provider_id || null },
      {
        signal,
        // 对话模式一轮可能包含多次 LLM 调用与工具执行，耗时不可预估；
        // 全局 5 分钟超时会让长测试在前端表现为"发送失败/已结束"（后端其实还在跑）。
        // 这里解除超时，交由后端看门狗与用户"停止"按钮控制。
        timeout: 0,
      }
    );
    return data;
  },
  // 任务运行中插入消息：非阻塞入队，Agent 下一思考边界即时识别
  // 返回 { queued: true } 表示已入队；{ queued: false } 表示无活跃循环，需回退阻塞发送
  inject: async (
    task_id: string,
    message: string
  ): Promise<{ queued: boolean; task_id: string }> => {
    const { data } = await client.post<{ queued: boolean; task_id: string }>(
      "/agent/inject",
      { task_id, message }
    );
    return data;
  },
  // 删除对话
  deleteConversation: async (task_id: string): Promise<void> => {
    await client.delete(`/agent/conversations/${task_id}`);
  },
};

// ===== Agent API 命名空间（任务管理） =====

/**
 * agentApi — Agent 任务管理命名空间，封装任务列表与详情查询。
 * 提供给对话式界面使用的统一入口。
 */
export const agentApi = {
  listTasks,
  getTask,
  createTask,
  cancelTask,
  approveTask,
  rejectTask,
  resumeTask,
  listPresets,
};
