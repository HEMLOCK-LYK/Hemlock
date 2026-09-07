"""会话管理服务：Agent 的 HTTP 会话状态管理。

功能：
1. Cookie/Session 跟踪：自动收集响应中的 Set-Cookie，在后续请求中携带
2. Token 管理：提取和复用 CSRF token、JWT、Bearer token
3. 请求串联：从响应中提取数据（正则/JSON path），注入后续请求
4. 认证流程：执行登录请求，建立认证会话

Agent 通过 session_id 维护一个会话上下文，所有请求自动携带会话状态。
"""

import re
import json
import httpx
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse, parse_qs


@dataclass
class SessionState:
    """单个会话的状态。"""
    cookies: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)  # 自定义 header（如 Authorization）
    csrf_tokens: dict[str, str] = field(default_factory=dict)  # 提取的 CSRF token
    variables: dict[str, str] = field(default_factory=dict)  # 自定义变量（从响应提取）
    authenticated: bool = False
    login_url: str = ""
    history: list[dict] = field(default_factory=list)  # 请求历史摘要


class SessionManager:
    """会话管理器：管理多个 Agent 会话。"""

    def __init__(self):
        self._sessions: dict[str, SessionState] = {}

    def create_session(self, session_id: str) -> SessionState:
        """创建新会话。"""
        state = SessionState()
        self._sessions[session_id] = state
        return state

    def get_session(self, session_id: str) -> SessionState | None:
        """获取会话状态。"""
        return self._sessions.get(session_id)

    def delete_session(self, session_id: str) -> bool:
        """删除会话。"""
        if session_id in self._sessions:
            del self._sessions[session_id]
            return True
        return False

    def list_sessions(self) -> list[dict]:
        """列出所有会话摘要。"""
        result = []
        for sid, state in self._sessions.items():
            result.append({
                "session_id": sid,
                "authenticated": state.authenticated,
                "login_url": state.login_url,
                "cookie_count": len(state.cookies),
                "header_count": len(state.headers),
                "variable_count": len(state.variables),
                "request_count": len(state.history),
            })
        return result


# 全局会话管理器单例
session_manager = SessionManager()


def _parse_headers(headers_str: str) -> dict[str, str]:
    """解析 header 字符串为字典。"""
    headers: dict[str, str] = {}
    for line in headers_str.strip().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip()] = v.strip()
    return headers


def _extract_cookies_from_response(resp_headers: str) -> dict[str, str]:
    """从响应头中提取 Set-Cookie。"""
    cookies = {}
    for line in resp_headers.splitlines():
        lower = line.lower().strip()
        if lower.startswith("set-cookie:"):
            # set-cookie: name=value; Path=/; HttpOnly
            cookie_part = line.split(":", 1)[1].strip()
            if "=" in cookie_part:
                name_value = cookie_part.split(";")[0].strip()
                if "=" in name_value:
                    name, value = name_value.split("=", 1)
                    cookies[name.strip()] = value.strip()
    return cookies


def _extract_csrf_tokens(html_body: str) -> dict[str, str]:
    """从 HTML 响应中提取 CSRF token。

    支持常见模式：
    - <input type="hidden" name="csrf_token" value="xxx">
    - <meta name="csrf-token" content="xxx">
    """
    tokens = {}

    # <input type="hidden" name="*_token" value="xxx">
    pattern1 = re.compile(
        r'<input[^>]+name=["\']([\w_-]*(?:token|csrf)[\w_-]*)["\'][^>]*value=["\']([^"\']+)["\']',
        re.IGNORECASE,
    )
    for match in pattern1.finditer(html_body):
        tokens[match.group(1)] = match.group(2)

    # <input type="hidden" name="xxx" value="yyy"> where name contains token
    pattern2 = re.compile(
        r'<input[^>]+value=["\']([^"\']+)["\'][^>]*name=["\']([\w_-]*(?:token|csrf)[\w_-]*)["\']',
        re.IGNORECASE,
    )
    for match in pattern2.finditer(html_body):
        tokens[match.group(2)] = match.group(1)

    # <meta name="csrf-token" content="xxx">
    pattern3 = re.compile(
        r'<meta[^>]+name=["\'](?:csrf-token|_token)["\'][^>]+content=["\']([^"\']+)["\']',
        re.IGNORECASE,
    )
    for match in pattern3.finditer(html_body):
        tokens["csrf_token"] = match.group(1)

    return tokens


