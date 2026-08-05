/**
 * HTTP 数据包词法分析工具（共享模块）
 *
 * 颜色值通过 CSS 变量动态适配当前主题：
 *   浅色主题取自 Burp Suite Professional v2025.2.4 (BurpLightLaf.properties)
 *   暗色主题取自 Burp Suite Professional v2025.2.4 (BurpDarkLaf.properties)
 *
 * CSS 变量定义在 themes.ts 的每个主题对象中，前缀 --pkt-*。
 * 切换主题时颜色自动跟随，无需重新渲染。
 */

export interface Segment {
  text: string;
  color: string;
  bold?: boolean;
}

/**
 * 数据包语法高亮颜色 — 使用 CSS 变量，随主题自动切换
 *
 * 变量 → 语义映射：
 *   --pkt-text          默认文本
 *   --pkt-operator      运算符/分隔符（冒号、等号、& 等）
 *   --pkt-reserved      保留字（HTTP 方法、布尔值、CSS 选择器）
 *   --pkt-number        数字字面量（状态码、JSON 数字）
 *   --pkt-string        字符串值（JSON 字符串）
 *   --pkt-comment       注释/处理指令
 *   --pkt-param-name    参数名（查询键、Cookie 名、JSON 键、属性名、表单键）
 *   --pkt-param-value   参数值（查询值、Cookie 值、属性值、表单值）
 *   --pkt-header-name   HTTP 头部名
 *   --pkt-header-value  HTTP 头部值
 *   --pkt-tag-name      HTML/XML 标签名
 *   --pkt-entity-ref    实体引用（&amp; 等）
 *   --pkt-cdata         CDATA 内容
 *   --pkt-http-line     HTTP 请求行/状态行
 */
export const COLOR = {
  // ===== HTTP 请求行/状态行 =====
  httpFirstLine: "var(--pkt-http-line)",
  httpVersion: "var(--pkt-operator)",

  // ===== HTTP 方法（保留字） =====
  method: "var(--pkt-reserved)",

  // ===== 状态码（数字字面量） =====
  statusCode: "var(--pkt-number)",

  // ===== URL 组件 =====
  urlPath: "var(--pkt-text)",
  urlFragment: "var(--pkt-operator)",

  // ===== 查询参数 =====
  queryKey: "var(--pkt-param-name)",
  queryValue: "var(--pkt-param-value)",
  querySep: "var(--pkt-operator)",

  // ===== Header =====
  headerName: "var(--pkt-header-name)",
  headerColon: "var(--pkt-operator)",
  headerValue: "var(--pkt-header-value)",

  // ===== Cookie =====
  cookieName: "var(--pkt-param-name)",
  cookieValue: "var(--pkt-param-value)",
  cookieSep: "var(--pkt-operator)",

  // ===== JSON =====
  jsonKey: "var(--pkt-param-name)",
  jsonString: "var(--pkt-string)",
  jsonNumber: "var(--pkt-number)",
  jsonBool: "var(--pkt-reserved)",
  jsonNull: "var(--pkt-reserved)",

  // ===== HTML/XML（标记语言） =====
  tagName: "var(--pkt-tag-name)",
  tagDelimiter: "var(--pkt-text)",
  attrName: "var(--pkt-param-name)",
  attrValue: "var(--pkt-param-value)",
  htmlComment: "var(--pkt-comment)",
  entityRef: "var(--pkt-entity-ref)",
  cdata: "var(--pkt-cdata)",
  xmlDeclaration: "var(--pkt-comment)",

  // ===== CSS =====
  cssSelector: "var(--pkt-reserved)",
  cssPropName: "var(--pkt-param-name)",
  cssPropValue: "var(--pkt-param-value)",
  cssComment: "var(--pkt-comment)",
  cssAtRule: "var(--pkt-reserved)",
  cssBrace: "var(--pkt-operator)",

  // ===== 表单 =====
  formKey: "var(--pkt-param-name)",
  formValue: "var(--pkt-param-value)",
  formSep: "var(--pkt-operator)",

  // ===== 普通文本 =====
  text: "var(--pkt-text)",
} as const;

function detectContentType(headerLines: string[]): string {
  for (const line of headerLines) {
    const match = line.match(/^Content-Type\s*:\s*(.*)$/i);
    if (match) return match[1].trim().toLowerCase();
  }
  return "";
}

