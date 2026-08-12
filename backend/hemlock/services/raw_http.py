"""原始 HTTP 请求/响应 解析、拼接、应用到 mitmproxy flow。

拦截器（Intercept）编辑区展示的是 Burp 风格的原始包文本，放行时把编辑后的
raw 文本解析回字段并应用到 flow.request / flow.response。

格式与前端 `utils/httpParser.ts` 对齐：
  - `\n` 换行（兼容 `\r\n`）
  - 第一个空行分割 headers 区与 body
  - 请求行 `METHOD path?query HTTP/1.1`，host 放 Host 头
  - 响应状态行 `HTTP/1.1 200 OK` / `HTTP/1.1 200` / `200 OK` / `200` 均可解析

注意：HTTP/1.1 不会根据 body 自动重算 Content-Length，apply 时必须自己重写
Content-Length 并删除 Transfer-Encoding（与 Burp 的自动修正行为一致）。
"""

from typing import Optional


def _normalize_newlines(raw: str) -> str:
    """统一为 \n 换行。"""
    return raw.replace("\r\n", "\n")


def _split_header_body(raw: str) -> tuple[str, str]:
    """按第一个空行分割 headers 区与 body。"""
    text = _normalize_newlines(raw)
    idx = text.find("\n\n")
    if idx >= 0:
        return text[:idx], text[idx + 2:]
    return text, ""


def parse_raw_request(
    raw: str, default_scheme: str = "https", default_host: str = ""
) -> Optional[dict]:
    """解析原始请求文本，返回 {method, url, headers, body, scheme} 或 None。

    支持两种请求行：
      1. 相对路径：`GET /path?query HTTP/1.1` + Host 头（Host 缺失时回退 default_host）
      2. 绝对 URL：`GET http://host/path HTTP/1.1`（scheme/host 直接从请求行取）
    """
    header_section, body = _split_header_body(raw)
    lines = header_section.split("\n")
    request_line = lines[0].strip() if lines else ""
    parts = request_line.split()
    if len(parts) < 2:
        return None

    method = parts[0].upper()
    target = parts[1]

    header_lines = lines[1:]

    # 绝对 URL 请求行：直接取 scheme/host/path
    if target.startswith(("http://", "https://")):
        from urllib.parse import urlparse

        parsed = urlparse(target)
        scheme = parsed.scheme or default_scheme
        url = target
        headers = "\n".join(l.strip() for l in header_lines if l.strip())
        return {"method": method, "url": url, "headers": headers, "body": body, "scheme": scheme}

    # 既不是绝对 URL、也不以 / 开头 → 不是有效的相对路径请求行
    if not target.startswith("/"):
        return None

    # 相对路径：需要 Host 头（缺失时回退 default_host，用于 apply 时继承 flow 的 host）
    host = ""
    for line in header_lines:
        lower = line.lower().strip()
        if lower.startswith("host:"):
            host = line.split(":", 1)[1].strip()
            break
    if not host:
        host = default_host or ""
    if not host:
        return None

    scheme = default_scheme
    url = f"{scheme}://{host}{target}"
    headers = "\n".join(l.strip() for l in header_lines if l.strip())
    return {"method": method, "url": url, "headers": headers, "body": body, "scheme": scheme}


def parse_raw_response(raw: str) -> Optional[dict]:
    """解析原始响应文本，返回 {status, reason, headers, body, http_version} 或 None。

    状态行兼容：
      - `HTTP/1.1 200 OK`
      - `HTTP/1.1 200`
      - `200 OK`
      - `200`
    """
    header_section, body = _split_header_body(raw)
    lines = header_section.split("\n")
    status_line = lines[0].strip() if lines else ""
    parts = status_line.split()
    if not parts:
        return None

    if parts[0].startswith("HTTP/"):
        http_version = parts[0].split("/", 1)[1] if "/" in parts[0] else "1.1"
        rest = parts[1:]
    else:
        http_version = "1.1"
        rest = parts

    if not rest:
        return None
    try:
        status = int(rest[0])
    except ValueError:
        return None

    reason = " ".join(rest[1:])
    headers = "\n".join(l.strip() for l in lines[1:] if l.strip())
    return {
        "status": status,
        "reason": reason,
        "headers": headers,
        "body": body,
        "http_version": http_version,
    }


def build_raw_request(
    method: str,
    path: str,
    http_version: str = "HTTP/1.1",
    headers_str: str = "",
    body: str = "",
) -> str:
    """把字段拼成 Burp 风格 raw request 文本。"""
    request_line = f"{method} {path} {http_version}".rstrip()
    headers = headers_str.rstrip("\n")
    if body:
        return f"{request_line}\n{headers}\n\n{body}"
    return f"{request_line}\n{headers}"


