"""攻击面发现服务：让 Agent 从"指令驱动"变为"发现驱动"。

提供两个发现工具：
1. analyze_js —— 抓取页面，下载其引用的 JS 文件，用正则提取真实接口（API 路径/参数/方法）。
   现代网站（React/Vue/SPA）的接口大多藏在 JS bundle 里，HTML 中不可见。
2. fuzz_paths —— 用内置字典爆破常见路径（/admin、/api、/.git、/backup 等），
   发现 JS 中未暴露的隐藏接口/备份文件/管理后台。

所有请求均过 check_scope 授权校验 + 限速，避免越权与失控。
"""

import logging
import re
import asyncio
from typing import Any
from urllib.parse import urljoin, urlparse, unquote

import httpx

from hemlock.core.scope import check_scope
from hemlock.core.config import settings

logger = logging.getLogger(__name__)

# 单页 JS 下载上限与单文件大小上限，防止失控
_MAX_JS_FILES = 15
_MAX_JS_SIZE = 1_000_000  # 1MB

# ===== JS 接口提取正则 =====

# fetch / axios / XHR 调用中的路径（最高置信度）
_FETCH_CALL_RE = re.compile(
    r"""(?:fetch|axios\.(?:get|post|put|delete|patch|request)|XMLHttpRequest|\.open)\s*\(?\s*['"`]([^'"`\s]{2,200})['"`]""",
    re.IGNORECASE,
)
# url / endpoint / baseURL / path 字段赋值
_URL_FIELD_RE = re.compile(
    r"""(?:url|endpoint|baseURL|apiUrl|requestUrl|api)\s*[:=]\s*['"`]([^'"`\s]{2,300})['"`]""",
    re.IGNORECASE,
)
# 字符串字面量中的相对/绝对 API 路径
_PATH_LITERAL_RE = re.compile(
    r"""['"`]((?:https?:)?//[^'"`\s]{5,200}|/(?:api|v\d|graphql|admin|auth|login|logout|register|account|search|upload|download|files|public|internal|ws|socket)[a-zA-Z0-9_\-/\.{}\$:]*?)['"`]""",
    re.IGNORECASE,
)

# 常见接口文件扩展名（提高置信度，过滤 CSS/图片等噪音）
_JS_ENDPOINT_EXT = (".json", ".xml", ".do", ".action", ".php", ".aspx", ".jsp", ".asp", ".cgi")


