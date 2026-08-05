"""Intruder schema。"""

from pydantic import BaseModel


class IntruderPosition(BaseModel):
    """标记位置。"""
    # 在请求中的占位符，如 §FUZZ§
    placeholder: str
    # 替换用的 payload 列表
    payloads: list[str]


class IntruderConfig(BaseModel):
    method: str = "GET"
    url: str
    headers: str = ""
    body: str = ""
    positions: list[IntruderPosition] = []
    concurrency: int = 5
    project_id: str | None = None
    attack_type: str = "sniper"


class IntruderResultItem(BaseModel):
    payload: str
    status: int | None = None
    length: int = 0
    flow_id: str | None = None
    duration_ms: int | None = None
    tags: str = ""
    tag_colors: str = ""


class IntruderResult(BaseModel):
    task_id: str
    total: int
    results: list[IntruderResultItem]