function autoDetectBodyType(body: string): "json" | "html" | "xml" | "css" | "form" | "text" {
  const trimmed = body.trim();
  if (!trimmed) return "text";
  if (trimmed.startsWith("{") || trimmed.startsWith("[")) return "json";
  if (trimmed.startsWith("<?xml")) return "xml";
  if (trimmed.startsWith("<!DOCTYPE html") || trimmed.startsWith("<html") || /^<div|^<span|^<p|^<a\b/i.test(trimmed)) return "html";
  if (trimmed.startsWith("<")) return "xml";
  if (/^[.#a-zA-Z][\w\s,>.:()*-]*\{[^}]*[:;][^}]*\}/m.test(trimmed) || trimmed.startsWith("@")) return "css";
  if (/^[^=]+=[^&]*(&[^=]+=[^&]*)*$/m.test(trimmed)) return "form";
  return "text";
}

function getBodyHighlightMode(contentType: string, body: string): "json" | "html" | "xml" | "css" | "form" | "text" {
  if (contentType.includes("json")) return "json";
  if (contentType.includes("html")) return "html";
  if (contentType.includes("xml")) return "xml";
  if (contentType.includes("css")) return "css";
  if (contentType.includes("urlencoded")) return "form";
  return autoDetectBodyType(body);
}

// ===== JSON 词法分析 =====
function tokenizeJson(text: string): Segment[] {
  const segments: Segment[] = [];
  let lastIndex = 0;
  const regex =
    /("(?:[^"\\]|\\.)*")(\s*:)|("(?:[^"\\]|\\.)*")|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|\b(true|false)\b|\b(null)\b/g;
  let match: RegExpExecArray | null;
  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ text: text.slice(lastIndex, match.index), color: COLOR.text });
    }
    if (match[1] !== undefined) {
      // JSON 键 — 蓝色加粗
      segments.push({ text: match[1], color: COLOR.jsonKey, bold: true });
      if (match[2]) segments.push({ text: match[2], color: COLOR.text });
    } else if (match[3] !== undefined) {
      // JSON 字符串值 — 绿色
      segments.push({ text: match[3], color: COLOR.jsonString });
    } else if (match[4] !== undefined) {
      // JSON 数字 — 蓝色
      segments.push({ text: match[4], color: COLOR.jsonNumber });
    } else if (match[5] !== undefined) {
      // JSON 布尔 — 深蓝色
      segments.push({ text: match[5], color: COLOR.jsonBool });
    } else if (match[6] !== undefined) {
      // JSON null — 深蓝色
      segments.push({ text: match[6], color: COLOR.jsonNull });
    }
    lastIndex = regex.lastIndex;
  }
  if (lastIndex < text.length) {
    segments.push({ text: text.slice(lastIndex), color: COLOR.text });
  }
  return segments;
}

// ===== HTML/XML 词法分析（共享） =====
function tokenizeHtmlLike(text: string, isXml: boolean): Segment[] {
  const segments: Segment[] = [];
  let lastIndex = 0;
  const regex =
    /<\?xml[^>]*\?>|<\?[^?]*\?>|<!--[\s\S]*?-->|<!DOCTYPE[^>]*>|<\/?[a-zA-Z][\w:-]*|\/?>|[\w:-]+(?=\s*=)|"[^"]*"|'[^']*'/g;
  let match: RegExpExecArray | null;
  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ text: text.slice(lastIndex, match.index), color: COLOR.text });
    }
    const token = match[0];
    if (token.startsWith("<?xml") || token.startsWith("<!DOCTYPE")) {
      // XML 声明 — 深绿色
      segments.push({ text: token, color: COLOR.xmlDeclaration });
    } else if (token.startsWith("<?")) {
      // 处理指令 — 深绿色
      segments.push({ text: token, color: COLOR.xmlDeclaration });
    } else if (token.startsWith("<!--")) {
      // 注释 — 深绿色
      segments.push({ text: token, color: COLOR.htmlComment });
    } else if (token.startsWith("<") || token === ">" || token === "/>") {
      // 标签分隔符 + 标签名 — 品红色
      segments.push({ text: token, color: COLOR.tagName });
    } else if (token.startsWith('"') || token.startsWith("'")) {
      // 属性值 — 暗红色
      segments.push({ text: token, color: COLOR.attrValue });
    } else {
      // 属性名 — 蓝色
      segments.push({ text: token, color: COLOR.attrName });
    }
    lastIndex = regex.lastIndex;
  }
  if (lastIndex < text.length) {
    segments.push({ text: text.slice(lastIndex), color: COLOR.text });
  }
  return segments;
}