def extract_data_from_response(
    body: str,
    pattern: str,
    extract_type: str = "regex",
    group: int = 0,
) -> str | None:
    """从响应体中提取数据。

    Args:
        body: 响应体
        pattern: 正则表达式或 JSON path（如 data.user.id）
        extract_type: "regex" 或 "json"
        group: 正则分组号（0=整体匹配）

    Returns:
        提取到的字符串，未找到返回 None
    """
    if extract_type == "json":
        try:
            data = json.loads(body)
            keys = pattern.split(".")
            for key in keys:
                if isinstance(data, list) and key.isdigit():
                    data = data[int(key)]
                elif isinstance(data, dict):
                    data = data.get(key)
                else:
                    return None
            return str(data) if data is not None else None
        except (json.JSONDecodeError, ValueError, TypeError):
            return None
    else:
        # regex
        match = re.search(pattern, body)
        if match:
            return match.group(group) if group > 0 else match.group(0)
        return None


def build_cookie_header(cookies: dict[str, str]) -> str:
    """构建 Cookie 请求头。"""
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


async def send_request_with_session(
    session_id: str,
    method: str,
    url: str,
    headers_str: str = "",
    body: str = "",
    follow_redirects: bool = True,
    use_proxy: bool = False,
) -> dict[str, Any]:
    """使用会话状态发送 HTTP 请求。

    自动携带会话 Cookie 和自定义 Header，
    自动从响应中更新 Cookie 和 CSRF token。

    Returns:
        {"status": int, "headers": str, "body": str, "flow_id": str}
    """
    state = session_manager.get_session(session_id)
    if not state:
        state = session_manager.create_session(session_id)

    # 合并 headers：会话 headers + 请求 headers
    headers = dict(state.headers)
    headers.update(_parse_headers(headers_str))

    # 携带 Cookie
    if state.cookies:
        existing_cookie = headers.get("Cookie", "")
        session_cookie = build_cookie_header(state.cookies)
        if existing_cookie:
            headers["Cookie"] = f"{existing_cookie}; {session_cookie}"
        else:
            headers["Cookie"] = session_cookie

    # 如果有 CSRF token 且 body 中有占位符，替换
    if state.csrf_tokens and body:
        for token_name, token_value in state.csrf_tokens.items():
            body = body.replace(f"{{{{{token_name}}}}}", token_value)
            body = body.replace(f"${token_name}", token_value)

    # 选择客户端：默认共享直连池；use_proxy=True 经上游代理（一次性客户端）
    from hemlock.services.request_service import _select_client
    client, is_temp = await _select_client(use_proxy)
    try:
        return await _send_with_client(
            client, state, method, url, headers_str, body, follow_redirects
        )
    finally:
        if is_temp:
            await client.aclose()


async def _send_with_client(
    client,
    state,
    method: str,
    url: str,
    headers_str: str = "",
    body: str = "",
    follow_redirects: bool = True,
) -> dict[str, Any]:
    """send_request_with_session 主体（client 由调用方注入，此处不管理其生命周期）。"""
    content = body.encode("utf-8") if body else None

    # 构建 Request 对象，捕获所有实际发送的头部（含 Host 等自动添加项）
    try:
        request = client.build_request(
            method=method,
            url=url,
            headers=headers,
            content=content,
        )
        actual_req_headers = "\n".join(f"{k}: {v}" for k, v in request.headers.items())
    except Exception as e:
        return {
            "status": None,
            "headers": "",
            "body": f"[请求构建失败] {type(e).__name__}: {e}",
            "req_headers": "\n".join(f"{k}: {v}" for k, v in headers.items()),
            "duration_ms": None,
        }

    try:
        import time as _time
        _t0 = _time.monotonic()
        resp = await client.send(request, follow_redirects=follow_redirects)
        _duration_ms = round((_time.monotonic() - _t0) * 1000)
    except Exception as e:
        return {
            "status": None,
            "headers": "",
            "body": f"[请求异常] {type(e).__name__}: {e}",
            "req_headers": actual_req_headers,
            "duration_ms": None,
        }

    resp_headers = "\n".join(f"{k}: {v}" for k, v in resp.headers.items())

    # 更新会话状态：收集 Cookie
    new_cookies = _extract_cookies_from_response(resp_headers)
    state.cookies.update(new_cookies)

    # 更新 CSRF token
    new_tokens = _extract_csrf_tokens(resp.text)
    state.csrf_tokens.update(new_tokens)

    # 记录请求历史
    state.history.append({
        "method": method,
        "url": url,
        "status": resp.status_code,
    })

    return {
        "status": resp.status_code,
        "headers": resp_headers,
        "body": resp.text,
        "req_headers": actual_req_headers,
        "duration_ms": _duration_ms,
    }


