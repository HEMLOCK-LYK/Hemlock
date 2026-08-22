"""Hemlock 后端入口脚本（PyInstaller 打包用）。

命令行参数：
    --host HOST    监听地址（默认 127.0.0.1）
    --port PORT    监听端口（默认 9876）
"""

import argparse
import logging
import os
import subprocess
import sys
import time

logger = logging.getLogger(__name__)


def _ensure_stdio():
    """PyInstaller 无控制台模式下 sys.stdout/stderr 可能为 None，需要修复。"""
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def _find_pids_on_ports(ports: set[int]) -> set[int]:
    """通过 netstat 找到指定端口上处于 LISTENING 状态的进程 PID。

    用系统自带 netstat，避免引入 psutil 等额外依赖（PyInstaller 打包体积/兼容）。
    """
    pids: set[int] = set()
    try:
        out = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=10
        ).stdout
    except Exception:
        return pids
    for line in out.splitlines():
        parts = line.split()
        # 形如: TCP  127.0.0.1:9876  0.0.0.0:0  LISTENING  11008
        if len(parts) >= 5 and parts[0] == "TCP" and parts[3] == "LISTENING":
            _, _, port = parts[1].rpartition(":")
            if port.isdigit() and int(port) in ports:
                pid = parts[4]
                if pid.isdigit():
                    pids.add(int(pid))
    return pids


def _process_name(pid: int) -> str:
    """查询 PID 对应的进程名（如 hemlock-backend.exe），失败返回空串。"""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        if not out:
            return ""
        return out.splitlines()[0].split('","')[0].strip('"').lower()
    except Exception:
        return ""


def _takeover_stale_hemlock(ports: list[int]) -> None:
    """启动前端口接管：若 API/代理端口被旧版 Hemlock 实例占用，自动终止后由本实例接管。

    场景：用户旧版本还在运行就安装了新版本，旧实例占着 9876/8080，
    导致新实例后端起不来、代理打不开。这里只杀进程名匹配 hemlock 的进程，
    避免误杀无关程序（如别的占用 8080 的代理软件）。
    """
    port_set = set(ports)
    for pid in _find_pids_on_ports(port_set):
        if pid == os.getpid():
            continue
        name = _process_name(pid)
        if "hemlock" not in name:
            continue
        logger.warning(
            f"检测到旧 Hemlock 实例占用端口 {sorted(port_set)}（PID {pid}，进程 {name}），"
            f"自动终止后由新版本接管"
        )
        try:
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True, text=True, timeout=10,
            )
        except Exception as e:
            logger.warning(f"终止旧实例失败: {e}")
    # 等待端口释放，避免 uvicorn 绑定失败
    for _ in range(10):
        if not _find_pids_on_ports(port_set):
            return
        time.sleep(0.5)


def main():
    _ensure_stdio()

    parser = argparse.ArgumentParser(description="Hemlock Backend Server")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=9876, help="监听端口")
    args = parser.parse_args()

    # 数据目录由 config.get_data_dir() 统一管理（支持 HEMLOCK_DATA_DIR 环境变量）
    # 无需在此硬编码数据库路径

    # 端口接管：旧版本未退出时自动终止，避免端口占用导致后端/代理打不开
    try:
        from hemlock.core.config import settings
        _takeover_stale_hemlock([args.port, settings.proxy_port])
    except Exception as e:
        logger.warning(f"端口接管检查跳过: {e}")

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