def build_raw_response(
    status: int,
    reason: str = "",
    http_version: str = "HTTP/1.1",
    headers_str: str = "",
    body: str = "",
) -> str:
    """把字段拼成 raw response 文本。"""
    reason_part = f" {reason}" if reason else ""
    status_line = f"HTTP/{http_version} {status}{reason_part}"
    headers = headers_str.rstrip("\n")
    if body:
        return f"{status_line}\n{headers}\n\n{body}"
    return f"{status_line}\n{headers}"


def _flow_host_header(req) -> str:
    """推导请求的 Host 头值：真实 Host 头 → authority → host[:port]。"""
    hh = getattr(req, "host_header", None)
    if hh:
        return hh
    au = getattr(req, "authority", None)
    if au:
        return au
    host = getattr(req, "host", "") or ""
    port = getattr(req, "port", None)
    if host:
        if port and port not in (80, 443):
            return f"{host}:{port}"
        return host
    return ""


def build_raw_request_from_flow(flow) -> str:
    """从 mitmproxy flow.request 拼 raw 文本（拦截器编辑区初值）。

    请求行用相对路径（含 query），与前端 httpParser/buildRawRequest 格式一致；
    body 用原始文本（不格式化），保证「所见即所得」——编辑后放行就是发送这个字节流。
    缺失 Host 头时补一个（HTTP/1.1 必需，否则相对路径请求行放行时无法解析）。
    """
    req = flow.request
    http_version = getattr(req, "http_version", "") or "HTTP/1.1"
    headers_str = "\n".join(f"{k}: {v}" for k, v in req.headers.items())
    if not any(k.lower() == "host" for k in req.headers.keys()):
        host_header = _flow_host_header(req)
        if host_header:
            headers_str = (headers_str + "\n" if headers_str else "") + f"Host: {host_header}"
    body = req.get_text(strict=False) or ""
    return build_raw_request(req.method, req.path, http_version, headers_str, body)


def build_raw_response_from_flow(flow) -> str:
    """从 flow.response 拼 raw 文本。"""
    resp = flow.response
    if resp is None:
        return ""
    http_version = getattr(resp, "http_version", "") or "1.1"
    if http_version.startswith("HTTP/"):
        http_version = http_version.split("/", 1)[1]
    headers_str = "\n".join(f"{k}: {v}" for k, v in resp.headers.items())
    body = resp.get_text(strict=False) or ""
    return build_raw_response(
        resp.status_code, resp.reason or "", http_version, headers_str, body
    )


def _rewrite_length(message, content_len: int) -> None:
    """重写 Content-Length 并删除 Transfer-Encoding（HTTP/1.1 不自动重算）。"""
    # 删除所有 Transfer-Encoding（可能多个）
    for k in [k for k in message.headers if k.lower() == "transfer-encoding"]:
        del message.headers[k]
    message.headers["Content-Length"] = str(content_len)


def _apply_headers(message, headers_str: str) -> None:
    """把 headers 文本应用到 mitmproxy Message 的 headers（清空后逐行重建）。"""
    message.headers.clear()
    for line in headers_str.split("\n"):
        line = line.strip()
        if not line or ":" not in line:
            continue
        name, value = line.split(":", 1)
        message.headers[name.strip()] = value.strip()


def apply_raw_request(flow, raw: str) -> bool:
    """把编辑后的 raw 应用到 flow.request。返回是否解析/应用成功。

    编辑后 raw 缺失 Host 头时，继承 flow 原有的 host（避免用户误删 Host 导致解析失败）。
    失败时调用方应「原样放行」。
    """
    parsed = parse_raw_request(raw, flow.request.scheme, default_host=flow.request.host)
    if not parsed:
        return False

    req = flow.request
    req.method = parsed["method"]
    req.url = parsed["url"]  # setter 自动拆分 scheme/host/port/path
    _apply_headers(req, parsed["headers"])
    # HTTP/1.1 必须有 Host；raw 缺 Host 时从 URL 推导补一个
    if "Host" not in req.headers:
        req.headers["Host"] = req.host
    req.text = parsed["body"]
    _rewrite_length(req, len(req.content or b""))
    return True


def apply_raw_response(flow, raw: str) -> bool:
    """把编辑后的 raw 应用到 flow.response。返回是否解析/应用成功。"""
    if flow.response is None:
        return False
    parsed = parse_raw_response(raw)
    if not parsed:
        return False

    resp = flow.response
    resp.status_code = parsed["status"]
    if parsed["reason"]:
        resp.reason = parsed["reason"]
    _apply_headers(resp, parsed["headers"])
    resp.text = parsed["body"]
    _rewrite_length(resp, len(resp.content or b""))
    return True
