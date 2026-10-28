import type { Flow } from "../types";
import { formatBody, extractContentType } from "./bodyFormatter";

/** 从 URL 提取 host（含端口），失败返回空串 */
function hostFromUrl(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return "";
  }
}

/**
 * 补齐 Host 头：HTTP/2 抓包没有 Host 头（目标在 :authority 伪头部），
 * 旧版本入库的记录 req_headers 里缺 Host——展示时从 URL 推导，
 * 插到头部区首位（与浏览器 HTTP/1.1 的头顺序一致）。
 */
function ensureHostHeader(headers: string, url: string): string {
  if (/^host\s*:/im.test(headers)) return headers;
  const host = hostFromUrl(url);
  if (!host) return headers;
  const trimmed = headers.replace(/\n+$/, "");
  return trimmed ? `Host: ${host}\n${trimmed}` : `Host: ${host}`;
}

/** 构建请求原始文本（自动格式化 body） */
export function buildRequestContent(flow: Flow): string {
  let path = flow.url;
  try {
    const u = new URL(flow.url);
    path = `${u.pathname}${u.search}`;
  } catch {
    // ignore — use full URL as path
  }
  const requestLine = `${flow.method} ${path} HTTP/1.1`;
  const headers = ensureHostHeader((flow.req_headers || "").replace(/\n+$/, ""), flow.url);
  const rawBody = flow.req_body || "";
  if (rawBody) {
    const ct = extractContentType(headers);
    const body = formatBody(rawBody, ct);
    return `${requestLine}\n${headers}\n\n${body}`;
  }
  return `${requestLine}\n${headers}`;
}

/** 构建响应原始文本（自动格式化 body） */
export function buildResponseContent(flow: Flow): string {
  const statusLine = `HTTP/1.1 ${flow.resp_status ?? 0}`;
  const headers = (flow.resp_headers || "").replace(/\n+$/, "");
  const rawBody = flow.resp_body || "";
  if (rawBody) {
    const ct = extractContentType(headers);
    const body = formatBody(rawBody, ct);
    return `${statusLine}\n${headers}\n\n${body}`;
  }
  return `${statusLine}\n${headers}`;
}
