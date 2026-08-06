"""Hemlock 后端入口脚本（PyInstaller 打包用）。

命令行参数：
    --host HOST    监听地址（默认 127.0.0.1）
    --port PORT    监听端口（默认 8000）
"""

import argparse
import sys
import os


def _ensure_stdio():
    """PyInstaller 无控制台模式下 sys.stdout/stderr 可能为 None，需要修复。"""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def main():
    _ensure_stdio()

    parser = argparse.ArgumentParser(description="Hemlock Backend Server")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=9876, help="监听端口")
    args = parser.parse_args()

    # 数据目录由 config.get_data_dir() 统一管理（支持 HEMLOCK_DATA_DIR 环境变量）
    # 无需在此硬编码数据库路径

    # 导入并启动 uvicorn
    import uvicorn
    from hemlock.main import app

    print(f"[Hemlock] Starting backend on {args.host}:{args.port}")
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
