import type { Flow } from "../types";
import { formatBody, extractContentType } from "./bodyFormatter";

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
  const headers = (flow.req_headers || "").replace(/\n+$/, "");
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
