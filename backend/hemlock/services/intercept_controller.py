"""拦截控制器：管理拦截开关、FIFO 队列与跨线程放行。

线程模型：
  - mitmproxy addon 跑在 proxy 线程的独立 asyncio loop 上，在 request/response
    hook 里 await 一个挂起的 Future，实现真正的流量暂停。
  - FastAPI 的 /api/intercept/* 端点跑在 uvicorn 线程，通过
    loop.call_soon_threadsafe 把 (decision, raw) 投递到 proxy loop，
    完成 Future 后 addon 恢复，应用编辑或丢弃。
"""

import asyncio
import threading
import time
from typing import Optional

from hemlock.services.event_bus import event_bus
from hemlock.services.flow_filter import is_static_noise
from hemlock.services.raw_http import (
    build_raw_request_from_flow,
    build_raw_response_from_flow,
)


def _set_future_result(future, decision, raw):
    """跨线程完成 Future（只能从 proxy loop 内执行，故经 call_soon_threadsafe 投递）。"""
    if not future.done():
        future.set_result((decision, raw))


class InterceptedItem:
    """队列中的一条被拦 flow。"""

    __slots__ = ("id", "kind", "flow", "future", "queued_at", "method", "url", "status", "raw")

    def __init__(self, item_id, kind, flow, future, method, url, status, raw):
        self.id = item_id            # "int_<seq>"
        self.kind = kind             # "request" | "response"
        self.flow = flow
        self.future = future         # proxy loop 上的 asyncio.Future, result=(decision, raw)
        self.queued_at = time.monotonic()
        self.method = method
        self.url = url
        self.status = status         # 响应拦截时的状态码；请求拦截为 None
        self.raw = raw               # 编辑区初值（原始包文本）


