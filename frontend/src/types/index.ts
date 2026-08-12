/** Hemlock 前端类型定义 — 与后端 schema 对齐 */

// ===== Flow =====
export interface FlowListItem {
  id: string;
  method: string;
  url: string;
  resp_status: number | null;
  // 拆分字段（Burp Suite 风格）
  host: string;
  scheme: string;
  path: string;
  resp_length: number | null;
  mime_type: string;
  extension: string;
  title: string;
  duration_ms?: number | null;
  // FlowTag 标签
  tags: string;
  tag_colors: string;
  // 元数据
  source: string;
  timestamp: string | null;
  flow_number?: number | null;
  is_noise?: boolean;
  noise_reason?: string;
}

export interface FlowListResponse {
  total: number;
  page: number;
  page_size: number;
  items: FlowListItem[];
  noise_count?: number;
}

export interface Flow {
  id: string;
  project_id: string | null;
  method: string;
  url: string;
  req_headers: string;
  req_body: string;
  resp_status: number | null;
  resp_headers: string;
  resp_body: string;
  // 拆分字段
  host: string;
  scheme: string;
  path: string;
  resp_length: number | null;
  mime_type: string;
  extension: string;
  title: string;
  duration_ms?: number | null;
  // FlowTag 标签
  tags: string;
  tag_colors: string;
  // 元数据
  source: string;
  timestamp: string | null;
  flow_number?: number | null;
  is_noise?: boolean;
  noise_reason?: string;
}

// ===== FlowTag =====
export interface FlowTagRule {
  name: string;
  f_regex: string;
  s_regex: string;
  format: string;
  color: string;
  scope: string;
  enabled: boolean;
  sensitive: boolean;
}

export interface FlowTagRulesResponse {
  rules: FlowTagRule[];
  colors: Record<string, string>;
}

export interface ReplayResponse {
  flow_id: string;
  resp_status: number | null;
  resp_headers: string;
  resp_body: string;
}

// ===== Request =====
export interface SendRequestRequest {
  method: string;
  url: string;
  headers: string;
  body: string;
  project_id?: string | null;
}

export interface SendRequestResponse {
  flow_id: string;
  status: number | null;
  headers: string;
  body: string;
  duration_ms?: number | null;
}

// ===== Intruder =====
export type PayloadType =
  | "simple"      // 简单列表
  | "numbers"     // 数字范围
  | "dates"       // 日期范围
  | "uuid"        // UUID 生成
  | "random"      // 随机字符串
  | "brute"       // 字符暴力
  | "wordlist";   // 内置字典

export type WordlistPreset =
  | "sqli"
  | "xss"
  | "path_traversal"
  | "usernames"
  | "passwords"
  | "common_dirs"
  | "api_endpoints"
  | "user_agents";

export interface IntruderPosition {
  placeholder: string;
  payloads: string[];
  payloadType: PayloadType;
  // numbers
  numFrom?: number;
  numTo?: number;
  numStep?: number;
  // dates
  dateFrom?: string;
  dateTo?: string;
  dateFormat?: string;
  // random
  randLength?: number;
  randCount?: number;
  randCharset?: string;
  // brute
  bruteCharset?: string;
  bruteMinLen?: number;
  bruteMaxLen?: number;
  // wordlist
  wordlistPreset?: WordlistPreset;
}

export interface IntruderConfig {
  method: string;
  url: string;
  headers: string;
  body: string;
  positions: IntruderPosition[];
  concurrency: number;
  project_id?: string | null;
  attack_type?: string;
}

export interface IntruderResultItem {
  payload: string;
  status: number | null;
  length: number;
  req_length?: number;
  flow_id: string | null;
  duration_ms?: number | null;
  mime_type?: string;
  host?: string;
  tags?: string;
  tag_colors?: string;
  error?: string;
}

export interface IntruderResult {
  task_id: string;
  total: number;
  results: IntruderResultItem[];
}

// ===== Repeater / Intruder 多标签页 =====
export interface RepeaterTab {
  id: string;
  rawRequest: string;
  scheme: "http" | "https";
  response: SendRequestResponse | null;
  error: string | null;
  respMode: "split" | "raw";
}

export interface IntruderTab {
  id: string;
  rawRequest: string;
  scheme: "http" | "https";
  positions: IntruderPosition[];
  rawPayloads: string[];
  selectedPositionIdx: number;
  concurrency: number;
  result: IntruderResult | null;
  error: string | null;
  attackType: "sniper" | "battering_ram" | "pitchfork" | "cluster_bomb";
}

