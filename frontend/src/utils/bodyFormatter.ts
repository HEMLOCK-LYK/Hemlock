/**
 * HTTP 数据包 body 智能格式化工具
 *
 * 自动检测 body 内容类型，并进行美化格式化（pretty-print），
 * 使数据包显示时根据内容结构智能换行、缩进，而非按字符数强制断行。
 *
 * 支持的类型：
 * - JSON (application/json, text/json, +内容检测)
 * - HTML (text/html, application/xhtml+xml)
 * - XML  (application/xml, text/xml, +内容检测)
 * - JavaScript (application/javascript, text/javascript, application/ecmascript)
 * - CSS  (text/css)
 * - YAML (application/yaml, text/yaml, application/x-yaml)
 * - CSV  (text/csv, application/csv)
 * - GraphQL (application/graphql)
 * - SQL  (application/sql, text/sql)
 * - Multipart form-data (multipart/form-data)
 * - Form-URLencoded (application/x-www-form-urlencoded)
 * - Base64 (base64 编码内容)
 * - 纯文本/未知类型 (智能换行回退策略)
 *
 * 所有格式化都是「安全」的：如果解析失败，返回原始内容，不影响显示。
 */

/** 最大行宽（超过此宽度的行会在自然边界处换行） */
const MAX_LINE_WIDTH = 120;

/** 检测内容类型并格式化 body */
export function formatBody(body: string, contentType?: string): string {
  if (!body || !body.trim()) return body;

  const ct = (contentType || "").toLowerCase();
  const trimmed = body.trimStart();

  // 1. JSON
  if (ct.includes("json") || _looksLikeJson(body)) {
    return _formatJson(body);
  }

  // 2. HTML
  if (ct.includes("html") || _looksLikeHtml(body)) {
    return _formatHtml(body);
  }

  // 3. XML (排除 XHTML，已由 HTML 处理)
  if (ct.includes("xml") || trimmed.startsWith("<?xml")) {
    return _formatXml(body);
  }

  // 4. JavaScript / TypeScript
  if (
    ct.includes("javascript") ||
    ct.includes("ecmascript") ||
    ct.includes("text/typescript") ||
    _looksLikeJs(body)
  ) {
    return _formatJavaScript(body);
  }

  // 5. CSS
  if (ct.includes("css") || _looksLikeCss(body)) {
    return _formatCss(body);
  }

  // 6. YAML
  if (ct.includes("yaml") || _looksLikeYaml(body)) {
    return _formatYaml(body);
  }

  // 7. CSV
  if (ct.includes("csv") || _looksLikeCsv(body)) {
    return _formatCsv(body);
  }

  // 8. GraphQL
  if (ct.includes("graphql") || _looksLikeGraphQL(body)) {
    return _formatGraphQL(body);
  }

  // 9. SQL
  if (ct.includes("sql") || _looksLikeSql(body)) {
    return _formatSql(body);
  }

  // 10. Multipart form-data
  if (ct.includes("multipart/form-data") || ct.includes("multipart/mixed")) {
    return _formatMultipart(body, ct);
  }

  // 11. Form-URLencoded
  if (ct.includes("x-www-form-urlencoded")) {
    return _formatUrlencoded(body);
  }

  // 12. Base64 编码内容
  if (_looksLikeBase64(body)) {
    return _formatBase64(body);
  }

  // 13. 通用智能换行（回退策略）
  return _formatGeneric(body);
}

/** 从 headers 文本中提取 Content-Type 值 */
export function extractContentType(headers: string): string {
  if (!headers) return "";
  const lines = headers.split(/\r?\n/);
  for (const line of lines) {
    const lower = line.toLowerCase();
    if (lower.startsWith("content-type:") || lower.startsWith("content-type :")) {
      const idx = line.indexOf(":");
      if (idx >= 0) {
        return line.substring(idx + 1).trim().split(";")[0].trim();
      }
    }
  }
  return "";
}

// ===== 内容检测函数 =====

function _looksLikeJson(body: string): boolean {
  const trimmed = body.trimStart();
  return (
    (trimmed.startsWith("{") && body.trimEnd().endsWith("}")) ||
    (trimmed.startsWith("[") && body.trimEnd().endsWith("]"))
  );
}

