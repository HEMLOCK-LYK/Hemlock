"""MCP Server 模型：外部 MCP 服务器连接配置。"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Text, DateTime, Boolean, Integer
from hemlock.core.database import Base

def _utcnow():
    return datetime.now(timezone.utc)

def _uuid():
    return f"mcp_{uuid.uuid4().hex[:24]}"

class McpServer(Base):
    __tablename__ = "mcp_servers"
    id = Column(String, primary_key=True, default=_uuid)
    name = Column(String(128), unique=True, nullable=False)  # 服务器名称
    transport = Column(String(32), default="stdio")  # 传输方式：stdio / http
    command = Column(String(512), default="")  # stdio 模式下的启动命令，如 "npx"
    args = Column(Text, default="[]")  # stdio 模式参数，JSON 数组
    env = Column(Text, default="{}")  # 环境变量，JSON 对象
    url = Column(String(512), default="")  # http 模式的 URL
    enabled = Column(Boolean, default=False)  # 是否启用
    status = Column(String(32), default="disconnected")  # disconnected / connecting / connected / error
    last_error = Column(Text, default="")
    tool_count = Column(Integer, default=0)  # 已发现的工具数量
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
