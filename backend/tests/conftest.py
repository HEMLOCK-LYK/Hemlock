"""pytest 配置：确保 backend 包在 sys.path 中。"""

import sys
from pathlib import Path

# 把 backend 目录加入 sys.path
backend_dir = Path(__file__).parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

# 确保所有模型被导入并注册到 Base.metadata
from hemlock.models import project, flow, finding, evidence, task  # noqa: E402,F401
