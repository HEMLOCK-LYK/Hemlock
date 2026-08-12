"""mitmproxy addon：拦截请求/响应，暂停流量等待人工处理。

关键点：
- addon 的 request/response hook 是 blocking 的——async hook 里 await 一个
  挂起的 Future 就能真正暂停该 flow（上游不发出、客户端收不到响应）。
- 本 addon 必须**先于** CaptureAddon 注册（addon 按注册顺序执行），这样响应
  拦截的暂停/编辑发生在入库之前，Forward 后的编辑才会被 CaptureAddon 写入数据库。
- 超时（settings.intercept_timeout）自动放行（不编辑），避免流量卡死。
"""

import asyncio
import logging

from mitmproxy import http

from hemlock.core.config import settings
from hemlock.services.intercept_controller import intercept_controller
from hemlock.services.raw_http import apply_raw_request, apply_raw_response

logger = logging.getLogger(__name__)


class InterceptAddon:
    """mitmproxy 拦截 addon。"""

    async def request(self, flow: http.HTTPFlow) -> None:
        """请求到达时：如需拦截则暂停，等待 Forward/Drop。"""
        if not intercept_controller.should_intercept("request", flow):
            return
        try:
            item = intercept_controller.enqueue("request", flow)
            decision, raw = await self._wait_decision(item)
            if decision == "drop":
                flow.kill()
                flow.metadata["hemlock_dropped"] = True
            elif raw:
                if not apply_raw_request(flow, raw):
                    logger.warning("编辑后请求解析失败，原样放行: %s", flow.request.pretty_url)
        except Exception:
            logger.exception("拦截请求处理异常，原样放行: %s", flow.request.pretty_url)

    async def response(self, flow: http.HTTPFlow) -> None:
        """响应返回时：如需拦截则暂停，等待 Forward/Drop。"""
        if flow.response is None:
            return
        if not intercept_controller.should_intercept("response", flow):
            return
        try:
            item = intercept_controller.enqueue("response", flow)
            decision, raw = await self._wait_decision(item)
            if decision == "drop":
                flow.kill()
                flow.metadata["hemlock_dropped"] = True
            elif raw:
                if not apply_raw_response(flow, raw):
                    logger.warning("编辑后响应解析失败，原样放行: %s", flow.request.pretty_url)
        except Exception:
            logger.exception("拦截响应处理异常，原样放行: %s", flow.request.pretty_url)

    async def _wait_decision(self, item):
        """等待人工决定；超时自动放行（不编辑）。"""
        try:
            return await asyncio.wait_for(item.future, timeout=settings.intercept_timeout)
        except asyncio.TimeoutError:
            intercept_controller.complete(item.id, "forward", reason="timeout")
            return ("forward", None)


# mitmproxy 加载入口
addon = InterceptAddon
