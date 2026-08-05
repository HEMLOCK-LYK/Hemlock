"""WebSocket 实时事件推送。"""

import json
import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from hemlock.services.event_bus import event_bus

router = APIRouter(tags=["ws"])


@router.websocket("/ws/events")
async def events_ws(websocket: WebSocket):
    """推送实时事件：新流量、Agent 进度、漏洞更新。

    关键：event_bus.publish 可能从 asyncio.to_thread 的线程池中调用
    （如 LLM streaming 回调），asyncio.Queue 不是线程安全的，
    必须使用 loop.call_soon_threadsafe 跨线程安全投递。
    """
    await websocket.accept()

    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def on_event(data):
        """事件回调：从任意线程安全投递到事件循环。

        使用 call_soon_threadsafe 确保 asyncio.Queue 的线程安全操作。
        """
        try:
            loop.call_soon_threadsafe(queue.put_nowait, data)
        except Exception:
            pass

    # 订阅所有频道
    for channel in ["flow", "agent", "finding", "intruder"]:
        event_bus.subscribe(channel, on_event)

    try:
        await websocket.send_json({"event": "connected", "message": "Hemlock events connected"})

        while True:
            try:
                data = await asyncio.wait_for(queue.get(), timeout=30.0)
                await websocket.send_json(data)
            except asyncio.TimeoutError:
                # 心跳
                await websocket.send_json({"event": "ping"})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        for channel in ["flow", "agent", "finding", "intruder"]:
            event_bus.unsubscribe(channel, on_event)
