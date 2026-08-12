import client from "./client";
import type {
  InterceptStatus,
  InterceptConfig,
  InterceptPendingList,
  InterceptActionResult,
} from "../types";

/** 获取拦截器状态 */
export async function getInterceptStatus(): Promise<InterceptStatus> {
  const { data } = await client.get<InterceptStatus>("/intercept/status");
  return data;
}

/** 更新拦截开关（enabled / intercept_requests / intercept_responses，缺省不改变） */
export async function setInterceptConfig(config: InterceptConfig): Promise<InterceptStatus> {
  const { data } = await client.put<InterceptStatus>("/intercept/config", config);
  return data;
}

/** 获取当前被拦且等待处理的队列 */
export async function getPendingList(): Promise<InterceptPendingList> {
  const { data } = await client.get<InterceptPendingList>("/intercept/pending");
  return data;
}

/** 放行被拦的 flow；raw 为编辑后的完整原始包，缺省=原样放行 */
export async function forwardIntercept(id: string, raw?: string): Promise<InterceptActionResult> {
  const { data } = await client.post<InterceptActionResult>("/intercept/forward", { id, raw });
  return data;
}

/** 丢弃被拦的 flow */
export async function dropIntercept(id: string): Promise<InterceptActionResult> {
  const { data } = await client.post<InterceptActionResult>("/intercept/drop", { id });
  return data;
}
