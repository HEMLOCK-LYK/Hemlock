"""Project schema。"""

from datetime import datetime
from pydantic import BaseModel, field_validator


class ProjectCreateRequest(BaseModel):
    """创建项目请求。"""
    name: str
    scope: list[str] = []

    @field_validator("scope", mode="before")
    @classmethod
    def _coerce_scope(cls, v):
        if v is None:
            return []
        if isinstance(v, str):
            parts = [s.strip() for s in v.replace("\n", ",").split(",")]
            return [p for p in parts if p]
        if isinstance(v, list):
            return [str(s).strip() for s in v if str(s).strip()]
        return []


class ProjectUpdateRequest(BaseModel):
    """更新项目请求。"""
    name: str | None = None
    scope: list[str] | None = None

    @field_validator("scope", mode="before")
    @classmethod
    def _coerce_scope(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            parts = [s.strip() for s in v.replace("\n", ",").split(",")]
            return [p for p in parts if p]
        if isinstance(v, list):
            return [str(s).strip() for s in v if str(s).strip()]
        return []


class SaveProjectRequest(BaseModel):
    """保存临时项目为正式项目。"""
    name: str


class ProjectResponse(BaseModel):
    """项目响应。"""
    id: str
    name: str
    scope: list[str] = []
    is_temp: bool = False
    created_at: datetime | None = None

    model_config = {"from_attributes": True}

    @field_validator("scope", mode="before")
    @classmethod
    def _parse_scope(cls, v):
        if v is None:
            return []
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            parts = [s.strip() for s in v.split(",")]
            return [p for p in parts if p]
        return []

    @field_validator("is_temp", mode="before")
    @classmethod
    def _parse_is_temp(cls, v):
        """兼容 SQLite 返回的 0/1。"""
        if v is None:
            return False
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return bool(v)
        return v


class ProjectDetailResponse(ProjectResponse):
    """项目详情（含统计信息）。"""
    flow_count: int = 0
    finding_count: int = 0
    task_count: int = 0
