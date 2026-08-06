"""Flow 模型：HTTP 流量记录。"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Integer, DateTime, Text, ForeignKey, Index, Boolean
from sqlalchemy.orm import relationship

from hemlock.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return f"flw_{uuid.uuid4().hex[:24]}"


class Flow(Base):
    __tablename__ = "flows"

    id = Column(String, primary_key=True, default=_uuid)
    project_id = Column(String, ForeignKey("projects.id"), nullable=True, index=True)

    # 请求
    method = Column(String(16), nullable=False)
    url = Column(Text, nullable=False)
    req_headers = Column(Text, default="")
    req_body = Column(Text, default="")

    # 响应
    resp_status = Column(Integer, nullable=True)
    resp_headers = Column(Text, default="")
    resp_body = Column(Text, default="")

    # 拆分字段（从 URL 解析，便于列表展示）
    host = Column(String(255), default="")
    scheme = Column(String(10), default="")       # http / https
    path = Column(Text, default="")                # URL path（含 query）

    # 响应元数据
    resp_length = Column(Integer, nullable=True)   # 响应体字节长度
    mime_type = Column(String(100), default="")    # Content-Type 简化值
    extension = Column(String(20), default="")     # URL 文件扩展名
    title = Column(String(500), default="")        # HTML <title>
    duration_ms = Column(Integer, nullable=True)   # 请求-响应耗时（毫秒），用于时间盲注等检测

    # FlowTag 标签：正则匹配命中的规则名称，逗号分隔（如 "Shiro,JWT"）
    tags = Column(String(500), default="")
    tag_colors = Column(String(200), default="")   # 标签颜色，逗号分隔（与 tags 一一对应）

    # 元数据
    source = Column(String(32), default="proxy")  # proxy / agent / repeater / intruder
    timestamp = Column(DateTime(timezone=True), default=_utcnow, index=True)

    # 杂包标记：True 表示心跳包/静态资源/高频杂包
    is_noise = Column(Boolean, default=False, index=True)
    noise_reason = Column(String(64), default="")

    project = relationship("Project", back_populates="flows")

    __table_args__ = (
        Index("ix_flows_url", "url"),
        Index("ix_flows_source", "source"),
        Index("ix_flows_host", "host"),
    )
