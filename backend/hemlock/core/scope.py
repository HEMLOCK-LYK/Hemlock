"""目标范围校验。"""

from urllib.parse import urlparse
from fnmatch import fnmatch
from typing import Optional

from hemlock.core.config import settings


def check_scope(url: str, scope_patterns: Optional[list[str]] = None) -> bool:
    """检查 URL 是否在授权目标范围内。

    如果未配置 target_scope，则允许所有目标。
    可传入 scope_patterns 覆盖配置（用于测试与 Agent 调用）。
    """
    if scope_patterns is None:
        scope_patterns = settings.scope_list

    if not scope_patterns:
        return True

    host = urlparse(url).hostname or ""
    for pattern in scope_patterns:
        if fnmatch(host.lower(), pattern.lower()):
            return True
    return False
