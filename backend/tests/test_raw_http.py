"""原始 HTTP 包解析/应用模块测试。"""

from hemlock.services.raw_http import (
    parse_raw_request,
    parse_raw_response,
    build_raw_request,
    build_raw_response,
    build_raw_request_from_flow,
    build_raw_response_from_flow,
    apply_raw_request,
    apply_raw_response,
)


# ---------- 解析 ----------

def test_parse_raw_request_relpath():
    r = parse_raw_request("GET /a?b=1 HTTP/1.1\nHost: example.com\nUser-Agent: t\n\nhello")
    assert r["method"] == "GET"
    assert r["url"] == "https://example.com/a?b=1"
    assert r["body"] == "hello"
    assert r["scheme"] == "https"


def test_parse_raw_request_absolute():
    r = parse_raw_request("POST http://api.test:8080/x HTTP/1.1\nContent-Type: application/json\n\n{}")
    assert r["scheme"] == "http"
    assert r["url"] == "http://api.test:8080/x"


def test_parse_raw_request_no_host_returns_none():
    assert parse_raw_request("GET / HTTP/1.1\nX: 1") is None


def test_parse_raw_request_crlf():
    r = parse_raw_request("GET /x HTTP/1.1\r\nHost: h.com\r\n\r\nbody")
    assert r["url"] == "https://h.com/x"
    assert r["body"] == "body"


def test_build_parse_roundtrip():
    raw = build_raw_request("GET", "/x?a=1", "HTTP/1.1", "Host: h.com\nAccept: */*", "body")
    r = parse_raw_request(raw)
    assert r["url"] == "https://h.com/x?a=1"
    assert r["body"] == "body"


# ---------- 响应 ----------

def test_parse_raw_response_variants():
    for sl in ["HTTP/1.1 200 OK", "HTTP/1.1 201", "200 No Content", "404"]:
        p = parse_raw_response(sl + "\nServer: nginx\n\nbody")
        assert p["status"] in (200, 201, 404)
    p = parse_raw_response("HTTP/1.1 200 OK\nServer: nginx\n\nbody")
    assert p["reason"] == "OK" and p["body"] == "body" and p["http_version"] == "1.1"


def test_build_raw_response_roundtrip():
    raw = build_raw_response(200, "OK", "1.1", "Content-Type: text/html", "<b>x</b>")
    p = parse_raw_response(raw)
    assert p["status"] == 200 and p["reason"] == "OK" and p["body"] == "<b>x</b>"


# ---------- 应用到 flow ----------

def _make_flow():
    """构造一个带 request + response 的 mitmproxy HTTPFlow。"""
    from mitmproxy.connection import Client, Server
    from mitmproxy.http import HTTPFlow
    from mitmproxy.test import tutils
    flow = HTTPFlow(
        Client(peername=("127.0.0.1", 1234), sockname=("127.0.0.1", 8080)),
        Server(peername=("93.184.216.34", 80), address=("example.com", 80)),
    )
    flow.request = tutils.treq()
    flow.response = tutils.tresp()
    return flow


def test_apply_raw_request():
    flow = _make_flow()
    raw = "POST /submit HTTP/1.1\nHost: example.com\nX-Test: 1\nTransfer-Encoding: chunked\n\nname=alice"
    assert apply_raw_request(flow, raw)
    req = flow.request
    assert req.method == "POST"
    # tflow() 默认 scheme 为 http
    assert req.url == "http://example.com/submit"
    assert req.headers.get("X-Test") == "1"
    assert req.get_text() == "name=alice"
    # Content-Length 重写、Transfer-Encoding 删除
    assert req.headers.get("Content-Length") == str(len(b"name=alice"))
    assert "Transfer-Encoding" not in req.headers


def test_apply_raw_request_adds_host():
    flow = _make_flow()
    raw = "GET /p HTTP/1.1\nAccept: */*"
    assert apply_raw_request(flow, raw)
    assert flow.request.headers.get("Host")


def test_apply_raw_request_bad_returns_false():
    flow = _make_flow()
    assert apply_raw_request(flow, "not a request") is False


def test_apply_raw_response():
    flow = _make_flow()
    raw = "HTTP/1.1 302 Found\nLocation: /new\n\nredirected"
    assert apply_raw_response(flow, raw)
    resp = flow.response
    assert resp.status_code == 302
    assert resp.reason == "Found"
    assert resp.get_text() == "redirected"
    assert resp.headers.get("Location") == "/new"
    assert resp.headers.get("Content-Length") == str(len(b"redirected"))


def test_apply_raw_response_bad_returns_false():
    flow = _make_flow()
    assert apply_raw_response(flow, "not a response") is False


# ---------- 从 flow 构建 ----------

def test_build_raw_from_flow_roundtrip():
    flow = _make_flow()
    raw = build_raw_request_from_flow(flow)
    assert raw.startswith(f"{flow.request.method} {flow.request.path} HTTP/")
    p = parse_raw_request(raw, flow.request.scheme)
    assert p["url"] == flow.request.pretty_url


def test_build_raw_response_from_flow():
    flow = _make_flow()
    raw = build_raw_response_from_flow(flow)
    assert raw.startswith("HTTP/1.1 200")
    p = parse_raw_response(raw)
    assert p["status"] == flow.response.status_code
