"""intel_service 测试：cve_lookup / web_search（mock HTTP，不依赖真实网络）。"""

import asyncio

import httpx
import pytest

from hemlock.services import intel_service


def _install_mock_client(monkeypatch, handler):
    """把 intel_service._make_client 替换为 MockTransport 客户端。"""
    transport = httpx.MockTransport(handler)

    def make_client(use_proxy=False):
        return httpx.AsyncClient(transport=transport, timeout=10)

    monkeypatch.setattr(intel_service, "_make_client", make_client)


def _nvd_response(cves: list[dict]) -> dict:
    """构造 NVD API v2 响应片段。"""
    return {
        "totalResults": len(cves),
        "vulnerabilities": [{"cve": c} for c in cves],
    }


def _nvd_cve(cve_id: str, desc: str, cvss: float, refs: list[str] | None = None) -> dict:
    return {
        "id": cve_id,
        "descriptions": [{"lang": "en", "value": desc}],
        "metrics": {"cvssMetricV31": [{"cvssData": {"baseScore": cvss}}]},
        "references": [{"url": u} for u in (refs or [])],
    }


# ===== cve_lookup =====

def test_cve_lookup_parses_nvd_and_sorts_by_cvss(monkeypatch):
    """NVD 返回解析正确，按 CVSS 降序。"""
    def handler(request: httpx.Request) -> httpx.Response:
        assert "services.nvd.nist.gov" in str(request.url)
        return httpx.Response(200, json=_nvd_response([
            _nvd_cve("CVE-2021-44228", "Apache Log4j2 2.0-beta9 through 2.14.1 RCE", 10.0,
                     ["https://github.com/example/poc"]),
            _nvd_cve("CVE-2017-5645", "Apache Log4j 2.x TCP socket RCE", 9.8),
        ]))

    _install_mock_client(monkeypatch, handler)

    async def run():
        return await intel_service.cve_lookup("log4j")

    result = asyncio.run(run())
    assert result["source"] == "NVD API v2"
    assert result["total"] == 2
    ids = [c["id"] for c in result["cves"]]
    assert ids == ["CVE-2021-44228", "CVE-2017-5645"]  # cvss 10.0 在前
    assert result["cves"][0]["severity"] == "严重"
    assert result["cves"][0]["has_poc"] is True  # reference 含 github.com
    assert "_match_text" not in result["cves"][0]  # 内部字段已剥离


def test_cve_lookup_version_match_and_fallback(monkeypatch):
    """版本命中优先；未命中返回组件高危并标记。"""
    def handler(request):
        return httpx.Response(200, json=_nvd_response([
            _nvd_cve("CVE-2021-23017", "nginx resolver off-by-one (0.6.18 through 1.20.0)", 7.7),
            _nvd_cve("CVE-2022-41742", "nginx 1.23.1 specifically affected", 9.1),
        ]))

    _install_mock_client(monkeypatch, handler)

    async def run():
        return await intel_service.cve_lookup("nginx", "1.23.1")

    result = asyncio.run(run())
    ids = [c["id"] for c in result["cves"]]
    assert "CVE-2022-41742" in ids  # 版本命中优先
    assert "CVE-2021-23017" not in ids[:1] or ids[0] == "CVE-2022-41742"


def test_cve_lookup_empty_component():
    """空组件报错。"""
    async def run():
        return await intel_service.cve_lookup("  ")

    result = asyncio.run(run())
    assert "component 不能为空" in result["error"]


def test_cve_lookup_falls_back_to_circl_when_nvd_fails(monkeypatch):
    """NVD 网络失败 → circl 回退（vendor 映射 apache/log4j）。"""
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if "services.nvd.nist.gov" in str(request.url):
            raise httpx.ConnectError("network down", request=request)
        # circl 返回 CVE-JSON-5 新格式
        return httpx.Response(200, json={
            "total_count": 1,
            "results": {
                "nvd": [[
                    "CVE-2021-44228",
                    {"containers": {"cna": {
                        "descriptions": [{"lang": "en", "value": "Log4j RCE"}],
                        "metrics": [{"cvssV3_1": {"baseScore": 10.0}}],
                        "references": [{"url": "https://logging.apache.org/"}],
                        "affected": [{"versions": [{"version": "2.14.1", "status": "affected"}]}],
                    }}},
                ]]
            },
        })

    _install_mock_client(monkeypatch, handler)

    async def run():
        return await intel_service.cve_lookup("log4j", "2.14.1")

    result = asyncio.run(run())
    assert result["source"] == "cve.circl.lu"
    assert result["cves"][0]["id"] == "cve-2021-44228"  # circl 返回小写 CVE ID
    assert "/search/apache/log4j" in str(calls[1])  # 厂商映射生效


