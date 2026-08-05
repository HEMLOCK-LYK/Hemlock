import client from "./client";
import type { FlowTagRule, FlowTagRulesResponse } from "../types";

/** 获取所有 FlowTag 规则 */
export async function getFlowTagRules(): Promise<FlowTagRulesResponse> {
  const { data } = await client.get<FlowTagRulesResponse>("/flowtag/rules");
  return data;
}

/** 覆盖式保存全部 FlowTag 规则 */
export async function updateFlowTagRules(rules: FlowTagRule[]): Promise<FlowTagRulesResponse> {
  const { data } = await client.put<FlowTagRulesResponse>("/flowtag/rules", { rules });
  return data;
}

/** 获取颜色映射 */
export async function getFlowTagColors(): Promise<Record<string, string>> {
  const { data } = await client.get<Record<string, string>>("/flowtag/colors");
  return data;
}

/** 手动对指定内容执行 FlowTag 匹配 */
export async function matchFlowTag(params: {
  req_headers: string;
  req_body: string;
  resp_headers: string;
  resp_body: string;
}): Promise<{ tags: string; tag_colors: string }> {
  const { data } = await client.post<{ tags: string; tag_colors: string }>("/flowtag/match", params);
  return data;
}

/** FlowTag 规则颜色映射（与后端 COLOR_MAP 一致） */
export const FLOWTAG_COLORS: Record<string, string> = {
  red: "#f0636c",
  orange: "#e59400",
  yellow: "#d4b106",
  green: "#6aab73",
  cyan: "#17a2b8",
  blue: "#4ea1e6",
  pink: "#e83e8c",
  magenta: "#b06acb",
  gray: "#7f7f7f",
  none: "",
};

/** 颜色选项列表 */
export const FLOWTAG_COLOR_OPTIONS = [
  { value: "red", label: "红色" },
  { value: "orange", label: "橙色" },
  { value: "yellow", label: "黄色" },
  { value: "green", label: "绿色" },
  { value: "cyan", label: "青色" },
  { value: "blue", label: "蓝色" },
  { value: "pink", label: "粉色" },
  { value: "magenta", label: "洋红" },
  { value: "gray", label: "灰色" },
];

/** 匹配范围选项 */
export const FLOWTAG_SCOPE_OPTIONS = [
  { value: "any", label: "任意" },
  { value: "request", label: "请求" },
  { value: "response", label: "响应" },
  { value: "request header", label: "请求头" },
  { value: "request body", label: "请求体" },
  { value: "response header", label: "响应头" },
  { value: "response body", label: "响应体" },
  { value: "any header", label: "任意头" },
  { value: "any body", label: "任意体" },
];
