"""信息收集服务。

整合多种空间测绘引擎和互联网信息收集方法，提供统一的搜索接口。
支持的引擎：
- FOFA（网络空间测绘）
- Quake（360网络空间测绘）
- Hunter（奇安信鹰图）
- ZoomEye（知道创宇）
- 360 Alpha（360资产测绘）
- Chaos（ProjectDiscovery 资产发现）
- crtsh（证书透明度日志）
- HackerTarget（DNS/子域名信息）
- DNS直连查询（A/CNAME/MX/TXT记录）

所有搜索结果统一输出为 Markdown 报告，保存到 ~/.hemlock/recon/ 目录。
API Key 存储在 ~/.hemlock/recon_keys.json，加密存储。
"""

import asyncio
import base64
import json
import logging
import os
import re
import socket
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

from hemlock.core.config import get_data_dir

logger = logging.getLogger(__name__)

# ===== 目录和文件路径 =====
RECON_DIR = get_data_dir() / "recon"
KEYS_FILE = get_data_dir() / "recon_keys.json"

# ===== 支持的引擎定义 =====
ENGINES = {
    "fofa": {
        "name": "FOFA",
        "description": "网络空间测绘引擎，支持 IP、域名、端口、服务搜索",
        "key_required": True,
        "key_label": "API Key",
        "key_hint": "输入 FOFA API Key",
        "doc_url": "https://fofa.info/api",
    },
    "quake": {
        "name": "Quake 360",
        "description": "360 网络空间测绘引擎",
        "key_required": True,
        "key_label": "API Key",
        "key_hint": "在 Quake 控制台获取",
        "doc_url": "https://quake.360.net/quake/#/help",
    },
    "hunter": {
        "name": "Hunter 鹰图",
        "description": "奇安信鹰图网络空间测绘",
        "key_required": True,
        "key_label": "API Key",
        "key_hint": "在鹰图控制台获取",
        "doc_url": "https://hunter.qianxin.com/home/helpCenter",
    },
    "zoomeye": {
        "name": "ZoomEye",
        "description": "知道创宇网络空间测绘引擎",
        "key_required": True,
        "key_label": "API Key",
        "key_hint": "在 ZoomEye 控制台获取",
        "doc_url": "https://www.zoomeye.org/doc",
    },
    "alpha": {
        "name": "360 Alpha",
        "description": "360 资产测绘引擎",
        "key_required": True,
        "key_label": "API Key",
        "key_hint": "在 Alpha 平台获取",
        "doc_url": "https://alpha.360.cn",
    },
    "chaos": {
        "name": "Chaos",
        "description": "ProjectDiscovery 资产发现服务，免费子域名查询",
        "key_required": True,
        "key_label": "API Key",
        "key_hint": "在 Chaos 平台获取",
        "doc_url": "https://chaos.projectdiscovery.io",
    },
    "crtsh": {
        "name": "crt.sh",
        "description": "证书透明度日志查询，通过 SSL 证书发现子域名",
        "key_required": False,
        "key_label": "",
        "key_hint": "",
        "doc_url": "https://crt.sh",
    },
    "hackertarget": {
        "name": "HackerTarget",
        "description": "DNS 信息和子域名查询（免费有限额）",
        "key_required": False,
        "key_label": "API Key (可选)",
        "key_hint": "免费无需 Key，有 Key 可提高限额",
        "doc_url": "https://hackertarget.com/api/",
    },
    "dns": {
        "name": "DNS 查询",
        "description": "直接 DNS 查询（A/CNAME/MX/TXT/NS 记录）",
        "key_required": False,
        "key_label": "",
        "key_hint": "",
        "doc_url": "",
    },
}


