"""当前活跃项目的共享状态。

代理层（mitmproxy addon）在独立线程中运行，
无法直接访问 FastAPI 请求上下文。通过此模块的模块级变量
实现跨线程的当前项目 ID 共享。

活跃项目 ID 会持久化到 ~/.hemlock/active_project.json，
后端重启时自动恢复，避免捕获的流量因 project_id=None 而丢失。
"""

import json
import logging
import threading
from typing import Optional

from hemlock.core.config import get_data_dir

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_current_project_id: Optional[str] = None

# 持久化文件路径
_PERSIST_FILE = get_data_dir() / "active_project.json"


def set_active_project(project_id: Optional[str]) -> None:
    """设置当前活跃项目 ID（用户在前端选择项目时调用）。

    同时将项目 ID 持久化到文件，确保后端重启后可恢复。
    """
    global _current_project_id
    with _lock:
        _current_project_id = project_id
        _persist(project_id)


def get_active_project() -> Optional[str]:
    """获取当前活跃项目 ID（代理捕获流量时调用）。"""
    with _lock:
        return _current_project_id


def restore_active_project() -> Optional[str]:
    """从持久化文件恢复活跃项目 ID（后端启动时调用）。

    仅恢复内存状态，不验证项目是否仍然存在。
    调用方应在恢复后自行验证项目是否存在（临时项目在启动时已被清理）。

    Returns:
        恢复的项目 ID，如果文件不存在或无记录则返回 None。
    """
    global _current_project_id
    with _lock:
        if _PERSIST_FILE.exists():
            try:
                data = json.loads(_PERSIST_FILE.read_text(encoding="utf-8"))
                project_id = data.get("project_id")
                if project_id:
                    _current_project_id = project_id
                    logger.info(f"从持久化文件恢复活跃项目: {project_id}")
                    return project_id
            except Exception as e:
                logger.warning(f"恢复活跃项目失败: {e}")
        _current_project_id = None
        return None


def _persist(project_id: Optional[str]) -> None:
    """将活跃项目 ID 持久化到文件。

    在持有 _lock 的情况下调用。
    """
    try:
        _PERSIST_FILE.parent.mkdir(parents=True, exist_ok=True)
        _PERSIST_FILE.write_text(
            json.dumps({"project_id": project_id}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning(f"持久化活跃项目失败: {e}")
