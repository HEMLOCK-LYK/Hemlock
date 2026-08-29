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
    # 是否跟随 3xx 重定向。默认 True；跨域重定向时 httpx 会按规范剥离
    # Authorization/Cookie，重定向目标返回 401 会让人误以为是认证失败——
    # 重放器可关闭此项查看原始 30x 响应（Burp 默认不跟随）。
    follow_redirects: bool = True

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
