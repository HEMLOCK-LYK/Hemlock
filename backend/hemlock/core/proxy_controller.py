"""代理控制器：管理 mitmproxy 代理的启动/停止/状态。

代理不再随后端自动启动，而是由用户通过 API 手动控制。
"""

import logging
import threading
import asyncio
import socket
import time
from typing import Optional

from hemlock.core.config import settings
from hemlock.services.intercept_controller import intercept_controller

logger = logging.getLogger(__name__)


class ProxyController:
    """代理控制器单例：管理 mitmproxy 代理进程。"""

    def __init__(self):
        self._master = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._running

    def _is_port_listening(self, host: str, port: int) -> bool:
        """检查端口是否在监听。"""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                return s.connect_ex((host, port)) == 0
        except Exception:
            return False

    def _cleanup_stale(self):
        """清理僵尸状态。

        必须在持有 _lock 的情况下调用。
        处理两种情况：
        1. _running=True 但线程已退出或端口未监听
        2. _running=False 但 _master 仍存在（线程崩溃后残留，端口仍被占用）
        """
        # 情况 2：_running=False 但 _master 残留（线程崩溃后未清理）
        if not self._running and self._master is not None:
            logger.warning("检测到残留 master 对象（代理线程崩溃后未清理），正在清理")
            thread_alive = self._thread is not None and self._thread.is_alive()
            if thread_alive:
                try:
                    self._master.shutdown()
                except Exception as e:
                    logger.warning(f"清理残留 master 时 shutdown 失败（可忽略）: {e}")
                self._thread.join(timeout=3)
            self._master = None
            self._thread = None
            self._wait_port_release()
            return

        # 情况 1：_running=True 但线程或端口异常
        if not self._running:
            return

        thread_alive = self._thread is not None and self._thread.is_alive()
        port_listening = self._is_port_listening(settings.proxy_host, settings.proxy_port)

        if thread_alive and port_listening:
            return  # 一切正常，无需清理

        logger.warning(
            f"检测到僵尸代理状态：线程存活={thread_alive} 端口监听={port_listening}，正在清理"
        )

        # 如果线程还活着但端口没监听（或反之），尝试优雅关闭
        if self._master and thread_alive:
            try:
                self._master.shutdown()
            except Exception as e:
                logger.warning(f"清理时 shutdown 失败（可忽略）: {e}")

        if self._thread and thread_alive:
            self._thread.join(timeout=3)

        self._master = None
        self._thread = None
        self._running = False
        self._wait_port_release()

    def start(self) -> tuple[bool, str]:
        """启动代理。返回 (success, message)。"""
        with self._lock:
            # 先清理可能的僵尸状态（_running=True 但实际已死）
            self._cleanup_stale()

            if self._running:
                return True, f"代理已在运行: {settings.proxy_host}:{settings.proxy_port}"

            # 检查端口是否被占用（可能是外部进程）
            if self._is_port_listening(settings.proxy_host, settings.proxy_port):
                # 端口被占用 — 可能是上次的 mitmproxy 未完全释放
                # 尝试等待端口释放（最多 3 秒）
                waited = 0.0
                while waited < 3.0:
                    time.sleep(0.5)
                    waited += 0.5
                    if not self._is_port_listening(settings.proxy_host, settings.proxy_port):
                        break
                else:
                    msg = f"端口 {settings.proxy_port} 已被占用，请释放该端口或修改代理端口"
                    logger.error(msg)
                    return False, msg

            try:
                from mitmproxy import options
                from mitmproxy.tools.dump import DumpMaster
                from hemlock_addon.capture import CaptureAddon
                from hemlock_addon.intercept import InterceptAddon

                # 构建 mitmproxy 选项
                opts_kwargs = dict(
                    listen_host=settings.proxy_host,
                    listen_port=settings.proxy_port,
                )

                # 上游代理配置：将流量转发到指定代理服务器
                upstream = getattr(settings, "upstream_proxy", "") or ""
                if upstream.strip():
                    upstream = upstream.strip()
                    if not upstream.startswith(("http://", "https://")):
                        upstream = f"http://{upstream}"
                    opts_kwargs["mode"] = [f"upstream:{upstream}"]
                    logger.info(f"代理将使用上游代理: {upstream}")

                opts = options.Options(**opts_kwargs)
                self._master = DumpMaster(opts)
                # InterceptAddon 必须先于 CaptureAddon 注册（addon 按注册顺序执行）：
                # 响应拦截的暂停/编辑需发生在入库之前，Forward 后的编辑才会被写入数据库
                self._master.addons.add(InterceptAddon())
                self._master.addons.add(CaptureAddon())

                self._thread = threading.Thread(target=self._run_loop, daemon=True)
                self._thread.start()

                # 等待 mitmproxy 实际绑定端口（最多 5 秒）
                bound = False
                for _ in range(10):
                    time.sleep(0.5)
                    if self._is_port_listening(settings.proxy_host, settings.proxy_port):
                        bound = True
                        break
                    # 检查线程是否已退出（说明启动失败）
                    if not self._thread.is_alive():
                        break

                if not bound:
                    # mitmproxy 未能绑定端口
                    self._running = False
                    self._master = None
                    msg = f"代理启动失败：无法监听 {settings.proxy_host}:{settings.proxy_port}，端口可能被占用"
                    logger.error(msg)
                    return False, msg

                self._running = True
                upstream_info = f"（上游代理: {upstream}）" if upstream.strip() else ""
                msg = f"代理已启动: {settings.proxy_host}:{settings.proxy_port}{upstream_info}"
                logger.info(msg)
                return True, msg
            except Exception as e:
                self._running = False
                self._master = None
                logger.error(f"代理启动失败: {e}", exc_info=True)
                return False, f"代理启动失败: {e}"

    def _run_loop(self):
        """在独立线程中运行 mitmproxy 事件循环。"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        # 记录 proxy loop，供拦截器跨线程投递 Forward/Drop 决定
        intercept_controller.bind_proxy_loop(loop)
        try:
            loop.run_until_complete(self._master.run())
        except Exception as e:
            logger.error(f"代理运行异常: {e}", exc_info=True)
        finally:
            self._running = False
            # 代理退出：清理拦截器挂起状态（取消等待中的 flow）
            intercept_controller.reset()
            # 关闭事件循环以释放 server socket
            # 不关闭 loop 会导致 mitmproxy 的监听端口无法释放
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
            except Exception as e:
                logger.debug(f"清理异步任务失败（通常可忽略）: {e}")
            try:
                loop.close()
                logger.info("代理事件循环已关闭，端口应已释放")
            except Exception as e:
                logger.warning(f"关闭事件循环失败: {e}")

    def stop(self) -> tuple[bool, str]:
        """停止代理。返回 (success, message)。"""
        with self._lock:
            if not self._running and not self._master:
                # 确保端口已释放
                self._wait_port_release()
                return True, "代理未运行"

            # 如果线程已退出，直接清理（无需 shutdown）
            thread_alive = self._thread is not None and self._thread.is_alive()
            if not thread_alive:
                logger.warning("代理线程已退出，直接清理资源")
                self._master = None
                self._thread = None
                self._running = False
                self._wait_port_release()
                return True, "代理已停止"

            try:
                self._master.shutdown()
                if self._thread:
                    self._thread.join(timeout=5)
                self._master = None
                self._thread = None
                self._running = False

                # 等待端口真正释放，否则下次启动会报"端口被占用"
                self._wait_port_release()

                logger.info("代理已停止")
                return True, "代理已停止"
            except Exception as e:
                logger.error(f"代理停止失败: {e}", exc_info=True)
                self._master = None
                self._thread = None
                self._running = False
                self._wait_port_release()
                return False, f"代理停止失败: {e}"

    def _wait_port_release(self, timeout: float = 5.0):
        """等待端口释放（最多 timeout 秒）。

        mitmproxy shutdown 后端口可能仍处于 TIME_WAIT 状态，
        需要等待其完全释放，否则下次 start() 会误判端口被占用。
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self._is_port_listening(settings.proxy_host, settings.proxy_port):
                return
            time.sleep(0.3)
        logger.warning(
            f"端口 {settings.proxy_port} 在 {timeout}s 后仍未释放，"
            "下次启动可能需要等待系统回收"
        )

    def status(self) -> dict:
        """获取代理状态（只读，不修改内部状态）。

        如果检测到僵尸状态（标记运行中但端口未监听），
        仅在返回值中报告 running=False，实际清理在 start()/stop() 中进行。
        """
        running = self._running
        if running:
            port_listening = self._is_port_listening(settings.proxy_host, settings.proxy_port)
            if not port_listening:
                logger.warning("代理状态查询：标记为运行中但端口未监听，将报告为未运行")
                running = False

        return {
            "running": running,
            "host": settings.proxy_host,
            "port": settings.proxy_port,
            "upstream_proxy": getattr(settings, "upstream_proxy", "") or "",
        }


# 全局单例
proxy_controller = ProxyController()
