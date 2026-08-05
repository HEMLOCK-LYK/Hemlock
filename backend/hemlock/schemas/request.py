"""请求 schema。"""

from typing import Union
from pydantic import BaseModel, field_validator


class SendRequestRequest(BaseModel):
    method: str = "GET"
    url: str
    # headers 同时接受字符串（"Key: Value\nKey2: Value2"）和字典（{"Key": "Value"}）
    # 修复 BUG-04: Agent call_api 以 JSON body 调用时 headers 为 dict 导致 422
    headers: Union[str, dict] = ""
    body: str = ""
    project_id: str | None = None

    @field_validator("headers", mode="before")
    @classmethod
    def normalize_headers(cls, v):
        """将 dict 格式的 headers 转为换行分隔的字符串，与 send_raw_request 预期一致。"""
        if v is None:
            return ""
        if isinstance(v, dict):
            return "\n".join(f"{k}: {val}" for k, val in v.items())
        return str(v)


class SendRequestResponse(BaseModel):
    flow_id: str
    status: int | None = None
    headers: str = ""
    body: str = ""
    duration_ms: int | None = None