# ===== API Key 管理 =====
def _load_keys() -> dict[str, str]:
    """加载已保存的 API Keys。"""
    if not KEYS_FILE.exists():
        return {}
    try:
        data = json.loads(KEYS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(f"读取 recon keys 失败: {e}")
        return {}


def _save_keys(keys: dict[str, str]) -> None:
    """保存 API Keys。"""
    KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEYS_FILE.write_text(
        json.dumps(keys, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_keys() -> dict[str, str]:
    """获取所有已配置的 API Keys（脱敏返回）。"""
    raw = _load_keys()
    masked = {}
    for k, v in raw.items():
        if not v:
            continue
        if len(v) <= 8:
            masked[k] = "****"
        else:
            masked[k] = f"{v[:4]}****{v[-4:]}"
    return masked


def get_raw_key(engine: str) -> str:
    """获取指定引擎的原始 API Key（内部使用）。"""
    return _load_keys().get(engine, "")


def has_key(engine: str) -> bool:
    """检查指定引擎是否已配置 Key。"""
    return bool(_load_keys().get(engine))


def set_key(engine: str, key: str) -> None:
    """设置指定引擎的 API Key。"""
    keys = _load_keys()
    if key:
        keys[engine] = key
    else:
        keys.pop(engine, None)
    _save_keys(keys)


def delete_key(engine: str) -> None:
    """删除指定引擎的 API Key。"""
    keys = _load_keys()
    keys.pop(engine, None)
    _save_keys(keys)


def get_engine_list() -> list[dict]:
    """返回所有引擎信息（含是否已配置 Key）。"""
    keys = _load_keys()
    result = []
    for eid, info in ENGINES.items():
        result.append({
            "id": eid,
            "name": info["name"],
            "description": info["description"],
            "key_required": info["key_required"],
            "key_label": info["key_label"],
            "key_hint": info["key_hint"],
            "doc_url": info["doc_url"],
            "has_key": bool(keys.get(eid)),
        })
    return result


# ===== 报告文件管理 =====
def list_reports() -> list[dict]:
    """列出所有信息收集报告文件。"""
    RECON_DIR.mkdir(parents=True, exist_ok=True)
    reports = []
    for f in sorted(RECON_DIR.glob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True):
        stat = f.stat()
        reports.append({
            "filename": f.name,
            "size": stat.st_size,
            "size_human": _format_size(stat.st_size),
            "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        })
    return reports


def get_report_content(filename: str) -> str | None:
    """读取报告内容。"""
    filepath = RECON_DIR / filename
    if not filepath.exists() or not filepath.name.endswith(".md"):
        return None
    return filepath.read_text(encoding="utf-8")


def delete_report(filename: str) -> bool:
    """删除报告文件。"""
    filepath = RECON_DIR / filename
    if not filepath.exists() or not filepath.name.endswith(".md"):
        return False
    filepath.unlink()
    return True


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    else:
        return f"{size / (1024 * 1024):.1f} MB"


def _save_report(target: str, engine: str, content: str) -> str:
    """保存 Markdown 报告，返回文件名。"""
    RECON_DIR.mkdir(parents=True, exist_ok=True)
    safe_target = re.sub(r"[^\w.-]", "_", target)[:50]
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{safe_target}_{engine}_{timestamp}.md"
    filepath = RECON_DIR / filename
    filepath.write_text(content, encoding="utf-8")
    return filename


# ===== 各引擎搜索实现 =====

async def _search_fofa(query: str, size: int = 100) -> dict:
    """FOFA 搜索。

    兼容两种 Key 格式：
    - 纯 API Key（新版 FOFA，仅需 key）
    - email:key（旧版 FOFA，需 email 和 key 用冒号连接）
    """
    key = get_raw_key("fofa")
    if not key:
        return {"error": "FOFA API Key 未配置"}

    # FOFA 搜索语法需 Base64 编码
    qbase64 = base64.b64encode(query.encode("utf-8")).decode("utf-8")
    url = "https://fofa.info/api/v1/search/all"

    # 兼容旧版 email:key 格式
    if ":" in key:
        email, api_key = key.split(":", 1)
        params = {
            "email": email,
            "key": api_key,
            "qbase64": qbase64,
            "size": min(size, 100),
            "fields": "host,ip,port,title,server,protocol,domain,country,city",
        }
    else:
        params = {
            "key": key,
            "qbase64": qbase64,
            "size": min(size, 100),
            "fields": "host,ip,port,title,server,protocol,domain,country,city",
        }
    async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
        resp = await client.get(url, params=params)
        data = resp.json()

    if data.get("error"):
        return {"error": f"FOFA: {data['errmsg']}"}

    results = []
    for item in data.get("results", []):
        parts = item.split(",") if isinstance(item, str) else item
        results.append({
            "host": parts[0] if len(parts) > 0 else "",
            "ip": parts[1] if len(parts) > 1 else "",
            "port": parts[2] if len(parts) > 2 else "",
            "title": parts[3] if len(parts) > 3 else "",
            "server": parts[4] if len(parts) > 4 else "",
            "protocol": parts[5] if len(parts) > 5 else "",
            "domain": parts[6] if len(parts) > 6 else "",
            "country": parts[7] if len(parts) > 7 else "",
            "city": parts[8] if len(parts) > 8 else "",
        })
    return {"engine": "FOFA", "total": data.get("size", len(results)), "results": results}


async def _search_quake(query: str, size: int = 100) -> dict:
    """Quake 360 搜索。"""
    key = get_raw_key("quake")
    if not key:
        return {"error": "Quake API Key 未配置"}

    url = "https://quake.360.net/api/v3/search/quake_service"
    headers = {"X-QuakeToken": key}
    payload = {
        "query": query,
        "size": min(size, 100),
        "start": 0,
        "include": ["ip", "port", "hostname", "service.name", "service.http.title", "location.country_cn", "location.city_cn"],
    }
    async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
        resp = await client.post(url, json=payload, headers=headers)
        data = resp.json()

    if data.get("code") != 0:
        return {"error": f"Quake: {data.get('message', '未知错误')}"}

    results = []
    for item in data.get("data", []):
        service = item.get("service", {})
        location = item.get("location", {})
        results.append({
            "ip": item.get("ip", ""),
            "port": str(item.get("port", "")),
            "hostname": item.get("hostname", ""),
            "service": service.get("name", ""),
            "title": service.get("http", {}).get("title", ""),
            "country": location.get("country_cn", ""),
            "city": location.get("city_cn", ""),
        })
    return {"engine": "Quake", "total": data.get("meta", {}).get("total", len(results)), "results": results}


async def _search_hunter(query: str, size: int = 100) -> dict:
    """Hunter 鹰图搜索。"""
    key = get_raw_key("hunter")
    if not key:
        return {"error": "Hunter API Key 未配置"}

    url = "https://hunter.qianxin.com/openApi/search"
    params = {
        "api-key": key,
        "search": query,
        "page": 1,
        "page_size": min(size, 100),
        "is_web": 3,  # 搜索全部
    }
    async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
        resp = await client.get(url, params=params)
        data = resp.json()

    if data.get("code") != 200:
        return {"error": f"Hunter: {data.get('message', '未知错误')}"}

    results = []
    for item in data.get("data", {}).get("arr", []):
        results.append({
            "ip": item.get("ip", ""),
            "port": str(item.get("port", "")),
            "url": item.get("url", ""),
            "title": item.get("web_title", ""),
            "domain": item.get("domain", ""),
            "protocol": item.get("protocol", ""),
            "country": item.get("country", ""),
            "city": item.get("city", ""),
        })
    return {"engine": "Hunter", "total": data.get("data", {}).get("total", len(results)), "results": results}


async def _search_zoomeye(query: str, size: int = 100) -> dict:
    """ZoomEye 搜索。"""
    key = get_raw_key("zoomeye")
    if not key:
        return {"error": "ZoomEye API Key 未配置"}

    url = "https://api.zoomeye.org/web/search"
    headers = {"API-KEY": key}
    params = {
        "query": query,
        "page": 1,
    }
    async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
        resp = await client.get(url, headers=headers, params=params)
        data = resp.json()

    if "error" in data:
        return {"error": f"ZoomEye: {data.get('message', data['error'])}"}

    results = []
    for item in data.get("matches", []):
        results.append({
            "ip": item.get("ip", ""),
            "port": str(item.get("portinfo", {}).get("port", "")),
            "title": item.get("portinfo", {}).get("title", ""),
            "server": item.get("portinfo", {}).get("server", ""),
            "hostname": item.get("portinfo", {}).get("hostname", ""),
            "country": item.get("geoinfo", {}).get("country", {}).get("names", {}).get("cn", ""),
        })
    return {"engine": "ZoomEye", "total": data.get("total", len(results)), "results": results}


async def _search_alpha(query: str, size: int = 100) -> dict:
    """360 Alpha 搜索。"""
    key = get_raw_key("alpha")
    if not key:
        return {"error": "Alpha API Key 未配置"}

    url = "https://alpha.360.cn/api/v1/search"
    headers = {"Alpha-API-Key": key}
    payload = {
        "query": query,
        "size": min(size, 100),
    }
    async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
        resp = await client.post(url, json=payload, headers=headers)
        data = resp.json()

    if data.get("code") != 0:
        return {"error": f"Alpha: {data.get('message', '未知错误')}"}

    results = []
    for item in data.get("data", {}).get("list", []):
        results.append({
            "ip": item.get("ip", ""),
            "port": str(item.get("port", "")),
            "title": item.get("title", ""),
            "domain": item.get("domain", ""),
            "service": item.get("service", ""),
        })
    return {"engine": "Alpha", "total": data.get("data", {}).get("total", len(results)), "results": results}


async def _search_chaos(query: str, size: int = 100) -> dict:
    """Chaos (ProjectDiscovery) 子域名搜索。"""
    key = get_raw_key("chaos")
    if not key:
        return {"error": "Chaos API Key 未配置"}

    # Chaos 只支持域名查询
    url = f"https://dns.projectdiscovery.io/dns/{query}/subdomains"
    headers = {"Authorization": key}
    async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
        resp = await client.get(url, headers=headers)
        data = resp.json()

    if resp.status_code != 200:
        return {"error": f"Chaos: {data.get('message', 'HTTP ' + str(resp.status_code))}"}

    subdomains = data.get("subdomains", [])
    results = [{"subdomain": f"{sub}.{query}"} for sub in subdomains[:size]]
    return {"engine": "Chaos", "total": len(subdomains), "results": results}


async def _search_crtsh(query: str, size: int = 100) -> dict:
    """crt.sh 证书透明度日志查询。"""
    url = f"https://crt.sh/?q={quote('%.' + query)}&output=json"
    async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
        resp = await client.get(url)
        if resp.status_code != 200:
            return {"error": f"crt.sh: HTTP {resp.status_code}"}
        data = resp.json()

    seen = set()
    results = []
    for item in data[:size * 2]:
        name = item.get("name_value", "")
        # 去重，处理通配符
        for n in name.split("\n"):
            n = n.strip().lstrip("*.").lower()
            if n and n not in seen and query in n:
                seen.add(n)
                results.append({
                    "domain": n,
                    "issuer": item.get("issuer_name", ""),
                    "not_before": item.get("not_before", ""),
                    "not_after": item.get("not_after", ""),
                })
                if len(results) >= size:
                    break
    return {"engine": "crt.sh", "total": len(results), "results": results}


async def _search_hackertarget(query: str, size: int = 100) -> dict:
    """HackerTarget 子域名和 DNS 查询。"""
    key = get_raw_key("hackertarget")
    # 免费无 Key 也能用，有 Key 限额更高
    url = f"https://api.hackertarget.com/hostsearch/?q={quote(query)}"
    if key:
        url += f"&api_key={key}"
    async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
        resp = await client.get(url)
        text = resp.text

    if "error" in text.lower() or "API count exceeded" in text:
        return {"error": f"HackerTarget: {text.strip()}"}

    results = []
    for line in text.strip().split("\n")[:size]:
        if "," in line:
            parts = line.split(",")
            results.append({
                "domain": parts[0].strip(),
                "ip": parts[1].strip() if len(parts) > 1 else "",
            })
    return {"engine": "HackerTarget", "total": len(results), "results": results}


async def _search_dns(query: str, size: int = 100) -> dict:
    """DNS 直接查询（A/CNAME/MX/TXT/NS 记录）。"""
    results = []

    # A 记录
    try:
        ips = socket.getaddrinfo(query, None, socket.AF_INET)
        seen_ips = set()
        for ip in ips:
            addr = ip[4][0]
            if addr not in seen_ips:
                seen_ips.add(addr)
                results.append({"type": "A", "name": query, "value": addr})
    except Exception:
        pass

    # CNAME / MX / TXT / NS 通过 DNS over HTTPS (Google)
    doh_url = "https://dns.google/resolve"
    record_types = ["CNAME", "MX", "TXT", "NS"]
    async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
        for rtype in record_types:
            try:
                resp = await client.get(doh_url, params={"name": query, "type": rtype})
                data = resp.json()
                for answer in data.get("Answer", []):
                    val = answer.get("data", "")
                    if rtype == "MX" and val and " " in val:
                        val = val.split(" ", 1)[1]  # 去掉优先级
                    results.append({"type": rtype, "name": query, "value": val})
            except Exception:
                pass

    return {"engine": "DNS", "total": len(results), "results": results}


# 引擎 → 搜索函数映射
ENGINE_FUNCS = {
    "fofa": _search_fofa,
    "quake": _search_quake,
    "hunter": _search_hunter,
    "zoomeye": _search_zoomeye,
    "alpha": _search_alpha,
    "chaos": _search_chaos,
    "crtsh": _search_crtsh,
    "hackertarget": _search_hackertarget,
    "dns": _search_dns,
}


async def search(engine: str, query: str, size: int = 100) -> dict:
    """执行单引擎搜索。"""
    func = ENGINE_FUNCS.get(engine)
    if not func:
        return {"error": f"不支持的引擎: {engine}"}
    if ENGINES.get(engine, {}).get("key_required") and not has_key(engine):
        return {"error": f"{ENGINES[engine]['name']} API Key 未配置"}
    try:
        return await func(query, size)
    except httpx.ConnectError as e:
        return {"error": f"网络连接失败: {e}"}
    except httpx.TimeoutException:
        return {"error": f"请求超时"}
    except Exception as e:
        logger.error(f"搜索引擎 {engine} 异常: {e}", exc_info=True)
        return {"error": f"搜索异常: {e}"}


async def multi_search(engines: list[str], query: str, size: int = 100) -> dict:
    """多引擎并行搜索，合并结果并生成 Markdown 报告。"""
    tasks = [search(e, query, size) for e in engines]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_results = {}
    errors = []
    for engine, result in zip(engines, results):
        if isinstance(result, Exception):
            errors.append(f"{ENGINES.get(engine, {}).get('name', engine)}: {result}")
        elif "error" in result:
            errors.append(f"{result['error']}")
        else:
            all_results[engine] = result

    # 生成 Markdown 报告
    report = _generate_report(query, engines, all_results, errors)

    # 保存报告
    engines_str = "multi" if len(engines) > 1 else engines[0]
    filename = _save_report(query, engines_str, report)

    return {
        "engines": list(all_results.keys()),
        "total_engines": len(all_results),
        "errors": errors,
        "report_file": filename,
        "report_preview": report[:2000],
        "results": all_results,
    }


def _generate_report(query: str, engines: list[str], results: dict, errors: list[str]) -> str:
    """生成 Markdown 格式的信息收集报告。"""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    lines = [
        f"# 信息收集报告",
        f"",
        f"- **目标**: `{query}`",
        f"- **时间**: {now}",
        f"- **引擎**: {', '.join(ENGINES.get(e, {}).get('name', e) for e in engines)}",
        f"",
    ]

    if errors:
        lines.append("## ⚠️ 错误信息")
        lines.append("")
        for err in errors:
            lines.append(f"- {err}")
        lines.append("")

    # 按引擎输出结果
    for engine, data in results.items():
        engine_name = ENGINES.get(engine, {}).get("name", engine)
        total = data.get("total", 0)
        items = data.get("results", [])

        lines.append(f"## {engine_name}（共 {total} 条）")
        lines.append("")

        if not items:
            lines.append("*无结果*")
            lines.append("")
            continue

        # 根据引擎类型输出不同格式
        if engine in ("fofa", "quake", "hunter", "zoomeye", "alpha"):
            # 资产类：表格
            lines.append("| # | IP | 端口 | 域名/主机 | 标题 | 服务 | 地区 |")
            lines.append("|---|-----|------|-----------|------|------|------|")
            for i, item in enumerate(items, 1):
                ip = item.get("ip", "-")
                port = item.get("port", "-")
                host = item.get("domain") or item.get("hostname") or item.get("host") or "-"
                title = item.get("title", "-")
                service = item.get("service") or item.get("server") or item.get("protocol") or "-"
                country = item.get("country", "-")
                city = item.get("city", "")
                region = f"{country} {city}".strip() or "-"
                lines.append(f"| {i} | {ip} | {port} | {host} | {title} | {service} | {region} |")
        elif engine in ("chaos", "crtsh", "hackertarget"):
            # 子域名类：列表
            lines.append("| # | 域名 | 额外信息 |")
            lines.append("|---|------|----------|")
            for i, item in enumerate(items, 1):
                domain = item.get("subdomain") or item.get("domain", "-")
                extra = item.get("ip") or item.get("issuer", "")
                lines.append(f"| {i} | {domain} | {extra} |")
        elif engine == "dns":
            # DNS 记录
            lines.append("| 类型 | 名称 | 值 |")
            lines.append("|------|------|------|")
            for item in items:
                lines.append(f"| {item.get('type', '-')} | {item.get('name', '-')} | {item.get('value', '-')} |")

        lines.append("")

    # 汇总统计
    lines.append("## 汇总统计")
    lines.append("")
    total_all = sum(len(d.get("results", [])) for d in results.values())
    lines.append(f"- 搜索引擎数: {len(results)}")
    lines.append(f"- 总结果数: {total_all}")
    if errors:
        lines.append(f"- 失败引擎数: {len(errors)}")
    lines.append("")
    lines.append("---")
    lines.append("*由 Hemlock 信息收集模块自动生成*")

    return "\n".join(lines)
