"""外部情报服务：给 Agent 联网搜集信息的能力（N-day 漏洞情报 + Web 搜索）。

提供两个情报工具：
1. cve_lookup —— 按「组件 + 版本」查询已知漏洞（CVE/N-day）。优先 cve.circl.lu（免费无 Key），
   失败回退 NVD API v2（免费限速）。返回 CVE 编号 / 严重度 / 描述 / PoC 线索。
2. web_search —— DuckDuckGo HTML 端点（免费无 Key），返回标题 / 摘要 / URL。

请求默认直连（信任系统环境代理 HTTP_PROXY/HTTPS_PROXY）；直连失败（如被墙）时
自动改用工具里配置的上游代理（upstream_proxy）重试一次——不是所有流量都强制走代理。
所有请求带超时 + 结果截断，避免失控与上下文爆炸。
"""

import asyncio
import logging
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from hemlock.core.config import settings

logger = logging.getLogger(__name__)

# cve.circl.lu（免费无 Key，CVE-JSON-5 结构化记录）
_CIRCL_CVE_API = "https://cve.circl.lu/api"
# NVD API v2（免费，限速约 5 req/s，无 Key 可用）
_NVD_API = "https://services.nvd.nist.gov/rest/json/cves/2.0"
# DuckDuckGo HTML 搜索（免费无 Key，需带 UA）
_DDG_HTML = "https://html.duckduckgo.com/html/"

# 结果上限与截断（省 token，关键信息保留）
_MAX_CVES = 8            # 单次返回 CVE 条数上限
_MAX_CVE_SUMMARY = 220   # 每条 CVE 描述截断长度
_MAX_WEB_RESULTS = 8     # web 搜索返回条目上限
_MAX_WEB_TEXT = 300      # 每条摘要截断长度
_REQUEST_TIMEOUT = 12.0

# PoC 线索关键词：命中即认为有公开利用
_POC_KEYWORDS = ("exploit-db", "github.com", "metasploit", "poc", "rapid7", "seebug")
# CVSS 度量键（按优先级）
_CVSS_KEYS = ("cvssV4_0", "cvssV3_1", "cvssV3_0", "cvssV2_0")
_DDG_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Hemlock/0.3"}


def _make_client(use_proxy: bool = False) -> httpx.AsyncClient:
    """构建 HTTP 客户端。

    默认直连（尊重系统环境代理 HTTP_PROXY/HTTPS_PROXY）；use_proxy=True 时
    显式走工具里配置的上游代理（settings.upstream_proxy，用户在代理设置页配置）。
    """
    kwargs: dict[str, Any] = {"timeout": _REQUEST_TIMEOUT, "trust_env": True}
    if use_proxy:
        upstream = getattr(settings, "upstream_proxy", "") or ""
        if upstream:
            kwargs["proxy"] = upstream
    return httpx.AsyncClient(**kwargs)


