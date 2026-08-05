"""请求发送服务：用 httpx 发送 HTTP 请求。

使用共享 AsyncClient 实现连接池复用，避免每次请求都新建 TCP 连接。
"""

import logging
import httpx
from typing import Any

logger = logging.getLogger(__name__)

# ===== 共享连接池 =====
# 进程级单例 client，复用 TCP 连接和 TLS 会话
_shared_client: httpx.AsyncClient | None = None

# 连接池配置
_POOL_LIMITS = httpx.Limits(
    max_connections=50,        # 最大并发连接数
    max_keepalive_connections=20,  # 保持的空闲连接数
    keepalive_expiry=30.0,     # 空闲连接超时（秒）
)

# 请求默认超时
_DEFAULT_TIMEOUT = httpx.Timeout(
    connect=10.0,   # 连接超时
    read=30.0,      # 读取超时
    write=10.0,     # 写入超时
    pool=5.0,       # 连接池等待超时
)


async def get_client() -> httpx.AsyncClient:
    """获取共享的 AsyncClient 实例（懒加载）。

    trust_env=False：不读取系统代理环境变量（HTTP_PROXY/HTTPS_PROXY），
    确保 AI 发送的请求直连目标，不经过 mitmproxy 代理。
    避免同一请求被代理捕获产生重复 Flow 记录，且被杂包过滤器误判。

    X-Hemlock-Source 头：标识此请求由 AI Agent 发出，
    供代理层 CaptureAddon 白名单使用（防御性措施：即使后续
    有人改回 trust_env=True，代理层仍可识别并跳过杂包检测）。
    """
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            verify=False,
            timeout=_DEFAULT_TIMEOUT,
            follow_redirects=True,
            limits=_POOL_LIMITS,
            http2=False,  # 大多数目标不支持 HTTP/2
            trust_env=False,  # 不使用系统代理，AI 请求直连目标
            headers={"X-Hemlock-Source": "agent"},  # 标识 AI 请求，供代理层白名单
        )
        logger.debug("HTTP 连接池已初始化 (max_connections=50, trust_env=False)")
    return _shared_client


async def close_client() -> None:
    """关闭共享连接池（应用关闭时调用）。"""
    global _shared_client
    if _shared_client and not _shared_client.is_closed:
        await _shared_client.aclose()
        _shared_client = None
        logger.debug("HTTP 连接池已关闭")


def _parse_headers(headers_str: str) -> dict[str, str]:
    """把 headers 字符串解析为字典。"""
    headers: dict[str, str] = {}
    for line in headers_str.strip().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip()] = v.strip()
    return headers


def _headers_to_str(headers) -> str:
    """把 headers 字典/httpx.Headers 转为 \\n 换行字符串（与前端显示一致）。"""
    return "\n".join(f"{k}: {v}" for k, v in headers.items())


async def send_raw_request(
    method: str,
    url: str,
    headers_str: str = "",
    body: str = "",
    follow_redirects: bool = True,
) -> dict[str, Any]:
    """发送 HTTP 请求，返回响应字典。

    使用共享连接池，避免重复 TCP 握手。
    使用 build_request 捕获 httpx 自动添加的头部（Host、User-Agent 等），
    确保存入 Flow 的请求头完整，与 Burp Suite 行为一致。

    使用 stream=True 分阶段获取响应：先拿状态码和响应头，
    再读取响应体。如果读取响应体时连接断开（ReadError），
    仍返回已收到的状态码和响应头，与 Burp Suite 行为一致。

    Args:
        follow_redirects: 是否自动跟随重定向。默认 True（取最终内容）。
            开放重定向验证等需要原始 30x 响应与 Location 头时传 False。

    返回: {"status": int, "headers": str, "body": str, "req_headers": str}
    """
    headers = _parse_headers(headers_str)
    client = await get_client()
    content = body.encode("utf-8") if body else None

    # 先构建 Request 对象，捕获所有实际发送的头部（含 Host 等自动添加项）
    try:
        request = client.build_request(
            method=method,
            url=url,
            headers=headers,
            content=content,
        )
        actual_req_headers = _headers_to_str(request.headers)
    except Exception as e:
        logger.error(f"构建请求失败: {url} - {type(e).__name__}: {e}")
        return {
            "status": None,
            "headers": "",
            "body": f"[请求构建失败] {type(e).__name__}: {e}",
            "req_headers": headers_str,
        }

    import time as _time
    _t0 = _time.monotonic()

    # ===== 第一阶段：发送请求 + 获取响应头（stream 模式） =====
    try:
        resp = await client.send(request, stream=True, follow_redirects=follow_redirects)
    except httpx.ConnectError as e:
        logger.warning(f"连接失败: {url} - {e}")
        return {"status": None, "headers": "", "body": f"[连接失败] {e}", "req_headers": actual_req_headers, "duration_ms": None}
    except httpx.ReadTimeout as e:
        logger.warning(f"读取超时: {url}")
        return {"status": None, "headers": "", "body": f"[读取超时] {e}", "req_headers": actual_req_headers, "duration_ms": None}
    except httpx.PoolTimeout as e:
        logger.warning(f"连接池超时: {url} - {e}")
        return {"status": None, "headers": "", "body": f"[连接池超时] {e}", "req_headers": actual_req_headers, "duration_ms": None}
    except httpx.ConnectTimeout as e:
        logger.warning(f"连接超时: {url}")
        return {"status": None, "headers": "", "body": f"[连接超时] {e}", "req_headers": actual_req_headers, "duration_ms": None}
    except httpx.WriteError as e:
        logger.warning(f"写入失败: {url} - {e}")
        return {"status": None, "headers": "", "body": f"[写入失败] {e}", "req_headers": actual_req_headers, "duration_ms": None}
    except Exception as e:
        logger.error(f"请求异常: {url} - {type(e).__name__}: {e}")
        return {"status": None, "headers": "", "body": f"[请求异常] {type(e).__name__}: {e}", "req_headers": actual_req_headers, "duration_ms": None}

    # 到这里说明状态码和响应头已经收到
    _duration_ms = round((_time.monotonic() - _t0) * 1000)
    resp_status = resp.status_code
    resp_headers = _headers_to_str(resp.headers)

    # ===== 第二阶段：读取响应体 =====
    # 如果读取响应体时连接断开，仍返回已收到的状态码和响应头（与 Burp 行为一致）
    resp_body = ""
    read_error = False
    try:
        resp_body = await resp.aread()
        resp_body = resp_body.decode("utf-8", errors="replace")
    except httpx.ReadError as e:
        logger.warning(f"响应体读取中断: {url} - {e}（状态码 {resp_status} 已收到）")
        resp_body = f"[响应体读取中断] {type(e).__name__}: {e}\n\n服务端可能提前关闭了连接，以上为已收到的响应头。"
        read_error = True
    except httpx.RemoteProtocolError as e:
        logger.warning(f"远端协议错误: {url} - {e}（状态码 {resp_status} 已收到）")
        resp_body = f"[远端协议错误] {type(e).__name__}: {e}\n\n服务端协议行为异常，以上为已收到的响应头。"
        read_error = True
    except Exception as e:
        logger.warning(f"响应体读取异常: {url} - {type(e).__name__}: {e}（状态码 {resp_status} 已收到）")
        resp_body = f"[响应读取异常] {type(e).__name__}: {e}\n\n以上为已收到的响应头。"
        read_error = True
    finally:
        # stream 模式必须关闭响应以释放连接
        await resp.aclose()

    return {
        "status": resp_status,
        "headers": resp_headers,
        "body": resp_body,
        "req_headers": actual_req_headers,
        "duration_ms": _duration_ms,
    }
