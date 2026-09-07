"""内置浏览器驱动：通过 CDP（Chrome DevTools Protocol）驱动本机 Chrome/Edge。

设计取舍：
- 不引入 Playwright（免 driver 下载与浏览器二进制分发，PyInstaller 打包零负担），
  直接走 CDP WebSocket，依赖仅 websockets + httpx（均已随 uvicorn/fastapi 存在）。
- 复用系统已装的 Chrome/Edge（Windows 11 必有 Edge），--user-data-dir 隔离在
  应用数据目录，不污染用户日常浏览器配置。
- 浏览器进程全局共享一个（模块级单例）；每个 Agent 任务持有自己的标签页
  （BrowserSession），会话（cookie/localStorage）在同一浏览器内共享，
  标签页生命周期跟随任务，页面状态跨工具调用延续。
- 应用退出时 atexit 回收自己拉起的浏览器进程。

协议要点（Chrome 110+）：
- 新建标签页 /json/new 需要 PUT（旧版 GET，失败时回退）。
- 页面级 WebSocket 地址来自 /json/list 的 webSocketDebuggerUrl，导航后不变。
"""

import asyncio
import atexit
import base64
import json
import logging
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

import httpx

from hemlock.core.config import settings

logger = logging.getLogger(__name__)

# CDP 调用与页面加载的默认超时
_NAV_TIMEOUT = 30
_EVAL_TIMEOUT = 15
_SCREENSHOT_TIMEOUT = 15
_BROWSER_READY_TIMEOUT = 20
# 输出截断（进入 LLM 上下文前先收敛）
_MAX_TEXT = 8000
_MAX_HTML = 2000
_MAX_LINKS = 100
_MAX_CONSOLE = 20