// ===== CSS 词法分析 =====
function tokenizeCss(text: string): Segment[] {
  const segments: Segment[] = [];
  let lastIndex = 0;
  const regex =
    /\/\*[\s\S]*?\*\/|@[a-zA-Z-]+|[\w:.>#*+>~()[\]="'-]+(?=\s*\{)|(\}|{)|([a-zA-Z-]+)(\s*:\s*)([^;{}]+)(;?)/g;
  let match: RegExpExecArray | null;
  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ text: text.slice(lastIndex, match.index), color: COLOR.text });
    }
    const token = match[0];
    if (token.startsWith("/*")) {
      segments.push({ text: token, color: COLOR.cssComment });
    } else if (token.startsWith("@")) {
      segments.push({ text: token, color: COLOR.cssAtRule });
    } else if (match[2] !== undefined) {
      segments.push({ text: match[2], color: COLOR.cssBrace });
    } else if (match[3] !== undefined) {
      segments.push({ text: match[3], color: COLOR.cssPropName });
      segments.push({ text: match[4], color: COLOR.cssBrace });
      segments.push({ text: match[5], color: COLOR.cssPropValue });
      if (match[6]) segments.push({ text: match[6], color: COLOR.cssBrace });
    } else {
      segments.push({ text: token, color: COLOR.cssSelector });
    }
    lastIndex = regex.lastIndex;
  }
  if (lastIndex < text.length) {
    segments.push({ text: text.slice(lastIndex), color: COLOR.text });
  }
  return segments;
}

// ===== 表单 (application/x-www-form-urlencoded) 词法分析 =====
function tokenizeForm(text: string): Segment[] {
  const segments: Segment[] = [];
  // 不跨行匹配：每行独立解析 key=value，避免多行表单数据被误判为单个值
  const regex = /([^=&\n]+)(=)([^&\n]*)(&)?/g;
  let match: RegExpExecArray | null;
  let lastIndex = 0;
  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ text: text.slice(lastIndex, match.index), color: COLOR.formSep });
    }
    segments.push({ text: match[1], color: COLOR.formKey, bold: true });
    segments.push({ text: match[2], color: COLOR.formSep });
    segments.push({ text: match[3], color: COLOR.formValue });
    if (match[4]) segments.push({ text: match[4], color: COLOR.formSep });
    lastIndex = regex.lastIndex;
  }
  if (lastIndex < text.length) {
    segments.push({ text: text.slice(lastIndex), color: COLOR.formSep });
  }
  return segments;
}

function segmentsToLines(segments: Segment[]): Segment[][] {
  const lines: Segment[][] = [[]];
  for (const seg of segments) {
    const parts = seg.text.split("\n");
    for (let i = 0; i < parts.length; i++) {
      if (i > 0) lines.push([]);
      if (parts[i]) {
        lines[lines.length - 1].push({ text: parts[i], color: seg.color, bold: seg.bold });
      }
    }
  }
  return lines;
}

/**
 * 检测数据包 body 的内容类型模式。
 * 用于 CodeEditor 决定换行策略：表单数据不换行（同 Burp Suite），JSON/HTML 等可换行。
 */
export function getPacketBodyMode(content: string): "json" | "html" | "xml" | "css" | "form" | "text" {
  if (!content || !content.trim()) return "text";
  let working = content.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const allLines = working.split("\n");
  let separatorIndex = -1;
  for (let i = 0; i < allLines.length; i++) {
    if (allLines[i].trim() === "") {
      separatorIndex = i;
      break;
    }
  }
  const headerLines = separatorIndex >= 0 ? allLines.slice(0, separatorIndex) : [];
  const bodyRaw = separatorIndex >= 0 ? allLines.slice(separatorIndex + 1).join("\n") : "";
  if (!bodyRaw) return "text";
  const contentType = detectContentType(headerLines);
  return getBodyHighlightMode(contentType, bodyRaw);
}

/**
 * 解析请求行 — Burp Suite 风格
 * 方法(深蓝 reservedWord) + URL(近黑 httpFirstLine) + 版本(深灰 operator)
 */
