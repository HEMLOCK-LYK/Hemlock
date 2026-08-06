"""Task 模型：Agent 任务执行轨迹。"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from hemlock.core.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return f"tsk_{uuid.uuid4().hex[:24]}"


# 任务状态
TASK_STATUS_IDLE = "idle"  # 待开始 / 空闲（对话已创建，等待用户发消息）
TASK_STATUS_RUNNING = "running"
TASK_STATUS_PAUSED = "paused"  # 等待人工确认
TASK_STATUS_DONE = "done"
TASK_STATUS_FAILED = "failed"


class Task(Base):
    __tablename__ = "tasks"

    id = Column(String, primary_key=True, default=_uuid)
    project_id = Column(String, ForeignKey("projects.id"), nullable=True, index=True)

    goal = Column(Text, default="")  # 任务目标描述
    status = Column(String(32), default=TASK_STATUS_RUNNING, index=True)
    steps = Column(Text, default="[]")  # JSON 数组，记录每步轨迹
    # checkpoint 支持（对齐 Claude Code 的会话持久化）：
    messages = Column(Text, default="[]")  # JSON 数组，当前 LLM 消息历史（周期保存，供恢复）
    checkpoint = Column(Text, default="{}")  # JSON，PentestContext + 步数快照（供恢复）

    started_at = Column(DateTime(timezone=True), default=_utcnow)
    ended_at = Column(DateTime(timezone=True), nullable=True)

    project = relationship("Project", back_populates="tasks")
    findings = relationship("Finding", back_populates="task")