def _find_browser_exe() -> str | None:
    """按常见安装位置探测本机 Chrome/Edge 可执行文件（Windows 优先，兼顾类 Unix）。"""
    candidates = [
        # Chrome
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        # Edge（Windows 11 必有）
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        # 类 Unix 常见路径
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for cand in candidates:
        if cand and os.path.isfile(cand):
            return cand
    for name in ("chrome", "msedge", "chromium", "chromium-browser", "google-chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _get_profile_dir() -> Path:
    """浏览器专用配置目录（与用户日常浏览器完全隔离）。"""
    from hemlock.core.config import get_data_dir

    d = get_data_dir() / "browser_profile"
    d.mkdir(parents=True, exist_ok=True)
    return d


class _BrowserProcess:
    """共享浏览器进程管理：全局唯一实例，负责启动与回收。"""

    def __init__(self):
        self.port: int | None = None
        self.proc: subprocess.Popen | None = None  # 仅当由本应用拉起时非 None
        self._lock = asyncio.Lock()

    async def ensure(self) -> int:
        """确保有可用的 CDP 端口，返回端口号。

        端口探测策略：从 9222 起逐个尝试——已有 CDP 响应的端口直接复用
        （可能是用户自己开的调试浏览器），连接被拒的端口视为空闲可用。
        """
        async with self._lock:
            if self.port is not None:
                return self.port

            for port in range(9222, 9232):
                if await self._is_cdp_alive(port):
                    # 已有浏览器在监听：复用，但不接管其进程生命周期
                    self.port = port
                    logger.info(f"复用已有 CDP 浏览器: 127.0.0.1:{port}")
                    return port
                try:
                    proc = await self._launch(port)
                except (FileNotFoundError, OSError) as e:
                    raise RuntimeError(
                        "未找到可用的 Chrome/Edge 浏览器，无法启动内置浏览器。"
                        f"（{type(e).__name__}）"
                    ) from e
                # 拉起后轮询就绪；失败则换下一个端口
                if await self._wait_ready(port):
                    self.port = port
                    self.proc = proc
                    logger.info(f"内置浏览器已启动: 127.0.0.1:{port}")
                    return port
                try:
                    proc.terminate()
                except Exception:
                    pass

            raise RuntimeError(
                "内置浏览器启动失败：9222-9231 端口均不可用（CDP 就绪超时）"
            )

    @staticmethod
    async def _is_cdp_alive(port: int) -> bool:
        try:
            async with httpx.AsyncClient(timeout=0.4) as client:
                resp = await client.get(f"http://127.0.0.1:{port}/json/version")
                return resp.status_code == 200 and "webSocketDebuggerUrl" in resp.text
        except Exception:
            return False

    async def _launch(self, port: int) -> subprocess.Popen:
        exe = _find_browser_exe()
        if not exe:
            raise RuntimeError("未找到 Chrome/Edge")
        args = [
            exe,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={_get_profile_dir()}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-gpu",
            "--disable-extensions",
            "--mute-audio",
            "--window-size=1440,900",
        ]
        # 有头开关：关闭时无头后台运行（默认）；开启时弹出可见窗口，用户可与
        # Agent 同时操作同一页面（人工过验证码/扫码登录，Agent 接管继续）
        if not getattr(settings, "browser_headed", False):
            args.append("--headless=new")
        # 配置了上游代理时浏览器流量跟随（Chrome 对 localhost 默认不代理）
        upstream = (getattr(settings, "upstream_proxy", "") or "").strip()
        if upstream:
            if not upstream.startswith(("http://", "https://", "socks5://", "socks4://")):
                upstream = f"http://{upstream}"
            args.append(f"--proxy-server={upstream}")
        args.append("about:blank")
        creationflags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
        return subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )

    @staticmethod
    async def _wait_ready(port: int) -> bool:
        deadline = time.monotonic() + _BROWSER_READY_TIMEOUT
        while time.monotonic() < deadline:
            if await _BrowserProcess._is_cdp_alive(port):
                return True
            await asyncio.sleep(0.3)
        return False

    def terminate_owned(self):
        """回收本应用拉起的浏览器进程（atexit 调用；复用的外部浏览器不动）。"""
        if self.proc is not None:
            try:
                self.proc.terminate()
            except Exception:
                pass
            self.proc = None
        self.port = None


_BROWSER = _BrowserProcess()
atexit.register(_BROWSER.terminate_owned)


class BrowserSession:
    """单个 Agent 任务的浏览器会话：持有自己的标签页，跨调用保持页面状态。"""

    def __init__(self):
        self._tab_ws: str | None = None

    # ===== 底层 CDP =====

    async def _new_tab(self) -> str:
        """新建标签页，返回其 WebSocket 调试地址（Chrome 110+ 用 PUT，失败回退 GET）。"""
        port = await _BROWSER.ensure()
        base = f"http://127.0.0.1:{port}"
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.put(f"{base}/json/new?about:blank")
            if resp.status_code != 200:
                resp = await client.get(f"{base}/json/new?about:blank")
            resp.raise_for_status()
            data = resp.json()
        ws_url = data.get("webSocketDebuggerUrl")
        if not ws_url:
            raise RuntimeError("新建标签页失败：未返回 webSocketDebuggerUrl")
        return ws_url

    async def _ensure_tab(self) -> str:
        """拿到可用标签页 ws 地址；失效（标签页被关/连接拒绝）时自动重建。"""
        if self._tab_ws:
            return self._tab_ws
        self._tab_ws = await self._new_tab()
        return self._tab_ws

    async def _reset_tab(self):
        self._tab_ws = None

    async def navigate(self, url: str, wait_seconds: float = 0) -> dict:
        """打开 URL，等页面加载完成后返回渲染结果（标题/文本/链接/控制台错误）。

        wait_seconds：load 事件后的额外稳定等待（JS 异步渲染的页面建议 2-4s）。
        """
        from websockets.asyncio.client import connect as ws_connect

        url = url.strip()
        if not re.match(r"^https?://", url):
            return {"error": "URL 必须以 http:// 或 https:// 开头"}
        wait_seconds = min(max(wait_seconds or 0, 0), 15)

        tab_ws = await self._ensure_tab()
        console_errors: list[str] = []
        try:
            async with ws_connect(tab_ws, max_size=64 * 1024 * 1024, open_timeout=5) as ws:
                await self._send(ws, "Runtime.enable")
                await self._send(ws, "Page.enable")
                nav = await self._send(ws, "Page.navigate", {"url": url})
                if nav.get("errorText"):
                    return {"error": f"导航失败: {nav['errorText']}", "url": url}

                # 等待 load 事件 + 额外稳定时间；整体受 _NAV_TIMEOUT 约束
                deadline = time.monotonic() + min(_NAV_TIMEOUT, 10 + wait_seconds)
                load_deadline = time.monotonic() + 10
                loaded = False
                while time.monotonic() < deadline:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=0.5)
                    except asyncio.TimeoutError:
                        if loaded and time.monotonic() > load_deadline + wait_seconds:
                            break
                        continue
                    except Exception:
                        break
                    try:
                        data = json.loads(msg)
                    except (ValueError, TypeError):
                        continue
                    method = data.get("method", "")
                    if method == "Page.loadEventFired":
                        loaded = True
                        load_deadline = time.monotonic()
                        if wait_seconds <= 0:
                            break
                    elif method == "Runtime.exceptionThrown":
                        try:
                            detail = data["params"]["exceptionDetails"]
                            text = detail.get("exception", {}).get(
                                "description"
                            ) or detail.get("text", "")
                            if text and len(console_errors) < _MAX_CONSOLE:
                                console_errors.append(text[:300])
                        except (KeyError, TypeError):
                            pass
                    elif method == "Log.entryAdded":
                        try:
                            entry = data["params"]["entry"]
                            if entry.get("level") == "error" and len(console_errors) < _MAX_CONSOLE:
                                console_errors.append(
                                    f"{entry.get('source','')}: {entry.get('text','')}"[:300]
                                )
                        except (KeyError, TypeError):
                            pass
                await asyncio.sleep(0.3)

                title = await self._eval_in(ws, "document.title")
                final_url = await self._eval_in(ws, "location.href")
                text = await self._eval_in(
                    ws, "document.body ? document.body.innerText : ''"
                )
                html_len = await self._eval_in(
                    ws, "document.documentElement ? document.documentElement.outerHTML.length : 0"
                )
                html_head = await self._eval_in(
                    ws,
                    "document.documentElement ? document.documentElement.outerHTML.slice(0, %d) : ''"
                    % _MAX_HTML,
                )
                links_raw = await self._eval_in(
                    ws,
                    "JSON.stringify([...new Set([...document.links].map(a => a.href))].slice(0, %d))"
                    % _MAX_LINKS,
                )
                try:
                    links = json.loads(links_raw) if links_raw else []
                except (ValueError, TypeError):
                    links = []
        except Exception as e:
            # 标签页可能已被关闭：重置，下次调用重建
            await self._reset_tab()
            return {"error": f"浏览器连接失败: {type(e).__name__}: {e}", "url": url}

        return {
            "url": url,
            "final_url": final_url or url,
            "title": title or "",
            "visible_text": (text or "")[:_MAX_TEXT],
            "text_truncated": len(text or "") > _MAX_TEXT,
            "html_length": int(html_len or 0),
            "html_head": (html_head or "")[:_MAX_HTML],
            "links": links,
            "console_errors": console_errors,
        }

    async def evaluate(self, expression: str) -> dict:
        """在当前页面执行 JS 并返回结果（支持 async IIFE / Promise）。"""
        from websockets.asyncio.client import connect as ws_connect

        if not expression or not expression.strip():
            return {"error": "expression 参数不能为空"}

        tab_ws = await self._ensure_tab()
        try:
            async with ws_connect(tab_ws, max_size=64 * 1024 * 1024, open_timeout=5) as ws:
                result = await self._send(
                    ws,
                    "Runtime.evaluate",
                    {
                        "expression": expression,
                        "returnByValue": True,
                        "awaitPromise": True,
                    },
                    timeout=_EVAL_TIMEOUT,
                )
        except Exception as e:
            await self._reset_tab()
            return {"error": f"浏览器连接失败: {type(e).__name__}: {e}"}

        if result.get("exceptionDetails"):
            detail = result["exceptionDetails"]
            text = detail.get("exception", {}).get("description") or detail.get("text", "")
            return {"error": f"JS 执行异常: {text[:500]}"}
        value = result.get("result", {})
        return {
            "value": value.get("value"),
            "type": value.get("type"),
            "json": json.dumps(value.get("value"), ensure_ascii=False, default=str)[:5000]
            if value.get("type") not in ("undefined",) else None,
        }

    async def screenshot(self, name: str = "", full_page: bool = False) -> dict:
        """截图当前页面，落盘到工作区 screenshots/ 目录，返回绝对路径（可作证据）。"""
        from websockets.asyncio.client import connect as ws_connect

        tab_ws = await self._ensure_tab()
        try:
            async with ws_connect(tab_ws, max_size=64 * 1024 * 1024, open_timeout=5) as ws:
                shot = await self._send(
                    ws,
                    "Page.captureScreenshot",
                    {
                        "format": "png",
                        "captureBeyondViewport": bool(full_page),
                    },
                    timeout=_SCREENSHOT_TIMEOUT,
                )
        except Exception as e:
            await self._reset_tab()
            return {"error": f"浏览器连接失败: {type(e).__name__}: {e}"}

        data_b64 = shot.get("data")
        if not data_b64:
            return {"error": "截图失败：CDP 未返回图像数据"}

        from hemlock.core.config import get_data_dir

        safe = re.sub(r"[^A-Za-z0-9_\-]", "_", name or "")[:80]
        filename = f"{safe or 'shot'}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        out_dir = get_data_dir() / "workspace" / "screenshots"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / filename
        out_path.write_bytes(base64.b64decode(data_b64))

        return {
            "screenshot_path": str(out_path),
            "full_page": bool(full_page),
            "size_bytes": out_path.stat().st_size,
        }

    async def cookies(self) -> dict:
        """返回当前页面可见的 Cookies（确认登录态/会话固定测试用）。"""
        from websockets.asyncio.client import connect as ws_connect

        tab_ws = await self._ensure_tab()
        try:
            async with ws_connect(tab_ws, max_size=64 * 1024 * 1024, open_timeout=5) as ws:
                result = await self._send(ws, "Network.getCookies")
        except Exception as e:
            await self._reset_tab()
            return {"error": f"浏览器连接失败: {type(e).__name__}: {e}"}

        items = [
            {
                "name": c.get("name"),
                "value": c.get("value"),
                "domain": c.get("domain"),
                "path": c.get("path"),
                "httpOnly": c.get("httpOnly"),
                "secure": c.get("secure"),
            }
            for c in result.get("cookies", [])
        ]
        return {"count": len(items), "cookies": items}

    # ===== CDP 收发辅助 =====

    @staticmethod
    async def _send(
        ws, method: str, params: dict | None = None, timeout: float = _NAV_TIMEOUT
    ) -> dict:
        """发送一条 CDP 命令并等待其响应（忽略途中混入的事件）。"""
        msg_id = int(time.monotonic() * 1000) % 1000000 + 1
        payload = {"id": msg_id, "method": method}
        if params is not None:
            payload["params"] = params
        await ws.send(json.dumps(payload))
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"CDP 响应超时: {method}")
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            data = json.loads(raw)
            if data.get("id") == msg_id:
                return data.get("result", {}) or {}
            # 事件/其他 id 的响应：继续等

    async def _eval_in(self, ws, expression: str, timeout: float = _EVAL_TIMEOUT):
        """在已建立的 ws 连接内执行一段 JS，返回 value（失败返回空串）。"""
        try:
            result = await self._send(
                ws,
                "Runtime.evaluate",
                {"expression": expression, "returnByValue": True},
                timeout=timeout,
            )
            return result.get("result", {}).get("value", "")
        except Exception:
            return ""


def get_browser_session() -> BrowserSession:
    """获取一个新会话（每个 Agent 任务持有一个，任务内跨调用复用标签页）。"""
    return BrowserSession()