async def authenticate(
    session_id: str,
    login_url: str,
    method: str,
    credentials: dict[str, str],
    headers_str: str = "",
    success_indicator: str = "",
) -> dict[str, Any]:
    """执行认证流程：发送登录请求，建立认证会话。

    Args:
        session_id: 会话 ID
        login_url: 登录端点 URL
        method: HTTP 方法（通常 POST）
        credentials: 凭证字典，如 {"username": "admin", "password": "admin"}
        headers_str: 额外请求头
        success_indicator: 成功标志（响应中包含此字符串或状态码）

    Returns:
        {"success": bool, "status": int, "body": str, "message": str}
    """
    state = session_manager.get_session(session_id)
    if not state:
        state = session_manager.create_session(session_id)

    # 先 GET 登录页面获取 CSRF token（如果需要）
    if method.upper() == "POST":
        try:
            await send_request_with_session(
                session_id, "GET", login_url, headers_str, ""
            )
        except Exception:
            pass  # 登录页可能不存在或不需要

    # 构建请求体
    body = "&".join(f"{k}={v}" for k, v in credentials.items())
    headers = headers_str
    if "Content-Type" not in headers:
        headers += "\nContent-Type: application/x-www-form-urlencoded" if headers else "Content-Type: application/x-www-form-urlencoded"

    result = await send_request_with_session(
        session_id, method, login_url, headers, body
    )

    status = result["status"]
    resp_body = result["body"]

    # 判断认证是否成功
    success = False
    message = ""

    if success_indicator:
        if success_indicator.isdigit():
            # 状态码判断
            success = status == int(success_indicator)
            message = f"状态码 {status} {'匹配' if success else '不匹配'}预期 {success_indicator}"
        else:
            # 响应内容判断
            success = success_indicator in resp_body
            message = f"响应中{'包含' if success else '不包含'}成功标志"
    else:
        # 默认判断：2xx/3xx 重定向通常表示登录成功，且响应中无 error/fail/invalid
        success = status in (200, 201, 301, 302)
        error_words = ["error", "failed", "invalid", "incorrect", "wrong", "失败", "错误"]
        resp_lower = resp_body.lower()
        for word in error_words:
            if word in resp_lower:
                success = False
                message = f"响应中包含错误标志: {word}"
                break
        if not message:
            message = f"状态码 {status}，默认判断为{'成功' if success else '失败'}"

    state.authenticated = success
    state.login_url = login_url

    return {
        "success": success,
        "status": status,
        "message": message,
        "cookies": list(state.cookies.keys()),
        "csrf_tokens": list(state.csrf_tokens.keys()),
        "req_headers": result.get("req_headers", headers),
        "duration_ms": result.get("duration_ms"),
    }


def set_session_header(session_id: str, header_name: str, header_value: str) -> dict:
    """设置会话自定义 Header（如 Authorization: Bearer xxx）。"""
    state = session_manager.get_session(session_id)
    if not state:
        state = session_manager.create_session(session_id)
    state.headers[header_name] = header_value
    return {"session_id": session_id, "header": header_name, "value": header_value}


def set_session_variable(session_id: str, var_name: str, var_value: str) -> dict:
    """设置会话变量（从响应提取的数据，可在后续请求中使用）。"""
    state = session_manager.get_session(session_id)
    if not state:
        state = session_manager.create_session(session_id)
    state.variables[var_name] = var_value
    return {"session_id": session_id, "variable": var_name, "value": var_value}


def get_session_info(session_id: str) -> dict | None:
    """获取会话详情。"""
    state = session_manager.get_session(session_id)
    if not state:
        return None
    return {
        "session_id": session_id,
        "authenticated": state.authenticated,
        "login_url": state.login_url,
        "cookies": dict(state.cookies),
        "headers": dict(state.headers),
        "csrf_tokens": dict(state.csrf_tokens),
        "variables": dict(state.variables),
        "request_count": len(state.history),
    }
