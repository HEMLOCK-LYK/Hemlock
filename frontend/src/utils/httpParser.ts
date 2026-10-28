/**
 * HTTP 原始请求解析与构建工具。
 *
 * 在 Repeater 的 Raw 编辑器中，用户编辑完整的 HTTP 请求文本，
 * 发送前需要解析出 method / url / headers / body 传给后端 API。
 */

import { formatBody, extractContentType } from "./bodyFormatter";

export interface ParsedRequest {
  method: string;
  url: string;
  headers: string; // 所有头部（含 Host），不含请求行
  body: string;
  scheme: "http" | "https";
}

/**
 * 解析原始 HTTP 请求文本。
 *
 * 格式：
 *   METHOD /path HTTP/1.1
 *   Host: example.com
 *   Other-Header: value
 *
 *   body content
 */
export function parseRawRequest(
  raw: string,
  scheme: "http" | "https" = "https"
): ParsedRequest | null {
  const text = raw.replace(/\r\n/g, "\n");
  // 用第一个空行分割 headers 区和 body
  const splitIdx = text.indexOf("\n\n");
  const headerSection = splitIdx >= 0 ? text.substring(0, splitIdx) : text;
  const body = splitIdx >= 0 ? text.substring(splitIdx + 2) : "";

  const lines = headerSection.split("\n");
  const requestLine = lines[0] || "";

  // 解析请求行: METHOD /path HTTP/1.1
  const reqParts = requestLine.trim().split(/\s+/);
  if (reqParts.length < 2) return null;
  const method = reqParts[0];
  let target = reqParts[1];

  // 解析头部
  const headerLines = lines.slice(1);
  let host = "";
  for (const line of headerLines) {
    const lower = line.toLowerCase();
    if (lower.startsWith("host:")) {
      host = line.substring(5).trim();
      break;
    }
  }

  // 绝对形式请求行（GET http://host/path HTTP/1.1，代理抓包常见）：
  // 直接用完整 URL 作为请求目标，scheme 以请求行为准。
  // 否则按 origin-form 拼 scheme://host+path——path 必须以 / 开头，
  // 否则会拼出 https://hosthttp://... 这类畸形 URL（曾导致流量列表 host 显示 "xx.comhttp"）。
  let url: string;
  if (/^https?:\/\//i.test(target)) {
    url = target;
    scheme = target.toLowerCase().startsWith("https://") ? "https" : "http";
  } else {
    if (!host) return null;
    if (target && !target.startsWith("/")) target = "/" + target;
    url = `${scheme}://${host}${target || "/"}`;
  }

  const headers = headerLines.join("\n").trim();

  return { method, url, headers, body, scheme };
}

/**
 * 从 {method, url, headers, body} 构建原始请求文本。
 *
 * 如果 headers 中没有 Host，从 URL 提取并插入到头部区首位——
 * 与浏览器 HTTP/1.1 的头顺序一致（Host 紧跟请求行），
 * 也与流量列表详情（buildRequestContent）和代理入库顺序保持一致。
 */
export function buildRawRequest(
  method: string,
  url: string,
  headers: string,
  body: string
): string {
  try {
    const u = new URL(url);
    const path = u.pathname + u.search;
    const headerLines = headers
      .split("\n")
      .map((l) => l.trim())
      .filter((l) => l.length > 0);

    // 检查是否已有 Host（缺失时插到首位，不追加到末尾）
    const hasHost = headerLines.some((l) =>
      l.toLowerCase().startsWith("host:")
    );
    if (!hasHost) {
      headerLines.unshift(`Host: ${u.host}`);
    }

    const headerStr = headerLines.join("\n");
    // 格式化 body — 与流量详情页保持一致（JSON 美化、HTML 缩进等）
    const formattedBody = body ? formatBody(body, extractContentType(headerStr)) : "";
    return formattedBody
      ? `${method} ${path} HTTP/1.1\n${headerStr}\n\n${formattedBody}`
      : `${method} ${path} HTTP/1.1\n${headerStr}`;
  } catch {
    // URL 无效时，尽力返回
    const formattedBody = body ? formatBody(body, extractContentType(headers)) : "";
    return formattedBody
      ? `${method} / HTTP/1.1\n${headers}\n\n${formattedBody}`
      : `${method} / HTTP/1.1\n${headers}`;
  }
}

/** 从完整 URL 提取 scheme */
export function extractScheme(url: string): "http" | "https" {
  return url.startsWith("https://") ? "https" : "http";
}
