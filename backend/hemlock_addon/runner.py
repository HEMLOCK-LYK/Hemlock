"""mitmproxy 代理启动封装。"""

import threading
import asyncio
from typing import Optional

from mitmproxy import options
from mitmproxy.tools.dump import DumpMaster

from hemlock.core.config import settings
from hemlock_addon.capture import CaptureAddon


class ProxyRunner:
    """在独立线程中启动 mitmproxy 代理。

    不再接收固定 project_id；CaptureAddon 会动态读取
    active_project 模块中的当前活跃项目 ID。
    """

    def __init__(self):
        self.master: Optional[DumpMaster] = None
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def _start():
            opts = options.Options(
                listen_host=settings.proxy_host,
                listen_port=settings.proxy_port,
            )
            self.master = DumpMaster(opts)
            self.master.addons.add(CaptureAddon())
            await self.master.run()

        try:
            loop.run_until_complete(_start())
        except KeyboardInterrupt:
            pass

    def stop(self) -> None:
        if self.master:
            self.master.shutdown()
        if self.thread:
            self.thread.join(timeout=5)