class InterceptController:
    """拦截器状态机单例。"""

    def __init__(self):
        self._lock = threading.Lock()
        self.enabled = False
        self.intercept_requests = False
        self.intercept_responses = False
        self._pending: list[InterceptedItem] = []
        self._proxy_loop: Optional[asyncio.AbstractEventLoop] = None
        self._seq = 0

    # ---------- 配置 ----------

    def get_status(self) -> dict:
        with self._lock:
            return self._status_locked()

    def _status_locked(self) -> dict:
        return {
            "enabled": self.enabled,
            "intercept_requests": self.intercept_requests,
            "intercept_responses": self.intercept_responses,
            "pending_count": len(self._pending),
        }

    def update_config(
        self,
        enabled: Optional[bool] = None,
        intercept_requests: Optional[bool] = None,
        intercept_responses: Optional[bool] = None,
    ) -> dict:
        """更新开关配置，有变化时广播 intercept_status。"""
        changed = False
        with self._lock:
            if enabled is not None and enabled != self.enabled:
                self.enabled = enabled
                changed = True
            if intercept_requests is not None and intercept_requests != self.intercept_requests:
                self.intercept_requests = intercept_requests
                changed = True
            if intercept_responses is not None and intercept_responses != self.intercept_responses:
                self.intercept_responses = intercept_responses
                changed = True
            status = self._status_locked()
        if changed:
            event_bus.publish("intercept", {"event": "intercept_status", **status})
        return status

    # ---------- proxy loop ----------

    def bind_proxy_loop(self, loop) -> None:
        """记录 mitmproxy 的 asyncio loop（ProxyController._run_loop 中调用）。"""
        with self._lock:
            self._proxy_loop = loop

    @property
    def proxy_loop(self):
        with self._lock:
            return self._proxy_loop

    # ---------- addon 侧（proxy 线程） ----------

    def should_intercept(self, kind: str, flow) -> bool:
        """是否应拦截该 flow。

        跳过静态杂包（JS/CSS/心跳/追踪域名等），避免拦截器被静态资源刷屏。
        """
        with self._lock:
            if not self.enabled:
                return False
            if kind == "request":
                if not self.intercept_requests:
                    return False
            elif kind == "response":
                if not self.intercept_responses:
                    return False
            else:
                return False
        try:
            url = flow.request.pretty_url
            method = flow.request.method
        except Exception:
            return True
        if is_static_noise(url, method):
            return False
        return True

    def enqueue(self, kind: str, flow) -> InterceptedItem:
        """把 flow 挂起并入队（必须在 proxy loop 内调用）。

        创建绑定 proxy loop 的 Future，addon 随后 await 它实现暂停。
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        try:
            method = flow.request.method
            url = flow.request.pretty_url
        except Exception:
            method, url = "?", "?"
        status = None
        if kind == "response" and flow.response is not None:
            status = flow.response.status_code
        if kind == "request":
            raw = build_raw_request_from_flow(flow)
        else:
            raw = build_raw_response_from_flow(flow)

        with self._lock:
            self._seq += 1
            item_id = f"int_{self._seq}"
            item = InterceptedItem(item_id, kind, flow, future, method, url, status, raw)
            self._pending.append(item)
            pending_count = len(self._pending)

        event_bus.publish("intercept", {
            "event": "intercept_queued",
            "id": item_id,
            "kind": kind,
            "method": method,
            "url": url,
            "status": status,
            "queued_at": item.queued_at,
            "pending_count": pending_count,
        })
        return item

    def complete(self, item_id: str, decision: str, reason: str = "") -> None:
        """超时自动放行时清理队列并广播（addon 侧调用）。

        future 已因超时被取消，这里只负责移除与广播。
        """
        with self._lock:
            item = self._pop_item_locked(item_id)
            remaining = len(self._pending)
        if item is None:
            return
        event_bus.publish("intercept", {
            "event": "intercept_resolved",
            "id": item_id,
            "decision": decision,
            "remaining": remaining,
            "reason": reason,
        })

    # ---------- API 侧（uvicorn 线程） ----------

    def _pop_item_locked(self, item_id: str) -> Optional[InterceptedItem]:
        for i, item in enumerate(self._pending):
            if item.id == item_id:
                return self._pending.pop(i)
        return None

    def _resolve_from_api(
        self, item_id: str, decision: str, raw: Optional[str] = None
    ) -> dict:
        """API 线程放行/丢弃：找 item → 出队 → 投递 Future 完成 → 广播。"""
        loop = self.proxy_loop
        if loop is None or loop.is_closed():
            return {"ok": False, "remaining": 0, "error": "proxy_not_running"}
        with self._lock:
            item = self._pop_item_locked(item_id)
            if item is None:
                return {"ok": False, "remaining": len(self._pending), "error": "not_found"}
            if item.future.done():
                # 已被超时取消或并发处理，放回队列由 complete 统一清理
                self._pending.append(item)
                return {"ok": False, "remaining": len(self._pending), "error": "already_resolved"}
            remaining = len(self._pending)
        try:
            loop.call_soon_threadsafe(_set_future_result, item.future, decision, raw)
        except RuntimeError:
            return {"ok": False, "remaining": remaining, "error": "proxy_not_running"}
        event_bus.publish("intercept", {
            "event": "intercept_resolved",
            "id": item_id,
            "decision": decision,
            "remaining": remaining,
        })
        return {"ok": True, "remaining": remaining, "error": ""}

    def forward(self, item_id: str, raw: Optional[str] = None) -> dict:
        """放行（可携带编辑后的完整 raw 文本）。"""
        return self._resolve_from_api(item_id, "forward", raw)

    def drop(self, item_id: str) -> dict:
        """丢弃。"""
        return self._resolve_from_api(item_id, "drop", None)

    # ---------- 查询 ----------

    def pending_list(self) -> dict:
        with self._lock:
            items = [
                {
                    "id": i.id,
                    "kind": i.kind,
                    "method": i.method,
                    "url": i.url,
                    "status": i.status,
                    "queued_at": i.queued_at,
                    "raw": i.raw,
                }
                for i in self._pending
            ]
            return {"total": len(items), "items": items}

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    # ---------- 生命周期 ----------

    def reset(self) -> None:
        """代理停止时：取消所有挂起的 Future、清空队列、置空 proxy loop。"""
        with self._lock:
            items = self._pending
            self._pending = []
            loop = self._proxy_loop
            self._proxy_loop = None
        for item in items:
            if loop is not None and not loop.is_closed():
                try:
                    loop.call_soon_threadsafe(self._cancel_future, item.future)
                except RuntimeError:
                    self._cancel_future(item.future)
            else:
                self._cancel_future(item.future)

    @staticmethod
    def _cancel_future(future):
        if not future.done():
            future.cancel()


# 全局单例
intercept_controller = InterceptController()