function _looksLikeHtml(body: string): boolean {
  const lower = body.trimStart().toLowerCase();
  return (
    lower.startsWith("<!doctype html") ||
    lower.startsWith("<html") ||
    (lower.includes("<head") && lower.includes("<body")) ||
    (lower.includes("<div") && lower.includes("</div>"))
  );
}

function _looksLikeJs(body: string): boolean {
  const trimmed = body.trimStart();
  // 常见 JS 开头模式
  if (
    trimmed.startsWith("function ") ||
    trimmed.startsWith("var ") ||
    trimmed.startsWith("let ") ||
    trimmed.startsWith("const ") ||
    trimmed.startsWith("import ") ||
    trimmed.startsWith("export ") ||
    trimmed.startsWith("class ") ||
    trimmed.startsWith("(function") ||
    trimmed.startsWith("!function") ||
    trimmed.startsWith("document.") ||
    trimmed.startsWith("window.") ||
    trimmed.startsWith("module.") ||
    trimmed.startsWith("require(")
  ) {
    return true;
  }
  // 包含典型 JS 语法模式
  if (/=>\s/.test(body) && /[{};]/.test(body)) return true;
  if (/console\.(log|error|warn|info)\s*\(/.test(body)) return true;
  return false;
}

function _looksLikeCss(body: string): boolean {
  const trimmed = body.trimStart();
  if (trimmed.startsWith("@media") || trimmed.startsWith("@import") || trimmed.startsWith("@keyframes")) {
    return true;
  }
  // CSS 选择器 + 属性块模式: selector { property: value; }
  return /[\w\-.#*:>\[\]=~+, ]+\s*\{[^}]*[\w-]+\s*:\s*[^;}]+[;}]/.test(body);
}

function _looksLikeYaml(body: string): boolean {
  const lines = body.split(/\n/);
  let yamlKeyCount = 0;
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    // YAML key: value 模式（不含 {} 且有缩进层级）
    if (/^[\w\-\.]+\s*:\s/.test(trimmed) && !trimmed.includes("{")) {
      yamlKeyCount++;
    }
    // YAML 列表项
    if (trimmed.startsWith("- ") || trimmed === "-") {
      yamlKeyCount++;
    }
  }
  return yamlKeyCount >= 2;
}

function _looksLikeCsv(body: string): boolean {
  const lines = body.split(/\n/).filter((l) => l.trim());
  if (lines.length < 2) return false;
  // 检测分隔符一致性
  const counts = lines.slice(0, Math.min(lines.length, 10)).map((l) => {
    const commas = (l.match(/,/g) || []).length;
    const tabs = (l.match(/\t/g) || []).length;
    const semicolons = (l.match(/;/g) || []).length;
    return { commas, tabs, semicolons };
  });
  // 如果所有行的逗号数一致且 > 0
  const firstCommas = counts[0].commas;
  if (firstCommas > 0 && counts.every((c) => c.commas === firstCommas)) return true;
  // 如果所有行的 tab 数一致且 > 0
  const firstTabs = counts[0].tabs;
  if (firstTabs > 0 && counts.every((c) => c.tabs === firstTabs)) return true;
  return false;
}

function _looksLikeGraphQL(body: string): boolean {
  const trimmed = body.trimStart();
  return (
    trimmed.startsWith("query ") ||
    trimmed.startsWith("mutation ") ||
    trimmed.startsWith("subscription ") ||
    trimmed.startsWith("{") && trimmed.includes("__typename")
  );
}

function _looksLikeSql(body: string): boolean {
  const upper = body.trimStart().toUpperCase();
  return (
    upper.startsWith("SELECT ") ||
    upper.startsWith("INSERT ") ||
    upper.startsWith("UPDATE ") ||
    upper.startsWith("DELETE ") ||
    upper.startsWith("CREATE ") ||
    upper.startsWith("ALTER ") ||
    upper.startsWith("DROP ") ||
    upper.startsWith("WITH ")
  );
}

function _looksLikeBase64(body: string): boolean {
  const trimmed = body.trim().replace(/\n/g, "").replace(/\r/g, "");
  if (trimmed.length < 100) return false;
  // Base64 字符集 + 可能的 padding
  return /^[A-Za-z0-9+/=\s]+$/.test(trimmed) && trimmed.length % 4 === 0;
}

// ===== 格式化实现 =====

function _formatJson(body: string): string {
  try {
    const parsed = JSON.parse(body);
    return JSON.stringify(parsed, null, 2);
  } catch {
    // 尝试修复常见的 JSON 问题后重试
    try {
      // 移除 BOM 和前后非 JSON 字符
      const cleaned = body.replace(/^\uFEFF/, "").trim();
      const parsed = JSON.parse(cleaned);
      return JSON.stringify(parsed, null, 2);
    } catch {
      return _formatGeneric(body);
    }
  }
}

function _formatHtml(body: string): string {
  // 简单缩进：保持原始换行，按标签层级添加缩进
  let result = body.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  result = result.replace(/\n{3,}/g, "\n\n");

  const lines = result.split("\n");
  let indent = 0;
  const formattedLines: string[] = [];
  const voidElements = new Set([
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
  ]);

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) {
      formattedLines.push("");
      continue;
    }
    // 闭合标签减少缩进
    if (trimmed.startsWith("</")) {
      indent = Math.max(0, indent - 1);
    }
    formattedLines.push("  ".repeat(indent) + trimmed);
    // 开标签增加缩进（排除自闭合、注释、行内闭合）
    if (
      trimmed.startsWith("<") &&
      !trimmed.startsWith("</") &&
      !trimmed.startsWith("<!") &&
      !trimmed.startsWith("<!--") &&
      !trimmed.endsWith("/>") &&
      !trimmed.endsWith("-->")
    ) {
      const tagName = trimmed.match(/^<(\w+)/)?.[1]?.toLowerCase() || "";
      if (!voidElements.has(tagName) && !trimmed.includes(`</${tagName}`)) {
        indent++;
      }
    }
  }
  return formattedLines.join("\n");
}