def test_cve_lookup_all_sources_fail(monkeypatch):
    """NVD 与 circl 都失败 → error。"""
    def handler(request):
        raise httpx.ConnectError("all down", request=request)

    _install_mock_client(monkeypatch, handler)

    async def run():
        return await intel_service.cve_lookup("nginx")

    result = asyncio.run(run())
    assert "error" in result


# ===== web_search =====

def test_web_search_parses_ddg_html(monkeypatch):
    """DDG HTML 解析：标题/摘要/uddg 解码。"""
    html = """
    <div class="result">
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fgithub.com%2Fkozmer%2Flog4j-shell-poc" rel="nofollow">GitHub PoC</a>
      <a class="result__snippet" href="//duckduckgo.com/l/?uddg=..." rel="nofollow">A Proof-Of-Concept for Log4Shell</a>
    </div>
    <div class="result">
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fen.wikipedia.org%2Fwiki%2FLog4Shell" rel="nofollow">Log4Shell</a>
      <a class="result__snippet" href="//duckduckgo.com/l/?uddg=..." rel="nofollow">Wikipedia article</a>
    </div>
    """

    def handler(request):
        assert "html.duckduckgo.com" in str(request.url)
        assert "log4j" in str(request.url)
        return httpx.Response(200, text=html)

    _install_mock_client(monkeypatch, handler)

    async def run():
        return await intel_service.web_search("log4j shell poc")

    result = asyncio.run(run())
    assert result["total"] == 2
    first = result["results"][0]
    assert first["title"] == "GitHub PoC"
    assert first["url"] == "https://github.com/kozmer/log4j-shell-poc"  # uddg 解码
    assert "Proof-Of-Concept" in first["snippet"]


def test_web_search_empty_query():
    """空查询报错。"""
    async def run():
        return await intel_service.web_search("  ")

    result = asyncio.run(run())
    assert "query 不能为空" in result["error"]


def test_intel_direct_fails_then_uses_upstream_proxy(monkeypatch):
    """直连失败（被墙）→ 自动改用上游代理重试，不是所有请求都强制走代理。"""
    calls = []

    def handler(request):
        if not calls:
            calls.append("direct")
            raise httpx.ConnectTimeout("wall", request=request)  # 第一次直连被墙
        calls.append("proxy-retry")
        return httpx.Response(200, json=_nvd_response([
            _nvd_cve("CVE-2021-44228", "Log4j RCE through 2.14.1", 10.0),
        ]))

    # 配置上游代理（模拟用户在工具里配了上游代理）
    monkeypatch.setattr(intel_service.settings, "upstream_proxy", "http://127.0.0.1:9999")
    _install_mock_client(monkeypatch, handler)

    async def run():
        return await intel_service.cve_lookup("log4j")

    result = asyncio.run(run())
    assert result["total"] == 1
    assert result["cves"][0]["id"] == "CVE-2021-44228"
    assert calls == ["direct", "proxy-retry"]  # 直连一次失败 + 代理一次成功


def test_intel_direct_fails_without_upstream_proxy(monkeypatch):
    """直连失败且未配上游代理 → 报错（不重试）。"""
    def handler(request):
        raise httpx.ConnectTimeout("wall", request=request)

    monkeypatch.setattr(intel_service.settings, "upstream_proxy", "")
    _install_mock_client(monkeypatch, handler)

    async def run():
        return await intel_service.cve_lookup("log4j")

    result = asyncio.run(run())
    assert "error" in result
