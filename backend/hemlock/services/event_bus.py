"""进程内事件总线，用于 WebSocket 实时推送。"""

import asyncio
import json
from collections import defaultdict
from typing import Callable, Any


class EventBus:
    """简单的进程内 pub/sub 事件总线。"""

    def __init__(self):
        self._subscribers: dict[str, list[Callable]] = defaultdict(list)
        self._lock = asyncio.Lock()

    def subscribe(self, channel: str, callback: Callable) -> None:
        self._subscribers[channel].append(callback)

    def unsubscribe(self, channel: str, callback: Callable) -> None:
        if callback in self._subscribers[channel]:
            self._subscribers[channel].remove(callback)

    def publish(self, channel: str, data: Any) -> None:
        for cb in self._subscribers.get(channel, []):
            try:
                cb(data)
            except Exception:
                pass


# 全局事件总线实例
event_bus = EventBus()