async def _get(url: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> httpx.Response:
    """直连优先，连接失败（如被墙 / 目标不可达）时自动改用上游代理重试一次。

    只有连接层错误（ConnectError / 超时）才触发代理兜底——HTTP 状态码错误
    （403/404 等）不重试，避免"上游代理也 403"的无效重试。
    """
    try:
        async with _make_client(use_proxy=False) as client:
            return await client.get(url, params=params, headers=headers)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
        upstream = getattr(settings, "upstream_proxy", "") or ""
        if not upstream:
            raise
        logger.info(f"直连 {url} 失败（{type(e).__name__}），改用上游代理重试")
        async with _make_client(use_proxy=True) as client:
            return await client.get(url, params=params, headers=headers)


def _severity_label(cvss: float | None) -> str:
    """CVSS 分数 → 严重度标签。"""
    if cvss is None:
        return "未知"
    if cvss >= 9.0:
        return "严重"
    if cvss >= 7.0:
        return "高危"
    if cvss >= 4.0:
        return "中危"
    return "低危"


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


async def _rate_limited_sleep() -> None:
    """按配置限速，避免请求失控。"""
    await asyncio.sleep(1.0 / max(settings.rate_limit_per_second, 1))


def _extract_cvss(metrics: list[dict] | None) -> float | None:
    """从 CVE 记录 metrics 中提取 CVSS baseScore。"""
    for m in metrics or []:
        for key in _CVSS_KEYS:
            val = m.get(key)
            if val:
                try:
                    return float(val.get("baseScore"))
                except (TypeError, ValueError):
                    continue
    return None


def _has_poc(summary: str, refs: list[str]) -> bool:
    text = (summary or "").lower()
    return any(k in text or any(k in r.lower() for r in refs) for k in _POC_KEYWORDS)


# ===== cve_lookup =====

# 常见组件 → (CPE vendor, CPE product)。厂商≠组件名的常见情况写死，其余兜底用 (component, component)。
# 例：log4j 的 CPE 厂商是 apache；用户说 "apache" 通常指 Apache HTTP Server。
_VENDOR_MAP: dict[str, tuple[str, str]] = {
    "log4j": ("apache", "log4j"),
    "log4j2": ("apache", "log4j"),
    "httpd": ("apache", "http_server"),
    "apache": ("apache", "http_server"),
    "tomcat": ("apache", "tomcat"),
    "struts": ("apache", "struts"),
    "spring": ("pivotal_software", "spring_framework"),
    "springframework": ("pivotal_software", "spring_framework"),
    "jackson": ("fasterxml", "jackson-databind"),
    "fastjson": ("alibaba", "fastjson"),
    "weblogic": ("oracle", "weblogic_server"),
    "mysql": ("oracle", "mysql"),
    "elasticsearch": ("elastic", "elasticsearch"),
    "kibana": ("elastic", "kibana"),
    "kubernetes": ("kubernetes", "kubernetes"),
    "docker": ("docker", "docker"),
    "node.js": ("nodejs", "node.js"),
    "postgresql": ("postgresql", "postgresql"),
}

_FETCH_BUDGET = 40  # 各来源最多抓取的条目数（用于客户端版本过滤）


async def cve_lookup(component: str, version: str = "", max_results: int = _MAX_CVES) -> dict[str, Any]:
    """按组件+版本查询已知漏洞。

    Args:
        component: 组件名（如 nginx / apache / wordpress / log4j）
        version:   版本号（如 1.18.0 / 2.4.49），留空查该组件全部
        max_results: 返回 CVE 条数上限

    Returns:
        cves / total / component / version / source / note，或 error
    """
    component = (component or "").strip().lower()
    if not component:
        return {"error": "component 不能为空"}

    # 1. 主源：NVD keywordSearch（resultsPerPage 大，client-side 按 CVSS 排序，能捞出历史高危）
    entries: list[dict[str, Any]] = []
    source = ""
    try:
        source, entries = await _cve_lookup_nvd(component)
    except Exception as e:
        logger.warning(f"NVD 查询失败，回退 cve.circl.lu: {type(e).__name__}: {e}")

    # 2. 回退：cve.circl.lu（映射表厂商 → 兜底 component/component）
    if not entries:
        candidates: list[tuple[str, str]] = []
        if component in _VENDOR_MAP:
            candidates.append(_VENDOR_MAP[component])
        if (component, component) not in candidates:
            candidates.append((component, component))
        for vendor, product in candidates:
            try:
                got, entries = await _cve_lookup_circl(vendor, product)
                source = got
                if entries:
                    break
            except Exception as e:
                logger.warning(f"cve.circl.lu {vendor}/{product} 失败: {type(e).__name__}: {e}")
                continue

    if not entries:
        return {"error": f"未查到 {component} 的已收录 CVE（可能组件名拼写有误，或情报源不可达）"}

    # 3. 按 CVSS 降序，客户端版本匹配
    entries.sort(key=lambda e: e["cvss"] if e["cvss"] is not None else -1, reverse=True)
    v = version.lower().strip()
    version_hit = True
    if v:
        matched = [e for e in entries if _entry_matches_version(e, v)]
        if matched:
            cves = matched[:max_results]
        else:
            cves = entries[:max_results]  # 未精确命中版本 → 返回组件高危，note 提示复核
            version_hit = False
    else:
        cves = entries[:max_results]

    # 4. 剥离内部匹配字段，只返回展示字段
    for c in cves:
        c.pop("_match_text", None)

    return {
        "cves": cves,
        "total": len(cves),
        "component": component,
        "version": version,
        "source": source,
        "note": (
            "版本匹配为尽力匹配（受影响版本 + 描述子串）"
            + ("；该版本未精确命中 CVE，以下为组件高危，请结合 web_search 复核影响范围。" if v and not version_hit else "；建议结合 web_search 复核利用条件。")
        ),
    }


def _entry_matches_version(entry: dict[str, Any], version: str) -> bool:
    """判断 CVE 是否影响指定版本（对受影响版本列表与描述做子串匹配）。"""
    match_text = entry.get("_match_text", "") or ""
    return version in match_text.lower()


def _cve_record_to_entry(cve_id: str, record: dict) -> dict[str, Any] | None:
    """CVE-JSON-5 记录 → 精简条目（含内部 _match_text 供版本过滤）。"""
    cna = (record.get("containers") or {}).get("cna") or {}
    descs = cna.get("descriptions") or []
    summary = " ".join(d.get("value", "") for d in descs if d.get("lang") == "en")
    if not summary:
        summary = " ".join(d.get("value", "") for d in descs)
    refs = [r.get("url", "") for r in (cna.get("references") or [])][:3]
    cvss = _extract_cvss(cna.get("metrics"))

    # 结构化受影响版本（containers.cna.affected[].versions[].version）
    affected_text = ""
    for aff in cna.get("affected") or []:
        for ver in aff.get("versions") or []:
            vv = ver.get("version") or ""
            if vv and vv not in ("*", "0", "-"):
                affected_text += vv.lower() + " "

    return {
        "id": cve_id,
        "cvss": cvss,
        "severity": _severity_label(cvss),
        "summary": _truncate(summary, _MAX_CVE_SUMMARY),
        "has_poc": _has_poc(summary, refs),
        "references": refs[:2],
        "_match_text": (affected_text + " " + summary).lower(),
    }


async def _cve_lookup_circl(vendor: str, product: str) -> tuple[str, list[dict[str, Any]]]:
    """cve.circl.lu：/api/search/{vendor}/{product}（CVE-JSON-5 新格式）。返回 (source, entries)。

    注意：该端点按日期倒序分页（历史高危可能不在首页），仅作 NVD 不可用时的回退；
    用 per_page 尽量多取，按 CVE ID 去重后由调用方按 CVSS 排序。
    """
    await _rate_limited_sleep()
    url = f"{_CIRCL_CVE_API}/search/{vendor}/{product}"
    resp = await _get(url, params={"per_page": 100})
    resp.raise_for_status()
    data = resp.json()

    results = data.get("results") if isinstance(data, dict) else None
    if not results:
        return "cve.circl.lu", []

    dedup: dict[str, dict[str, Any]] = {}
    for items in results.values():
        if not items:
            continue
        for item in items:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                cve_id = (item[0] or "").lower()
                entry = _cve_record_to_entry(cve_id, item[1])
                if entry and cve_id not in dedup:
                    dedup[cve_id] = entry

    entries = list(dedup.values())
    entries.sort(key=lambda e: e["cvss"] if e["cvss"] is not None else -1, reverse=True)
    return "cve.circl.lu", entries[:_FETCH_BUDGET]


async def _cve_lookup_nvd(component: str) -> tuple[str, list[dict[str, Any]]]:
    """NVD API v2 回退：keywordSearch（组件名，客户端版本过滤）。返回 (source, entries)。"""
    await _rate_limited_sleep()
    params = {"keywordSearch": component, "resultsPerPage": _FETCH_BUDGET}
    resp = await _get(_NVD_API, params=params)
    resp.raise_for_status()
    data = resp.json()

    entries = []
    for item in data.get("vulnerabilities", [])[:_FETCH_BUDGET]:
        cve = item.get("cve", {})
        desc = ""
        for d in cve.get("descriptions", []):
            if d.get("lang") == "en":
                desc = d["value"]
                break
        refs = [r.get("url", "") for r in cve.get("references", [])][:2]
        # NVD v2 metrics 结构：{"cvssMetricV31": [{cvssData: {baseScore}}], ...}
        cvss = None
        metrics = cve.get("metrics") or {}
        for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
            arr = metrics.get(key)
            if arr:
                try:
                    cvss = float(arr[0].get("cvssData", {}).get("baseScore"))
                except (TypeError, ValueError):
                    pass
                if cvss is not None:
                    break
        entries.append({
            "id": cve.get("id", ""),
            "cvss": cvss,
            "severity": _severity_label(cvss),
            "summary": _truncate(desc, _MAX_CVE_SUMMARY),
            "has_poc": _has_poc(desc, refs),
            "references": refs,
            "_match_text": desc.lower(),
        })
    return "NVD API v2", entries


# ===== web_search =====

def _decode_ddg_url(href: str) -> str:
    """DuckDuckGo 结果链接为跳转 URL（//duckduckgo.com/l/?uddg=xxx），解出真实 URL。"""
    if "uddg=" in href:
        query = parse_qs(urlparse(href).query)
        real = query.get("uddg", [""])[0]
        if real:
            return real
    return href if href.startswith("http") else f"https:{href}" if href.startswith("//") else href


async def web_search(query: str, max_results: int = _MAX_WEB_RESULTS) -> dict[str, Any]:
    """DuckDuckGo HTML 搜索（免费无 Key）。

    Args:
        query: 搜索关键词
        max_results: 返回条目上限

    Returns:
        results: [{title, snippet, url}]，或 error
    """
    query = (query or "").strip()
    if not query:
        return {"error": "query 不能为空"}

    await _rate_limited_sleep()
    resp = await _get(_DDG_HTML, params={"q": query, "kl": "us-en"}, headers=_DDG_HEADERS)
    resp.raise_for_status()
    html = resp.text

    links = re.findall(r'class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S)
    snips = re.findall(r'class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>', html, re.S)

    results = []
    for i, (href, title_html) in enumerate(links[:max_results]):
        title = re.sub(r"<[^>]+>", "", title_html).strip()
        snippet = re.sub(r"<[^>]+>", "", snips[i]).strip() if i < len(snips) else ""
        if not title:
            continue
        results.append({
            "title": _truncate(title, 100),
            "snippet": _truncate(snippet, _MAX_WEB_TEXT),
            "url": _decode_ddg_url(href),
        })

    if not results:
        return {
            "results": [],
            "total": 0,
            "note": "DuckDuckGo 未返回结果。可换关键词重试，或用 cve_lookup 查结构化 CVE。",
        }
    return {"results": results[:max_results], "total": len(results[:max_results])}