function _formatXml(body: string): string {
  // 简单缩进：保持原始换行，按标签层级添加缩进
  let result = body.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  result = result.replace(/\n{3,}/g, "\n\n");

  const lines = result.split("\n");
  let indent = 0;
  const formattedLines: string[] = [];

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) {
      formattedLines.push("");
      continue;
    }
    // XML 声明不缩进
    if (trimmed.startsWith("<?xml")) {
      formattedLines.push(trimmed);
      continue;
    }
    // 闭合标签减少缩进
    if (trimmed.startsWith("</")) {
      indent = Math.max(0, indent - 1);
    }
    formattedLines.push("  ".repeat(indent) + trimmed);
    // 开标签增加缩进
    if (
      trimmed.startsWith("<") &&
      !trimmed.startsWith("</") &&
      !trimmed.startsWith("<?") &&
      !trimmed.startsWith("<!--") &&
      !trimmed.endsWith("/>") &&
      !trimmed.includes("</")
    ) {
      indent++;
    }
  }
  return formattedLines.join("\n");
}

function _formatJavaScript(body: string): string {
  let result = body.replace(/\r\n/g, "\n");

  // JS 美化策略：
  // 1. 在 ; 后插入换行（如果同一行有多个语句）
  // 2. 在 { 后和 } 前插入换行
  // 3. 计算缩进
  // 4. 不处理字符串内部的内容

  // 先在 ; { } 后添加换行（注意不处理字符串内部）
  result = _breakAtDelimiters(result, [";", "{", "}"]);

  // 压缩连续空行
  result = result.replace(/\n{3,}/g, "\n\n");

  // 计算缩进
  const lines = result.split("\n");
  let indent = 0;
  const formattedLines: string[] = [];

  for (const line of lines) {
    let trimmedLine = line.trim();
    if (!trimmedLine) {
      formattedLines.push("");
      continue;
    }

    // 闭合括号减少缩进
    if (trimmedLine.startsWith("}") || trimmedLine.startsWith(")") || trimmedLine.startsWith("]")) {
      indent = Math.max(0, indent - 1);
    }

    formattedLines.push("  ".repeat(indent) + trimmedLine);

    // 开括号增加缩进
    const opens = (trimmedLine.match(/[{\[\(]/g) || []).length;
    const closes = (trimmedLine.match(/[}\]\)]/g) || []).length;
    // 如果行以开括号结尾
    if (/[{\[\(]\s*$/.test(trimmedLine) || (opens > closes)) {
      indent += opens - closes;
      if (indent < 0) indent = 0;
    }
  }

  return formattedLines.join("\n").replace(/\n{3,}/g, "\n\n");
}

function _formatCss(body: string): string {
  let result = body.replace(/\r\n/g, "\n");

  // CSS 美化策略：
  // 1. 在 { 前换行
  // 2. 在 } 前换行，} 后换行
  // 3. 在 ; 后换行
  // 4. 在 : 后添加空格
  // 5. 计算缩进

  // 在 { 前换行
  result = result.replace(/\s*\{\s*/g, " {\n  ");
  // 在 ; 后换行（带缩进）
  result = result.replace(/;\s*/g, ";\n  ");
  // 在 } 前换行
  result = result.replace(/\s*\}\s*/g, "\n}\n");
  // 修复 : 后空格
  result = result.replace(/:\s*/g, ": ");
  // 压缩多余空行
  result = result.replace(/\n{3,}/g, "\n\n");
  // 移除行首多余空格（非缩进行）
  result = result.replace(/\n  \n/g, "\n}\n");

  // 清理缩进
  const lines = result.split("\n");
  let indent = 0;
  const formattedLines: string[] = [];

  for (const line of lines) {
    const trimmedLine = line.trim();
    if (!trimmedLine) {
      formattedLines.push("");
      continue;
    }

    if (trimmedLine === "}") {
      indent = Math.max(0, indent - 1);
      formattedLines.push("  ".repeat(indent) + "}");
    } else if (trimmedLine.endsWith("{")) {
      formattedLines.push("  ".repeat(indent) + trimmedLine);
      indent++;
    } else {
      formattedLines.push("  ".repeat(indent) + trimmedLine);
    }
  }

  return formattedLines.join("\n").replace(/\n{3,}/g, "\n\n");
}

function _formatYaml(body: string): string {
  // YAML 通常已经是结构化的，只需统一换行和清理
  let result = body.replace(/\r\n/g, "\n");

  // 如果已经是多行格式化好的 YAML，保持原样
  const lines = result.split("\n");
  if (lines.length > 1) {
    // 检查是否已经格式化（有缩进）
    const hasIndent = lines.some((l) => l.startsWith("  ") || l.startsWith("- "));
    if (hasIndent) {
      return result.replace(/\n{3,}/g, "\n\n").replace(/\t/g, "  ");
    }
  }

  // 单行 YAML，尝试按 key: value 拆分
  return result.replace(/\n{3,}/g, "\n\n");
}

function _formatCsv(body: string): string {
  let result = body.replace(/\r\n/g, "\n").replace(/\r/g, "\n");

  // 检测分隔符
  const firstLine = result.split("\n")[0] || "";
  let delimiter = ",";
  if ((firstLine.match(/\t/g) || []).length > (firstLine.match(/,/g) || []).length) {
    delimiter = "\t";
  } else if ((firstLine.match(/;/g) || []).length > (firstLine.match(/,/g) || []).length) {
    delimiter = ";";
  }

  // CSV 已经是行格式，只需确保换行正确
  // 对齐列宽（如果行数不多）
  const lines = result.split("\n").filter((l) => l.trim());
  if (lines.length <= 200) {
    const rows = lines.map((l) => l.split(delimiter));
    const colCount = Math.max(...rows.map((r) => r.length));
    const colWidths: number[] = new Array(colCount).fill(0);
    for (const row of rows) {
      for (let i = 0; i < row.length; i++) {
        colWidths[i] = Math.max(colWidths[i], row[i].trim().length);
      }
    }
    // 如果最大列宽合理，进行对齐
    const totalWidth = colWidths.reduce((a, b) => a + b, 0);
    if (totalWidth <= MAX_LINE_WIDTH * 2 && colWidths.every((w) => w <= 60)) {
      return rows
        .map((row) =>
          row
            .map((cell, i) => cell.trim().padEnd(colWidths[i]))
            .join(delimiter === "\t" ? " | " : delimiter + " ")
            .trimEnd()
        )
        .join("\n");
    }
  }

  return result;
}

function _formatGraphQL(body: string): string {
  let result = body.replace(/\r\n/g, "\n").trim();

  // GraphQL 美化：在 { } ( ) 前后换行
  result = result.replace(/\s*\{\s*/g, " {\n");
  result = result.replace(/\s*\}\s*/g, "\n}\n");
  result = result.replace(/\s*\(\s*/g, "(\n  ");
  result = result.replace(/\s*\)\s*/g, "\n)");
  // 字段间换行
  result = result.replace(/(\w+)\s+(\w+)/g, "$1\n$2");

  // 计算缩进
  const lines = result.split("\n");
  let indent = 0;
  const formattedLines: string[] = [];

  for (const line of lines) {
    const trimmedLine = line.trim();
    if (!trimmedLine) {
      formattedLines.push("");
      continue;
    }

    if (trimmedLine.startsWith("}") || trimmedLine.startsWith(")")) {
      indent = Math.max(0, indent - 1);
    }

    formattedLines.push("  ".repeat(indent) + trimmedLine);

    if (trimmedLine.endsWith("{") || trimmedLine.endsWith("(")) {
      indent++;
    }
  }

  return formattedLines.join("\n").replace(/\n{3,}/g, "\n\n");
}

function _formatSql(body: string): string {
  let result = body.replace(/\r\n/g, "\n").replace(/\t/g, "  ");

  // SQL 美化策略：
  // 1. 关键字后换行
  // 2. 逗号后换行
  // 3. 在 ( 后和 ) 前换行
  // 4. 子查询缩进

  const keywords = [
    "SELECT", "FROM", "WHERE", "INSERT", "UPDATE", "DELETE", "CREATE",
    "ALTER", "DROP", "JOIN", "LEFT", "RIGHT", "INNER", "OUTER", "ON",
    "GROUP BY", "ORDER BY", "HAVING", "LIMIT", "OFFSET", "VALUES", "SET",
    "INTO", "TABLE", "AND", "OR", "UNION", "WITH", "AS",
  ];

  // 在关键字前换行
  for (const kw of keywords) {
    const re = new RegExp(`\\b${kw}\\b`, "gi");
    result = result.replace(re, "\n" + kw);
  }

  // 逗号后换行
  result = result.replace(/,\s*/g, ",\n  ");

  // ( 后和 ) 前换行
  result = result.replace(/\(\s*/g, "(\n  ");
  result = result.replace(/\s*\)/g, "\n)");

  // 清理开头的换行
  result = result.replace(/^\n+/, "");

  // 计算缩进
  const lines = result.split("\n");
  let indent = 0;
  const formattedLines: string[] = [];

  for (const line of lines) {
    const trimmedLine = line.trim();
    if (!trimmedLine) {
      formattedLines.push("");
      continue;
    }

    // 闭合括号减少缩进
    if (trimmedLine.startsWith(")")) {
      indent = Math.max(0, indent - 1);
    }

    formattedLines.push("  ".repeat(indent) + trimmedLine);

    // 开括号增加缩进
    if (trimmedLine.endsWith("(")) {
      indent++;
    }
  }

  return formattedLines.join("\n").replace(/\n{3,}/g, "\n\n");
}

function _formatMultipart(body: string, contentType: string): string {
  // 提取 boundary
  const boundaryMatch = contentType.match(/boundary=([^;]+)/i);
  const boundary = boundaryMatch ? boundaryMatch[1].trim().replace(/^["']|["']$/g, "") : "";

  if (!boundary) {
    return _formatGeneric(body);
  }

  let result = body.replace(/\r\n/g, "\n");
  const delimiter = "--" + boundary;
  const parts = result.split(delimiter);

  const formattedParts: string[] = [];
  for (let i = 0; i < parts.length; i++) {
    const part = parts[i].trim();
    if (!part || part === "--") continue;

    // 分离 header 和 body
    const headerBodySplit = part.split(/\n\n/);
    const headers = headerBodySplit[0] || "";
    const partBody = headerBodySplit.slice(1).join("\n\n") || "";

    // 格式化 part body
    let formattedBody = partBody;
    if (partBody) {
      // 检测 part body 的内容类型
      const partCt = headers.match(/content-type:\s*([^\r\n]+)/i)?.[1]?.trim() || "";
      formattedBody = formatBody(partBody.trimEnd(), partCt);
    }

    formattedParts.push(
      `${delimiter}\n${headers}${formattedBody ? "\n\n" + formattedBody : ""}`
    );
  }

  // 添加结束 boundary
  return formattedParts.join("\n\n") + `\n${delimiter}--\n`;
}

function _formatUrlencoded(body: string): string {
  // 表单数据保持原始单行格式，不拆分为多行（同 Burp Suite 行为）
  return body;
}

function _formatBase64(body: string): string {
  // Base64 内容按 76 字符换行（MIME 标准）
  const cleaned = body.replace(/\s/g, "");
  if (cleaned.length <= 76) return body;

  const lines: string[] = [];
  for (let i = 0; i < cleaned.length; i += 76) {
    lines.push(cleaned.substring(i, i + 76));
  }
  return lines.join("\n");
}

/**
 * 通用智能换行策略
 * 对未知类型的内容，按自然边界进行换行：
 * - 保留已有的换行
 * - 在自然分隔符（; , | 等后插入换行
 * - 超长行在空格处断行
 */
function _formatGeneric(body: string): string {
  let result = body.replace(/\r\n/g, "\n");

  // 如果已经有多行且每行长度合理，保持原样
  const existingLines = result.split("\n");
  const maxExistingLen = Math.max(...existingLines.map((l) => l.length));
  if (existingLines.length > 1 && maxExistingLen <= MAX_LINE_WIDTH) {
    return result;
  }

  // 在 ; 后换行（如果同行有多个 ; 分隔的内容）
  if (/;.{10,}/.test(result)) {
    result = result.replace(/;(?=\S)/g, ";\n");
  }

  // 在 , 后换行（仅当行很长时）
  const lines1 = result.split("\n");
  result = lines1
    .map((line) => {
      if (line.length <= MAX_LINE_WIDTH) return line;
      // 长行在逗号后断行
      return line.replace(/,(?=\S)/g, ",\n");
    })
    .join("\n");

  // 在 | 后换行（管道分隔）
  const lines2 = result.split("\n");
  result = lines2
    .map((line) => {
      if (line.length <= MAX_LINE_WIDTH) return line;
      return line.replace(/\|(?=\S)/g, "|\n");
    })
    .join("\n");

  // 超长行在空格处断行
  const lines3 = result.split("\n");
  result = lines3
    .map((line) => {
      if (line.length <= MAX_LINE_WIDTH) return line;
      return _wrapAtWordBoundary(line, MAX_LINE_WIDTH);
    })
    .join("\n");

  // 压缩多余空行
  return result.replace(/\n{3,}/g, "\n\n");
}

/** 在单词边界处换行 */
function _wrapAtWordBoundary(text: string, maxWidth: number): string {
  if (text.length <= maxWidth) return text;

  const words = text.split(/(\s+)/);
  const lines: string[] = [];
  let current = "";

  for (const word of words) {
    if ((current + word).length > maxWidth && current.trim()) {
      lines.push(current);
      current = word.trimStart();
    } else {
      current += word;
    }
  }
  if (current) lines.push(current);

  return lines.join("\n");
}

/**
 * 在分隔符处断行（用于 JS 等语言）
 * 避免在字符串内部断行
 */
function _breakAtDelimiters(code: string, delimiters: string[]): string {
  const result: string[] = [];
  let current = "";
  let inString: string | null = null; // ', ", `
  let inComment = false;
  let inLineComment = false;
  let escape = false;

  for (let i = 0; i < code.length; i++) {
    const char = code[i];
    const next = code[i + 1];

    // 处理转义字符
    if (escape) {
      current += char;
      escape = false;
      continue;
    }

    if (char === "\\") {
      current += char;
      escape = true;
      continue;
    }

    // 处理行注释 //
    if (!inString && !inComment && char === "/" && next === "/") {
      inLineComment = true;
    }
    if (inLineComment) {
      current += char;
      if (char === "\n") {
        inLineComment = false;
        result.push(current);
        current = "";
      }
      continue;
    }

    // 处理块注释 /*
    if (!inString && !inComment && char === "/" && next === "*") {
      inComment = true;
      current += char;
      continue;
    }
    if (inComment) {
      current += char;
      if (char === "*" && next === "/") {
        current += next;
        i++;
        inComment = false;
      }
      continue;
    }

    // 处理字符串
    if (!inComment) {
      if (inString) {
        if (char === inString) {
          inString = null;
        }
        current += char;
        continue;
      } else {
        if (char === "'" || char === '"' || char === "`") {
          inString = char;
          current += char;
          continue;
        }
      }
    }

    // 处理分隔符
    if (!inString && !inComment && delimiters.includes(char)) {
      current += char;
      if (char === "{") {
        current += "\n";
        result.push(current);
        current = "";
      } else if (char === "}") {
        result.push(current);
        current = "}\n";
      } else if (char === ";") {
        // 只在行内有多个语句时换行
        const remaining = code.substring(i + 1);
        if (remaining.trim() && !remaining.trimStart().startsWith("}")) {
          current += "\n";
          result.push(current);
          current = "";
        }
      }
      continue;
    }

    current += char;
  }

  if (current) result.push(current);

  return result.join("");
}