function parseRequestLine(line: string): Segment[] {
  const match = line.match(/^(\S+)(\s+)(\S+)(\s+)(HTTP\/\d(?:\.\d)?)$/);
  if (match) {
    return [
      { text: match[1], color: COLOR.method, bold: true },          // 方法 — 深蓝加粗
      { text: match[2], color: COLOR.httpFirstLine },                // 空格
      ...tokenizeUrl(match[3]),                                       // URL
      { text: match[4], color: COLOR.httpFirstLine },                // 空格
      { text: match[5], color: COLOR.httpVersion },                  // HTTP 版本 — 深灰
    ];
  }
  const simpleMatch = line.match(/^(\S+)(\s+)(\S+)$/);
  if (simpleMatch) {
    return [
      { text: simpleMatch[1], color: COLOR.method, bold: true },
      { text: simpleMatch[2], color: COLOR.httpFirstLine },
      ...tokenizeUrl(simpleMatch[3]),
    ];
  }
  return [{ text: line, color: COLOR.httpFirstLine }];
}

/**
 * URL 拆分：路径(近黑) + ?查询(键蓝值暗红) + #锚点(深灰)
 */
function tokenizeUrl(url: string): Segment[] {
  const segments: Segment[] = [];
  let queryStart = url.indexOf("?");
  let fragmentStart = url.indexOf("#");

  if (fragmentStart >= 0 && (queryStart < 0 || fragmentStart < queryStart)) {
    segments.push({ text: url.slice(0, fragmentStart), color: COLOR.urlPath });
    segments.push({ text: url.slice(fragmentStart), color: COLOR.urlFragment });
    return segments;
  }

  if (queryStart >= 0) {
    segments.push({ text: url.slice(0, queryStart), color: COLOR.urlPath });
    segments.push({ text: "?", color: COLOR.querySep });

    if (fragmentStart >= 0 && fragmentStart > queryStart) {
      const queryString = url.slice(queryStart + 1, fragmentStart);
      segments.push(...tokenizeQueryString(queryString));
      segments.push({ text: url.slice(fragmentStart), color: COLOR.urlFragment });
    } else {
      const queryString = url.slice(queryStart + 1);
      segments.push(...tokenizeQueryString(queryString));
    }
  } else if (fragmentStart >= 0) {
    segments.push({ text: url.slice(0, fragmentStart), color: COLOR.urlPath });
    segments.push({ text: url.slice(fragmentStart), color: COLOR.urlFragment });
  } else {
    segments.push({ text: url, color: COLOR.urlPath });
  }
  return segments;
}

/**
 * 查询字符串着色：键蓝(#0000c0)、= 深灰、值暗红(#a01010)、& 深灰
 */
function tokenizeQueryString(query: string): Segment[] {
  const segments: Segment[] = [];
  const regex = /([^=&]+)(=)([^&]*)(&)?/g;
  let match: RegExpExecArray | null;
  let lastIndex = 0;
  while ((match = regex.exec(query)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ text: query.slice(lastIndex, match.index), color: COLOR.querySep });
    }
    segments.push({ text: match[1], color: COLOR.queryKey, bold: true });
    segments.push({ text: match[2], color: COLOR.querySep });
    segments.push({ text: match[3], color: COLOR.queryValue });
    if (match[4]) segments.push({ text: match[4], color: COLOR.querySep });
    lastIndex = regex.lastIndex;
  }
  if (lastIndex < query.length) {
    segments.push({ text: query.slice(lastIndex), color: COLOR.querySep });
  }
  return segments;
}

/**
 * 解析状态行 — Burp Suite 风格
 * HTTP版本(深灰) + 状态码(蓝色数字) + 描述(近黑)
 */
function parseStatusLine(line: string): Segment[] {
  const match = line.match(/^(HTTP\/\d(?:\.\d)?)(\s+)(\d{3})(\s+)(.*)$/);
  if (match) {
    return [
      { text: match[1], color: COLOR.httpVersion },                 // HTTP 版本 — 深灰
      { text: match[2], color: COLOR.httpFirstLine },               // 空格
      { text: match[3], color: COLOR.statusCode, bold: true },      // 状态码 — 蓝色数字加粗
      { text: match[4], color: COLOR.httpFirstLine },               // 空格
      { text: match[5], color: COLOR.httpFirstLine },               // 描述文本 — 近黑
    ];
  }
  return [{ text: line, color: COLOR.httpFirstLine }];
}

