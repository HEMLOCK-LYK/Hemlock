"""Evidence 模型：漏洞证据包。"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Text, Boolean, DateTime

from hemlock.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return f"evd_{uuid.uuid4().hex[:24]}"


class Evidence(Base):
    __tablename__ = "evidences"

    id = Column(String, primary_key=True, default=_uuid)
    # 原始请求响应快照
    request = Column(Text, default="")
    response = Column(Text, default="")
    # 判定指标（为什么认为是漏洞）
    indicator = Column(Text, default="")
    ai_reasoning = Column(Text, default="")
    replayable = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
