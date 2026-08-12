"""拦截控制器跨线程测试。

验证核心难点：addon 在 proxy loop 上挂起 flow，API 线程跨线程完成 Future 后
addon 恢复，以及超时兜底 / reset 清理。
"""

import asyncio
import threading

import pytest
from mitmproxy.test import tutils

from hemlock.services.event_bus import event_bus
from hemlock.services.intercept_controller import InterceptController


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


@pytest.fixture()
def proxy_loop():
    """一个真实运行在独立线程的 asyncio loop（模拟 mitmproxy 线程）。"""
    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    yield loop
    loop.call_soon_threadsafe(loop.stop)
    t.join(timeout=5)
    loop.close()


@pytest.fixture()
def controller(proxy_loop):
    c = InterceptController()
    c.bind_proxy_loop(proxy_loop)
    yield c
    c.reset()


@pytest.fixture()
def events():
    collected = []
    event_bus.subscribe("intercept", collected.append)
    yield collected
    event_bus.unsubscribe("intercept", collected.append)


async def _enqueue(controller, kind, flow):
    return controller.enqueue(kind, flow)


async def _await_item(item):
    return await item.future


def _run(loop, coro):
    return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=5)


def _enqueue_on(loop, controller, kind, flow):
    return _run(loop, _enqueue(controller, kind, flow))


def test_enqueue_and_forward(controller, proxy_loop, events):
    flow = _make_flow()
    item = _enqueue_on(proxy_loop, controller, "request", flow)
    assert item.kind == "request"
    assert item.raw  # 编辑区初值非空
    assert controller.pending_count() == 1

    res = controller.forward(item.id, None)
    assert res["ok"] is True
    assert res["remaining"] == 0

    decision = _run(proxy_loop, _await_item(item))
    assert decision == ("forward", None)
    assert controller.pending_count() == 0

    evs = [e.get("event") for e in events]
    assert "intercept_queued" in evs
    assert "intercept_resolved" in evs


def test_forward_with_edited_raw(controller, proxy_loop):
    flow = _make_flow()
    item = _enqueue_on(proxy_loop, controller, "request", flow)
    edited = "GET /changed HTTP/1.1\nHost: example.com\n\nnewbody"
    res = controller.forward(item.id, edited)
    assert res["ok"] is True
    decision = _run(proxy_loop, _await_item(item))
    assert decision == ("forward", edited)
    # 放行后 addon 会把编辑后的 raw 应用到 flow（controller 只传值，apply 是 addon 的职责）
    from hemlock.services.raw_http import apply_raw_request
    assert apply_raw_request(flow, edited)
    assert flow.request.path == "/changed"
    assert flow.request.get_text() == "newbody"


def test_drop(controller, proxy_loop):
    flow = _make_flow()
    item = _enqueue_on(proxy_loop, controller, "request", flow)
    res = controller.drop(item.id)
    assert res["ok"] is True
    decision = _run(proxy_loop, _await_item(item))
    assert decision == ("drop", None)


def test_forward_unknown_id_not_found(controller, proxy_loop):
    res = controller.forward("int_999")
    assert res["ok"] is False
    assert res["error"] == "not_found"


def test_timeout_complete(controller, proxy_loop, events):
    flow = _make_flow()
    item = _enqueue_on(proxy_loop, controller, "request", flow)

    async def wait():
        try:
            await asyncio.wait_for(item.future, timeout=0.2)
            return "no-timeout"
        except asyncio.TimeoutError:
            return "timeout"

    assert _run(proxy_loop, wait()) == "timeout"
    controller.complete(item.id, "forward", reason="timeout")
    assert controller.pending_count() == 0
    resolved = [e for e in events if e.get("event") == "intercept_resolved"]
    assert resolved and resolved[-1]["reason"] == "timeout"


def test_forward_after_timeout_not_found(controller, proxy_loop):
    flow = _make_flow()
    item = _enqueue_on(proxy_loop, controller, "request", flow)

    async def wait():
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(item.future, timeout=0.2)

    _run(proxy_loop, wait())
    controller.complete(item.id, "forward", reason="timeout")
    res = controller.forward(item.id)
    assert res["ok"] is False
    assert res["error"] == "not_found"


def test_reset_cancels_pending(controller, proxy_loop):
    flow = _make_flow()
    item = _enqueue_on(proxy_loop, controller, "request", flow)
    assert controller.pending_count() == 1
    controller.reset()
    assert controller.pending_count() == 0
    assert controller.proxy_loop is None
    # 等 proxy loop 执行取消回调
    _run(proxy_loop, asyncio.sleep(0.05))
    assert item.future.cancelled()


def test_update_config_broadcasts(events):
    c = InterceptController()
    st = c.update_config(enabled=True, intercept_requests=True)
    assert st["enabled"] and st["intercept_requests"]
    assert any(e.get("event") == "intercept_status" for e in events)


def test_should_intercept_skips_static_noise():
    c = InterceptController()
    c.enabled = True
    c.intercept_requests = True
    flow = _make_flow()
    flow.request.url = "https://cdn.example.com/app.js"
    assert c.should_intercept("request", flow) is False
    flow.request.url = "https://app.example.com/api/login"
    assert c.should_intercept("request", flow) is True
    # 子开关关闭时不拦
    c.intercept_requests = False
    assert c.should_intercept("request", flow) is False
