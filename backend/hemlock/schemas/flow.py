"""流量相关 schema。"""

from datetime import datetime
from pydantic import BaseModel


class FlowResponse(BaseModel):
    id: str
    project_id: str | None = None
    method: str
    url: str
    req_headers: str = ""
    req_body: str = ""
    resp_status: int | None = None
    resp_headers: str = ""
    resp_body: str = ""
    # 拆分字段
    host: str = ""
    scheme: str = ""
    path: str = ""
    resp_length: int | None = None
    mime_type: str = ""
    extension: str = ""
    title: str = ""
    duration_ms: int | None = None  # 请求-响应耗时（毫秒）
    # FlowTag 标签
    tags: str = ""
    tag_colors: str = ""
    # 元数据
    source: str = "proxy"
    timestamp: datetime | None = None
    flow_number: int | None = None
    is_noise: bool = False
    noise_reason: str = ""

    model_config = {"from_attributes": True}


class FlowListItem(BaseModel):
    """流量列表项（精简版，不含完整 body）。"""
    id: str
    method: str
    url: str
    resp_status: int | None = None
    # 拆分字段
    host: str = ""
    scheme: str = ""
    path: str = ""
    resp_length: int | None = None
    mime_type: str = ""
    extension: str = ""
    title: str = ""
    duration_ms: int | None = None  # 请求-响应耗时（毫秒）
    # FlowTag 标签
    tags: str = ""
    tag_colors: str = ""
    # 元数据
    source: str = "proxy"
    timestamp: datetime | None = None
    flow_number: int | None = None
    is_noise: bool = False
    noise_reason: str = ""

    model_config = {"from_attributes": True}


class FlowListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[FlowListItem]
    noise_count: int = 0


class ReplayResponse(BaseModel):
    flow_id: str
    resp_status: int | None = None
    resp_headers: str = ""
    resp_body: str = ""