/**
 * Cookie 值着色：name=value; name=value
 * 名蓝色(#0000c0)、= 深灰、值暗红(#a01010)、; 深灰
 */
function tokenizeCookieValue(value: string): Segment[] {
  const segments: Segment[] = [];
  const cookieAttrs = /^(domain|path|expires|max-age|httponly|secure|samesite|priority)\s*=/i;
  const regex = /([^=;\s]+)(\s*=\s*)?([^;]*)?(;\s*)?/g;
  let match: RegExpExecArray | null;
  let lastIndex = 0;
  while ((match = regex.exec(value)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ text: value.slice(lastIndex, match.index), color: COLOR.cookieSep });
    }
    if (match[1] !== undefined) {
      const name = match[1];
      const isAttr = cookieAttrs.test(name + "=");
      if (isAttr) {
        segments.push({ text: name, color: COLOR.cookieSep });
      } else {
        segments.push({ text: name, color: COLOR.cookieName, bold: true });
      }
    }
    if (match[2] !== undefined) {
      segments.push({ text: match[2], color: COLOR.cookieSep });
    }
    if (match[3] !== undefined && match[3] !== "") {
      segments.push({ text: match[3], color: COLOR.cookieValue });
    }
    if (match[4] !== undefined) {
      segments.push({ text: match[4], color: COLOR.cookieSep });
    }
    lastIndex = regex.lastIndex;
  }
  if (lastIndex < value.length) {
    segments.push({ text: value.slice(lastIndex), color: COLOR.cookieSep });
  }
  return segments.length > 0 ? segments : [{ text: value, color: COLOR.headerValue }];
}

/**
 * 解析头部行 — Burp Suite 风格
 * Header 名(深蓝 #000075) + :(深灰) + 值(深灰 #202020)
 */
function parseHeaderLine(line: string): Segment[] {
  const match = line.match(/^([^:]+)(:)(.*)$/);
  if (match) {
    const headerName = match[1].trim();
    const lowerName = headerName.toLowerCase();
    const valueText = match[3];
    const segments: Segment[] = [
      { text: match[1], color: COLOR.headerName, bold: true },
      { text: match[2], color: COLOR.headerColon },
    ];

    if (lowerName === "cookie") {
      segments.push(...tokenizeCookieValue(valueText));
    } else {
      segments.push({ text: valueText, color: COLOR.headerValue });
    }
    return segments;
  }
  return [{ text: line, color: COLOR.text }];
}

/**
 * 将 HTTP 数据包文本词法分析为带颜色的行数组。
 */
export function tokenizePacket(
  content: string,
  type: "request" | "response"
): Segment[][] {
  if (!content || !content.trim()) return [];

  let working = content.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const allLines = working.split("\n");

  let separatorIndex = -1;
  for (let i = 0; i < allLines.length; i++) {
    if (allLines[i].trim() === "") {
      separatorIndex = i;
      break;
    }
  }

  const headerLines =
    separatorIndex >= 0 ? allLines.slice(0, separatorIndex) : allLines;
  const bodyRaw =
    separatorIndex >= 0 ? allLines.slice(separatorIndex + 1).join("\n") : "";

  const contentType = detectContentType(headerLines);
  const bodyMode = getBodyHighlightMode(contentType, bodyRaw);

  const lines: Segment[][] = [];

  if (headerLines.length > 0) {
    lines.push(
      type === "request"
        ? parseRequestLine(headerLines[0])
        : parseStatusLine(headerLines[0])
    );
  }

  for (let i = 1; i < headerLines.length; i++) {
    lines.push(parseHeaderLine(headerLines[i]));
  }

  if (separatorIndex >= 0) {
    lines.push([]);
  }

  if (bodyRaw) {
    let bodySegments: Segment[];
    switch (bodyMode) {
      case "json": bodySegments = tokenizeJson(bodyRaw); break;
      case "html": bodySegments = tokenizeHtmlLike(bodyRaw, false); break;
      case "xml": bodySegments = tokenizeHtmlLike(bodyRaw, true); break;
      case "css": bodySegments = tokenizeCss(bodyRaw); break;
      case "form": bodySegments = tokenizeForm(bodyRaw); break;
      default: bodySegments = [{ text: bodyRaw, color: COLOR.text }]; break;
    }
    for (const lineSegs of segmentsToLines(bodySegments)) {
      lines.push(lineSegs);
    }
  }

  return lines;
}
