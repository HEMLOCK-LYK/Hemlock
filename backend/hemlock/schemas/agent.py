"""Agent & Finding schema。"""

from pydantic import BaseModel
from datetime import datetime


# ===== Agent Task =====

class CreateTaskRequest(BaseModel):
    """创建 Agent 任务。"""
    goal: str = ""
    target_url: str
    project_id: str | None = None
    preset: str = "custom"  # 策略预设 ID


class StepData(BaseModel):
    """单步执行记录。"""
    type: str  # tool_call / thinking / complete / error / confirmation_required
    data: str
    timestamp: str


class TaskResponse(BaseModel):
    """任务详情。"""
    id: str
    goal: str
    status: str
    steps: list[StepData] = []
    started_at: datetime | None = None
    ended_at: datetime | None = None

    class Config:
        from_attributes = True


class TaskSummary(BaseModel):
    """任务摘要（列表用）。"""
    id: str
    goal: str
    status: str
    started_at: datetime | None = None
    ended_at: datetime | None = None

    class Config:
        from_attributes = True


# ===== Finding =====

class EvidenceResponse(BaseModel):
    """证据包。"""
    id: str
    request: str = ""
    response: str = ""
    indicator: str = ""
    ai_reasoning: str = ""
    replayable: bool = True
    created_at: datetime | None = None

    class Config:
        from_attributes = True


class FindingResponse(BaseModel):
    """漏洞记录。"""
    id: str
    project_id: str | None = None
    vuln_type: str
    severity: str = "medium"
    status: str = "pending"
    title: str = ""
    detail: str = ""
    evidence_id: str | None = None
    task_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    class Config:
        from_attributes = True


class FindingDetailResponse(FindingResponse):
    """漏洞详情（含证据链）。"""
    evidence: EvidenceResponse | None = None


class UpdateFindingStatusRequest(BaseModel):
    """更新漏洞状态。"""
    status: str  # pending / confirmed / false_positive / closed


# ===== 复测 =====

class RetestResponse(BaseModel):
    """漏洞复测结果。"""
    finding_id: str
    retest_status: str  # still_vulnerable / fixed / uncertain / error
    original_status: int | None = None
    new_status: int | None = None
    similarity: float = 0.0
    analysis: str = ""
    new_flow_id: str | None = None
    error: str | None = None


# ===== 报告 =====

class ReportRequest(BaseModel):
    """报告生成请求。"""
    project_id: str | None = None
    task_id: str | None = None
    fmt: str = "html"  # html / markdown / json


# ===== 策略 =====

class StrategyResponse(BaseModel):
    """渗透测试策略。"""
    name: str
    vuln_type: str
    owasp_category: str
    description: str
    recon_steps: list[str]
    test_methods: list[str]
    verification: list[str]
    common_payloads: list[str]


class PresetResponse(BaseModel):
    """策略预设。"""
    id: str
    name: str
    description: str
    focus_vuln_types: list[str] = []


# ===== 会话 =====

class SessionInfoResponse(BaseModel):
    """会话信息。"""
    session_id: str
    authenticated: bool = False
    login_url: str = ""
    cookies: dict[str, str] = {}
    headers: dict[str, str] = {}
    csrf_tokens: dict[str, str] = {}
    variables: dict[str, str] = {}
    request_count: int = 0