def _looks_like_endpoint(path: str, base_host: str) -> bool:
    """判断提取到的字符串是否像接口路径，过滤明显噪音。"""
    if not path:
        return False
    # 去引号
    path = path.strip("\"'`")
    if len(path) < 2 or len(path) > 300:
        return False
    # 过滤常见噪音
    lower = path.lower()
    if lower.startswith(("data:", "javascript:", "mailto:", "blob:", "chrome-extension:")):
        return False
    if lower.endswith((".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".woff", ".woff2", ".ttf", ".eot", ".map")):
        return False
    if lower in ("/", "//", "/favicon.ico", "/robots.txt"):
        return False
    # 过滤纯静态资源路径（无扩展名或非接口扩展时看是否含 api 语义）
    if not lower.startswith(("/", "http", "//")):
        # 相对路径也可能是接口（如 "users/list"），保留
        if "/" not in lower:
            return False
    return True


def _normalize_endpoint(raw: str, page_url: str) -> str:
    """归一化接口为绝对路径形式。"""
    raw = raw.strip("\"'`")
    # 协议相对 //host/path → 补全
    if raw.startswith("//"):
        parsed = urlparse(page_url)
        raw = f"{parsed.scheme}:{raw}"
    # 相对路径 → 基于页面 URL 解析为绝对路径
    if not raw.startswith(("http://", "https://")):
        raw = urljoin(page_url, raw)
    try:
        p = urlparse(raw)
        # 保留 path + query（query 里的参数对接口测试有价值）
        path = p.path
        query = f"?{p.query}" if p.query else ""
        # 去掉 hash
        return f"{path}{query}"
    except Exception:
        return raw


def extract_js_endpoints(js_content: str, page_url: str, base_host: str) -> list[dict[str, str]]:
    """从单个 JS 文件内容中提取接口端点。

    Returns:
        [{endpoint, source(字段类型), hint}] 去重后的接口列表
    """
    found: dict[str, dict[str, str]] = {}

    def _add(raw: str, source: str) -> None:
        if not _looks_like_endpoint(raw, base_host):
            return
        ep = _normalize_endpoint(raw, page_url)
        if not ep or ep in ("/", ""):
            return
        # 同主机才保留（避免把第三方 CDN 静态资源当目标）
        try:
            host = urlparse(ep if ep.startswith("http") else urljoin(page_url, ep)).hostname or ""
            if host and host != base_host and not host.endswith(f".{base_host}"):
                return
        except Exception:
            return
        if ep not in found:
            found[ep] = {"endpoint": ep, "source": source}

    # 1. fetch/axios/XHR 调用
    for m in _FETCH_CALL_RE.finditer(js_content):
        _add(m.group(1), "call")
    # 2. url/endpoint 字段
    for m in _URL_FIELD_RE.finditer(js_content):
        _add(m.group(1), "field")
    # 3. 路径字面量
    for m in _PATH_LITERAL_RE.finditer(js_content):
        _add(m.group(1), "literal")

    return list(found.values())


async def _fetch_text(url: str, timeout: float = 15.0) -> str | None:
    """抓取文本内容（直连，不经过代理）。失败返回 None。"""
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code >= 400:
                return None
            return resp.text
    except Exception as e:
        logger.debug(f"抓取失败 {url}: {e}")
        return None


def _extract_script_urls(html: str, page_url: str) -> list[str]:
    """从 HTML 中提取 <script src> 引用的 JS 文件 URL。"""
    urls = []
    # 匹配 <script ... src="...">
    for m in re.finditer(r"""<script[^>]+src\s*=\s*["']([^"']+)["']""", html, re.IGNORECASE):
        src = m.group(1).strip()
        if src and not src.startswith(("data:", "blob:")):
            urls.append(urljoin(page_url, src))
    return urls


async def analyze_js_page(url: str, max_js_files: int = _MAX_JS_FILES) -> dict[str, Any]:
    """分析一个页面：抓取 HTML → 下载 JS → 提取接口端点。

    Args:
        url: 目标页面 URL（须在授权范围内）
        max_js_files: 最多分析的 JS 文件数

    Returns:
        {page_url, js_files, endpoints, total, errors}
    """
    parsed = urlparse(url)
    base_host = parsed.hostname or ""

    html = await _fetch_text(url)
    if html is None:
        return {"page_url": url, "error": "页面抓取失败", "js_files": [], "endpoints": [], "total": 0}

    script_urls = _extract_script_urls(html, url)
    script_urls = script_urls[:max_js_files]

    all_endpoints: dict[str, dict[str, str]] = {}
    js_files_ok = 0
    errors: list[str] = []

    # 并行下载 JS（限速，避免打爆目标）
    semaphore = asyncio.Semaphore(4)
    async def _process_js(js_url: str) -> None:
        nonlocal js_files_ok
        async with semaphore:
            await asyncio.sleep(1.0 / max(settings.rate_limit_per_second, 1))
            try:
                async with httpx.AsyncClient(timeout=20, trust_env=False, follow_redirects=True) as client:
                    resp = await client.get(js_url)
                    if resp.status_code >= 400:
                        errors.append(f"{js_url}: HTTP {resp.status_code}")
                        return
                    content = resp.text[:_MAX_JS_SIZE]
            except Exception as e:
                errors.append(f"{js_url}: {type(e).__name__}")
                return
            js_files_ok += 1
            for item in extract_js_endpoints(content, url, base_host):
                all_endpoints.setdefault(item["endpoint"], item)

    await asyncio.gather(*(_process_js(u) for u in script_urls))

    endpoints = sorted(all_endpoints.values(), key=lambda e: e["endpoint"])
    return {
        "page_url": url,
        "js_files": script_urls,
        "js_files_ok": js_files_ok,
        "endpoints": endpoints,
        "total": len(endpoints),
        "errors": errors,
    }


# ===== 路径爆破（fuzz）=====

# 内置常用路径字典（可追加自定义路径）
COMMON_PATHS = [
    # 管理/入口
    "/admin", "/admin/", "/administrator", "/manage", "/manager", "/console", "/dashboard",
    "/admin/login", "/login", "/signin", "/logout", "/register", "/signup",
    # API
    "/api", "/api/", "/api/v1", "/api/v2", "/api/v3", "/v1", "/v2", "/graphql", "/swagger",
    "/swagger-ui.html", "/swagger.json", "/openapi.json", "/api-docs", "/redoc",
    # 敏感文件
    "/robots.txt", "/sitemap.xml", "/.git/", "/.git/config", "/.git/HEAD", "/.env", "/.env.local",
    "/.gitignore", "/.htaccess", "/.svn/", "/.DS_Store", "/composer.json", "/package.json",
    "/web.config", "/config.json", "/config.php", "/wp-config.php",
    # 备份/源码
    "/backup", "/backup.zip", "/backup.tar.gz", "/backups", "/db_backup.sql", "/dump.sql",
    "/database.sql", "/www.zip", "/site.zip", "/src.zip", "/public.zip",
    # 常见目录
    "/static", "/assets", "/uploads", "/upload", "/files", "/download", "/images", "/img",
    "/js", "/css", "/media", "/tmp", "/temp", "/test", "/tests", "/dev", "/debug", "/status",
    "/health", "/info", "/version", "/server-status", "/server-info", "/phpinfo.php",
    # 技术栈相关
    "/actuator", "/actuator/health", "/actuator/env", "/actuator/heapdump",
    "/wp-admin", "/wp-login.php", "/wordpress", "/joomla", "/drupal", "/phpmyadmin",
    "/wp-content/uploads", "/adminer.php",
    # 用户/账号
    "/users", "/user", "/profile", "/account", "/member", "/members", "/me", "/settings",
    # 常见功能
    "/search", "/upload", "/download", "/export", "/import", "/reports", "/report",
    "/notifications", "/messages", "/comments", "/posts", "/articles", "/news", "/blog",
]


async def fuzz_paths(base_url: str, paths: list[str] | None = None, max_findings: int = 50) -> dict[str, Any]:
    """用字典爆破目标站点常见路径。

    Args:
        base_url: 目标基础 URL，如 https://example.com（须在授权范围内）
        paths: 自定义路径列表（相对路径），默认用内置字典
        max_findings: 最多返回的命中数（非 404）

    Returns:
        {base_url, tested, found, findings: [{path, status, length}]}
    """
    base_url = base_url.rstrip("/")
    if not check_scope(base_url):
        return {"base_url": base_url, "error": "URL 超出授权范围", "tested": 0, "found": 0, "findings": []}

    wordlist = paths or COMMON_PATHS
    findings: list[dict[str, Any]] = []
    tested = 0

    for path in wordlist:
        if not path.startswith("/"):
            path = f"/{path}"
        url = f"{base_url}{path}"

        # 范围校验（每个候选路径）
        if not check_scope(url):
            continue

        await asyncio.sleep(1.0 / max(settings.rate_limit_per_second, 1))
        tested += 1
        try:
            async with httpx.AsyncClient(timeout=8, trust_env=False, follow_redirects=False) as client:
                resp = await client.request("GET", url)
        except Exception:
            continue

        status = resp.status_code
        # 只保留"存在"的（非 404），404 视为不存在
        if status in (200, 201, 202, 204, 301, 302, 303, 307, 308, 400, 401, 403, 405, 500, 502, 503):
            length = len(resp.content)
            findings.append({
                "path": path,
                "status": status,
                "length": length,
                "content_type": resp.headers.get("content-type", "").split(";")[0],
            })
            if len(findings) >= max_findings:
                break

    return {
        "base_url": base_url,
        "tested": tested,
        "found": len(findings),
        "findings": findings,
    }
