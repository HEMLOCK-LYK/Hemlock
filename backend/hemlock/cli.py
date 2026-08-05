"""Hemlock CLI：一键启动后端服务。

代理不再自动启动，由用户通过 API/UI 手动控制。
使用 --with-proxy 可强制随启动开启代理。
"""

import sys

import uvicorn

from hemlock.core.config import settings
from hemlock.core.database import init_db


def main():
    """启动 Hemlock 后端。"""
    print(f"[Hemlock] 初始化数据库: {settings.db_path}")
    init_db()

    # 代理默认不自动启动（用户通过 UI 手动控制）
    # 使用 --with-proxy 参数可随启动自动开启代理
    auto_start_proxy = "--with-proxy" in sys.argv
    if auto_start_proxy:
        try:
            from hemlock.core.proxy_controller import proxy_controller
            success, msg = proxy_controller.start()
            print(f"[Hemlock] {msg}")
        except Exception as e:
            print(f"[Hemlock] 代理启动失败: {e}")

    # 启动 FastAPI
    print(f"[Hemlock] API 服务启动: http://{settings.api_host}:{settings.api_port}")
    print(f"[Hemlock] API 文档: http://{settings.api_host}:{settings.api_port}/docs")
    print(f"[Hemlock] 代理监听: {settings.proxy_host}:{settings.proxy_port} (需手动启动)")
    try:
        uvicorn.run(
            "hemlock.main:app",
            host=settings.api_host,
            port=settings.api_port,
            reload=False,
            log_level="info",
        )
    except KeyboardInterrupt:
        print("\n[Hemlock] 正在关闭...")
    finally:
        # 确保代理被关闭
        try:
            from hemlock.core.proxy_controller import proxy_controller
            proxy_controller.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()
