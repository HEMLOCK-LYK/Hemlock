"""Finding 模型：漏洞记录。"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from hemlock.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return f"fnd_{uuid.uuid4().hex[:24]}"


# 漏洞状态枚举
FINDING_STATUS_PENDING = "pending"
FINDING_STATUS_CONFIRMED = "confirmed"
FINDING_STATUS_FALSE_POSITIVE = "false_positive"
FINDING_STATUS_CLOSED = "closed"


class Finding(Base):
    __tablename__ = "findings"

    id = Column(String, primary_key=True, default=_uuid)
    project_id = Column(String, ForeignKey("projects.id"), nullable=True, index=True)

    vuln_type = Column(String(64), nullable=False)  # SQL_INJECTION / XSS / ...
    severity = Column(String(16), default="medium")  # critical / high / medium / low / info
    status = Column(String(32), default=FINDING_STATUS_PENDING, index=True)

    title = Column(String(512), default="")
    detail = Column(Text, default="")

    evidence_id = Column(String, ForeignKey("evidences.id"), nullable=True)
    task_id = Column(String, ForeignKey("tasks.id"), nullable=True)

    created_at = Column(DateTime(timezone=True), default=_utcnow, index=True)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    project = relationship("Project", back_populates="findings")
    evidence = relationship("Evidence")
    task = relationship("Task", back_populates="findings")
