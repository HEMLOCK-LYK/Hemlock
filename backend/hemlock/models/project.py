"""Project 模型：渗透测试项目。"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, DateTime, Text, Boolean
from sqlalchemy.orm import relationship

from hemlock.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return f"prj_{uuid.uuid4().hex[:24]}"


class Project(Base):
    __tablename__ = "projects"

    id = Column(String, primary_key=True, default=_uuid)
    name = Column(String(256), nullable=False)
    # 授权目标范围，逗号分隔的域名/通配符（仅作信息记录，不强制校验）
    scope = Column(Text, default="")
    # 是否为临时项目（用户点击"立即开始"创建，退出时如不保存则自动删除）
    is_temp = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow)

    flows = relationship("Flow", back_populates="project", cascade="all, delete-orphan")
    findings = relationship("Finding", back_populates="project", cascade="all, delete-orphan")
    tasks = relationship("Task", back_populates="project", cascade="all, delete-orphan")
