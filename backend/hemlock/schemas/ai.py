"""AI schema。"""

from pydantic import BaseModel


class AnalyzeRequest(BaseModel):
    flow_id: str
    # 可选：自定义分析问题
    question: str | None = None


class AnalyzeResponse(BaseModel):
    flow_id: str
    analysis: str
    risk_level: str = "info"  # critical / high / medium / low / info
    suggestions: list[str] = []


class PayloadRequest(BaseModel):
    """生成 payload 字典请求。"""
    scene: str  # weak_password / sqli / xss / fuzz / waf_bypass
    context: str = ""  # 目标上下文（技术栈、参数等）
    count: int = 50
    # 完整请求上下文（让 AI 生成针对性 payload）
    method: str = ""
    url: str = ""
    headers: str = ""
    body: str = ""


class PayloadResponse(BaseModel):
    scene: str
    payloads: list[str]