// ===== AI =====
export interface AnalyzeRequest {
  flow_id: string;
  question?: string | null;
}

export interface AnalyzeResponse {
  flow_id: string;
  analysis: string;
  risk_level: string;
  suggestions: string[];
}

export interface PayloadRequest {
  scene: string;
  context: string;
  count: number;
  method?: string;
  url?: string;
  headers?: string;
  body?: string;
}

export interface PayloadResponse {
  scene: string;
  payloads: string[];
}

// ===== WebSocket Events =====
export interface WSEvent {
  event: string;
  [key: string]: unknown;
}

// ===== Agent Task =====
export interface CreateTaskRequest {
  goal: string;
  target_url: string;
  project_id?: string | null;
  preset?: string;
}

export interface StepData {
  type: string; // tool_call / thinking / complete / error / confirmation_required
  data: string;
  timestamp: string;
}

export interface TaskResponse {
  id: string;
  goal: string;
  status: string; // running / paused / done / failed
  steps: StepData[];
  started_at: string | null;
  ended_at: string | null;
}

export interface TaskSummary {
  id: string;
  goal: string;
  status: string;
  started_at: string | null;
  ended_at: string | null;
}

// ===== Finding =====
export interface Evidence {
  id: string;
  request: string;
  response: string;
  indicator: string;
  ai_reasoning: string;
  replayable: boolean;
  created_at: string | null;
}

export interface Finding {
  id: string;
  project_id: string | null;
  vuln_type: string;
  severity: string;
  status: string;
  title: string;
  detail: string;
  evidence_id: string | null;
  task_id: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface FindingDetail extends Finding {
  evidence: Evidence | null;
}

// ===== Retest =====
export interface RetestResponse {
  finding_id: string;
  retest_status: string; // still_vulnerable / fixed / uncertain / error
  original_status: number | null;
  new_status: number | null;
  similarity: number;
  analysis: string;
  new_flow_id: string | null;
  error: string | null;
}

// ===== Strategy =====
export interface Strategy {
  name: string;
  vuln_type: string;
  owasp_category: string;
  description: string;
  recon_steps: string[];
  test_methods: string[];
  verification: string[];
  common_payloads: string[];
}

// ===== Preset =====
export interface Preset {
  id: string;
  name: string;
  description: string;
  focus_vuln_types: string[];
}

// ===== Session =====
export interface SessionInfo {
  session_id: string;
  authenticated: boolean;
  login_url: string;
  cookies: Record<string, string>;
  headers: Record<string, string>;
  csrf_tokens: Record<string, string>;
  variables: Record<string, string>;
  request_count: number;
}

// ===== Report =====
export interface ReportRequest {
  project_id?: string | null;
  task_id?: string | null;
  fmt: string; // html / markdown / json
}

// ===== Project =====
export interface Project {
  id: string;
  name: string;
  scope: string[];
  is_temp: boolean;
  created_at: string | null;
}

export interface ProjectWithStats extends Project {
  flow_count: number;
  finding_count: number;
  task_count?: number;
}

export interface CreateProjectRequest {
  name: string;
  scope?: string[];
}

export interface SaveProjectRequest {
  name: string;
}

// ===== Agent Chat =====
export interface ChatToolCall {
  tool: string;
  args: Record<string, unknown> | null;
  result: unknown;
  timestamp?: string;
}

export interface ChatResponse {
  reply: string;
  tool_calls: ChatToolCall[];
  task_id?: string;
}

// ===== Intercept =====
export interface InterceptStatus {
  enabled: boolean;
  intercept_requests: boolean;
  intercept_responses: boolean;
  pending_count: number;
  proxy_running: boolean;
}

export interface InterceptConfig {
  enabled?: boolean;
  intercept_requests?: boolean;
  intercept_responses?: boolean;
}

export interface InterceptItem {
  id: string;
  kind: "request" | "response";
  method: string;
  url: string;
  status: number | null;
  queued_at: number;
  raw: string;
}

export interface InterceptPendingList {
  total: number;
  items: InterceptItem[];
}

export interface InterceptActionResult {
  ok: boolean;
  remaining: number;
  error: string; // "not_found" / "already_resolved" / "proxy_not_running"
}
